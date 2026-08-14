from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import pathlib
import stat
from collections.abc import Callable

import duckdb
import pytest
import urllib3
from minio.error import S3Error

import run

from .helpers import full_args


@pytest.mark.parametrize("value", ["0", "-1", "-100"])
def test_positive_int_rejects_non_positive(value: str) -> None:
    """0 以下の値で argparse 引数エラーになることを確認する。"""
    with pytest.raises(argparse.ArgumentTypeError, match="value must be >= 1"):
        run.positive_int(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", 1), ("100", 100), ("999999", 999999)],
)
def test_positive_int_accepts_positive(value: str, expected: int) -> None:
    """正の整数文字列を int に変換して返すことを確認する。"""
    assert run.positive_int(value) == expected


def test_build_parser_s3_use_ssl_defaults_to_true() -> None:
    """--s3_use_ssl のデフォルトが True であることを確認する。

    認証情報の平文送信を防ぐため、 SSL 利用は明示的に無効化しない限り有効にする。
    """
    parser = run.build_parser()
    assert parser.parse_args(["init"]).s3_use_ssl is True
    assert parser.parse_args(["--no-s3_use_ssl", "init"]).s3_use_ssl is False
    assert parser.parse_args(["--s3_use_ssl", "init"]).s3_use_ssl is True


def test_args_defaults_match_build_parser_defaults() -> None:
    """Args のデフォルト値が build_parser のデフォルト値と一致することを確認する。

    main は parse_args(namespace=Args()) で注入するため、 コマンドライン未指定時に
    dataclass 側のデフォルトが優先される。 これが build_parser 側のデフォルト
    (--help 表示値) と乖離すると、 実挙動と表示が食い違う。 乖離の再発を防ぐために
    全フィールドを突き合わせる。
    """
    parser = run.build_parser()
    parsed = parser.parse_args(["init"])
    args = run.Args()
    for field_name, field_value in vars(args).items():
        if field_name == "func":
            # func は set_defaults が注入するため、デフォルト値の突き合わせ対象外
            assert getattr(parsed, field_name) is run.init
            continue
        assert getattr(parsed, field_name) == field_value, (
            f"Args.{field_name} のデフォルト {field_value!r} が "
            f"build_parser のデフォルト {getattr(parsed, field_name)!r} と一致しません"
        )
    # build_parser に追加された引数が Args にフィールドとして存在しないと、
    # main の parse_args(namespace=Args()) 経由で setattr され型チェッカが
    # 追えないため、 逆方向の乖離も検出する (help は argparse が自動追加する)。
    parser_field_names = {
        action.dest
        for action in parser._actions
        if action.dest not in (argparse.SUPPRESS, "help")
    }
    assert parser_field_names - set(vars(args)) == set(), (
        f"build_parser の引数が Args にありません: {sorted(parser_field_names - set(vars(args)))}"
    )


def test_parse_args_injects_args_via_namespace() -> None:
    """parse_args(namespace=Args()) の注入経路を確認する。

    main はこの呼び出し形で CLI 引数を注入する。 この経路ではコマンドライン
    未指定時に argparse のデフォルトではなく Args 側のデフォルトが適用される。
    set_defaults による func の注入と、 注入された Args がコマンドラインの値を
    反映することを検証して回帰を防ぐ。
    """
    parser = run.build_parser()
    args = parser.parse_args(["--db", "custom.db", "init"], namespace=run.Args())
    assert args.func is run.init
    assert args.db == "custom.db"
    # コマンドライン未指定のフィールドは Args のデフォルトが残る
    assert args.retention_period == run.Args().retention_period


def test_delete_returns_without_copy_when_no_rows_deleted(
    tmp_path: pathlib.Path,
) -> None:
    """削除件数が 0 件の場合に DB コピー処理 (ATTACH + COPY + shutil.move) へ進まないことを確認する。

    「元 DB のバイト内容そのものが不変」 は DuckDB が R/W 接続を開いた時点で WAL ヘッダー等を
    更新する可能性があるため保証しない (バージョン差で変わり得る)。 仕様は「COPY 経路が走って
    いない」 で、 inode 不変 + `.copy` 不在で担保する。
    """
    db_path = tmp_path / "delete_no_rows.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")

    before_stat = db_path.stat()

    args = full_args(db=str(db_path), retention_period=1)
    run.delete(args)

    # DB ファイルが shutil.move で置き換わっていないこと (inode 不変)、COPY 用一時ファイルが
    # 作られていないことを確認する。
    assert db_path.exists()
    assert db_path.stat().st_ino == before_stat.st_ino
    assert os.path.exists(f"{db_path}.copy") is False


def test_delete_handles_single_quote_in_db_path(tmp_path: pathlib.Path) -> None:
    """シングルクォートを含む DB ファイルパスでも ATTACH 文が成立し、delete が完走することを確認する。

    ensure_safe_sql_string_literal によるエスケープが実際の ATTACH 文で有効であることを、
    実 DuckDB を相手にしたファイル操作経路で検証する。
    """
    # ファイル名にシングルクォートを含める。エスケープが効いていなければ ATTACH 文が
    # 構文エラーになるか、別のパスを参照してしまう。
    db_path = tmp_path / "test'delete.db"

    # retention_period=1 で削除対象となるよう、2 日前の timestamp を持つ行を挿入する。
    old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")
        con.execute("INSERT INTO rtc_stats VALUES (?)", (old_timestamp,))

    args = full_args(db=str(db_path), retention_period=1)
    run.delete(args)

    # delete 後、古い行が削除されていることを確認する。
    # ATTACH/COPY 経路が破綻していればここまで到達せず、データも消えない。
    with duckdb.connect(str(db_path)) as con:
        result = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()
        assert result is not None
        assert result[0] == 0

    # COPY 用の一時ファイルが残っていないことを確認する (ATTACH/COPY/move が完了している)。
    assert os.path.exists(f"{db_path}.copy") is False
    assert os.path.exists(f"{db_path}.copy.wal") is False


def test_delete_restricts_db_file_permission(tmp_path: pathlib.Path) -> None:
    """delete が生成する DB ファイルのパーミッションが owner と group のみに縮小されることを確認する。

    chmod 対象は COPY 先の copy ファイルだが、 shutil.move (rename) 経由で最終的な args.db の
    パーミッションが 0o660 になる (rename は元 args.db のパーミッションを引き継がず copy_file
    側で置換する)。 owner の読み書きと group の読み書きのみ残り、 other から全権限が落ちる
    ことを保証する。
    """
    db_path = tmp_path / "delete_permission.db"

    # retention_period=1 で削除対象となるよう、2 日前の timestamp を持つ行を挿入する。
    old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")
        con.execute("INSERT INTO rtc_stats VALUES (?)", (old_timestamp,))

    args = full_args(db=str(db_path), retention_period=1)
    run.delete(args)

    # owner: rw, group: rw, other: なし
    expected_mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
    actual_mode = stat.S_IMODE(db_path.stat().st_mode)
    assert actual_mode == expected_mode


def test_delete_removes_stale_copy_files_before_start(tmp_path: pathlib.Path) -> None:
    """前回異常終了で残った .copy / .copy.wal があっても delete が完走し、 残骸が消えることを確認する。

    冒頭の remove_delete_incomplete_copy_files で残骸を掃除してから ATTACH/COPY に
    入る挙動を担保する。 残骸を放置すると ATTACH '{copy_file}' AS copy が既存ファイルを
    開いてしまい、 COPY FROM DATABASE で古いスキーマと新本体データが混ざる可能性がある。
    """
    db_path = tmp_path / "delete_with_stale.db"

    # retention_period=1 で削除対象となるよう、2 日前の timestamp を持つ行を挿入する。
    old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")
        con.execute("INSERT INTO rtc_stats VALUES (?)", (old_timestamp,))

    # 前回 delete が SIGKILL 等で異常終了した状況を再現する: .copy と .copy.wal を任意の
    # 内容で配置する。
    stale_copy = tmp_path / "delete_with_stale.db.copy"
    stale_wal = tmp_path / "delete_with_stale.db.copy.wal"
    stale_copy.write_bytes(b"stale copy payload")
    stale_wal.write_bytes(b"stale wal payload")

    args = full_args(db=str(db_path), retention_period=1)
    run.delete(args)

    # 残骸が掃除され、delete が完走している (2 日前の行が削除されている) ことを確認する。
    with duckdb.connect(str(db_path)) as con:
        result = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()
        assert result is not None
        assert result[0] == 0

    # .copy と .copy.wal が残っていないことを確認する。
    assert os.path.exists(f"{db_path}.copy") is False
    assert os.path.exists(f"{db_path}.copy.wal") is False


# should_create_readonly


def test_should_create_readonly_returns_false_when_stat_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """initial_stat と現在の (mtime_ns, size) が一致するとき False を返すことを確認する。"""
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    stat_result = db_path.stat()
    initial_stat = (stat_result.st_mtime_ns, stat_result.st_size)
    assert run.should_create_readonly(str(db_path), initial_stat) is False


def test_should_create_readonly_returns_true_when_mtime_changed(
    tmp_path: pathlib.Path,
) -> None:
    """initial_stat の mtime_ns と現在の mtime_ns が異なるとき True を返すことを確認する。"""
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    initial = db_path.stat()
    initial_stat = (initial.st_mtime_ns, initial.st_size)
    # mtime を 100 秒先の未来値 (ナノ秒 = 100 * 10^9) に更新して initial と差をつける。
    future_ns = initial.st_mtime_ns + 100 * 10**9
    os.utime(db_path, ns=(future_ns, future_ns))
    assert run.should_create_readonly(str(db_path), initial_stat) is True


def test_should_create_readonly_returns_true_when_size_changed(
    tmp_path: pathlib.Path,
) -> None:
    """initial_stat の mtime_ns と現在の mtime_ns が同じでも、 st_size が異なれば True を返すことを確認する。

    mtime が秒粒度に丸められる FS で同一秒内に書き込みが完了して mtime が変化しないケースを、
    st_size の変化で検出できることを担保する。
    """
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    initial = db_path.stat()
    initial_stat = (initial.st_mtime_ns, initial.st_size)
    # ファイル内容を書き換えてサイズを変えつつ、mtime_ns は initial と同じ値に戻す。
    db_path.write_bytes(b"payload-longer")
    os.utime(db_path, ns=(initial.st_mtime_ns, initial.st_mtime_ns))
    assert run.should_create_readonly(str(db_path), initial_stat) is True


def test_should_create_readonly_returns_false_when_db_missing_after_run(
    tmp_path: pathlib.Path,
) -> None:
    """args.func 実行後に args.db が消失したケース (現在ファイル不在) は False を返すことを確認する。"""
    missing = tmp_path / "missing.db"
    # initial_stat があっても現在ファイルが無ければ False。
    assert run.should_create_readonly(str(missing), (0, 0)) is False
    # initial_stat が None (起動時から不在) でも False。
    assert run.should_create_readonly(str(missing), None) is False


# capture_db_stat


def test_capture_db_stat_returns_none_when_db_missing(tmp_path: pathlib.Path) -> None:
    """DB ファイルが存在しないとき None を返すことを確認する。"""
    missing = tmp_path / "missing.db"
    assert run.capture_db_stat(str(missing)) is None


def test_capture_db_stat_returns_mtime_ns_and_size(tmp_path: pathlib.Path) -> None:
    """DB ファイルが存在するとき (mtime_ns, size) タプルを返し、 st_mtime_ns と st_size と一致することを確認する。"""
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    stat_result = db_path.stat()
    assert run.capture_db_stat(str(db_path)) == (
        stat_result.st_mtime_ns,
        stat_result.st_size,
    )


# create_readonly_copy


def test_create_readonly_copy_generates_readonly_with_restricted_permission(
    tmp_path: pathlib.Path,
) -> None:
    """create_readonly_copy が .readonly を生成し、パーミッションを 0o660 に揃えることを確認する。"""
    db_path = tmp_path / "source.db"
    db_path.write_bytes(b"duckdb-file-payload")
    # 元の DB を過剰権限にしておき、.readonly 側が必ず縮小されることを示せるようにする。
    os.chmod(db_path, 0o666)

    run.create_readonly_copy(str(db_path))

    readonly_path = tmp_path / "source.db.readonly"
    assert readonly_path.exists()
    # 内容が元 DB と一致すること
    assert readonly_path.read_bytes() == db_path.read_bytes()
    # owner: rw, group: rw, other: なし
    expected_mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
    assert stat.S_IMODE(readonly_path.stat().st_mode) == expected_mode


def test_create_readonly_copy_overwrites_existing_readonly(
    tmp_path: pathlib.Path,
) -> None:
    """既に .readonly が存在しても、 最新の DB 内容で上書きされ、 パーミッションが 0o660 に縮小されることを確認する。"""
    db_path = tmp_path / "source.db"
    db_path.write_bytes(b"new-payload")

    readonly_path = tmp_path / "source.db.readonly"
    # 既存の .readonly を別内容 + 過剰権限で配置しておく
    readonly_path.write_bytes(b"stale-payload")
    os.chmod(readonly_path, 0o666)

    run.create_readonly_copy(str(db_path))

    assert readonly_path.read_bytes() == b"new-payload"
    # owner: rw, group: rw, other: なし
    expected_mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
    assert stat.S_IMODE(readonly_path.stat().st_mode) == expected_mode


def test_create_readonly_copy_does_not_leave_tmp_file(tmp_path: pathlib.Path) -> None:
    """create_readonly_copy が一時ファイル .tmp を後始末することを確認する。"""
    db_path = tmp_path / "source.db"
    db_path.write_bytes(b"payload")

    run.create_readonly_copy(str(db_path))

    tmp_file = tmp_path / "source.db.tmp"
    assert not tmp_file.exists()


# require_s3_credentials


def test_require_s3_credentials_rejects_missing_access_key() -> None:
    """access_key が未指定の場合に CliUsageError を送出することを確認する。"""
    args = run.Args(s3_access_key_id=None, s3_secret_access_key="secret")
    with pytest.raises(run.CliUsageError, match="S3 credentials are required"):
        run.require_s3_credentials(args)


def test_require_s3_credentials_rejects_missing_secret() -> None:
    """secret が未指定の場合に CliUsageError を送出することを確認する。"""
    args = run.Args(s3_access_key_id="access", s3_secret_access_key=None)
    with pytest.raises(run.CliUsageError, match="S3 credentials are required"):
        run.require_s3_credentials(args)


def test_require_s3_credentials_accepts_valid_credentials() -> None:
    """両方の値が指定されている場合は例外を送出しないことを確認する。"""
    args = run.Args(s3_access_key_id="access", s3_secret_access_key="secret")
    assert run.require_s3_credentials(args) is None


# ensure_safe_sql_string_literal


def test_ensure_safe_sql_string_literal_doubles_single_quote() -> None:
    """シングルクォートが 1 個含まれる場合に 2 個に変換することを確認する。"""
    assert run.ensure_safe_sql_string_literal("a'b") == "a''b"


def test_ensure_safe_sql_string_literal_handles_multiple_quotes() -> None:
    """複数のシングルクォートをすべて二重化することを確認する。"""
    assert run.ensure_safe_sql_string_literal("'a'b'c'") == "''a''b''c''"


def test_ensure_safe_sql_string_literal_passes_through_safe_string() -> None:
    """シングルクォートを含まない文字列はそのまま返すことを確認する。"""
    assert (
        run.ensure_safe_sql_string_literal("/var/lib/kohaku/duck.db")
        == "/var/lib/kohaku/duck.db"
    )


def test_ensure_safe_sql_string_literal_handles_empty_string() -> None:
    """空文字列を渡しても例外なく空文字列を返すことを確認する。"""
    assert run.ensure_safe_sql_string_literal("") == ""


def test_ensure_safe_sql_string_literal_neutralizes_injection_payload() -> None:
    """SQL インジェクション風の payload も単純なエスケープで無害化されることを確認する。"""
    payload = "'; DROP TABLE x; --"
    expected = "''; DROP TABLE x; --"
    assert run.ensure_safe_sql_string_literal(payload) == expected


@pytest.mark.parametrize(
    "control_char",
    [
        "\x00",  # NUL
        "\n",  # LF
        "\r",  # CR
        "\t",  # TAB
        "\x1f",  # 制御文字の上限
        "\x7f",  # DEL
    ],
)
def test_ensure_safe_sql_string_literal_rejects_control_characters(
    control_char: str,
) -> None:
    """制御文字を含む値は CliUsageError で拒否されることを確認する。"""
    value = f"/var/lib/kohaku/duck{control_char}.db"
    with pytest.raises(run.CliUsageError, match="control characters"):
        run.ensure_safe_sql_string_literal(value)


def test_ensure_safe_sql_string_literal_error_message_shows_codepoint_and_index() -> (
    None
):
    """制御文字拒否時のメッセージに U+XXXX と at index N が含まれることを確認する。"""
    # `\x1f` を index 5 に配置。
    value = "abcde\x1f/duck.db"
    with pytest.raises(run.CliUsageError) as exc_info:
        run.ensure_safe_sql_string_literal(value)
    message = str(exc_info.value)
    assert "U+001F" in message
    assert "at index 5" in message


# load_columns の YAML 読み込み経路


def test_load_columns_returns_dict_for_valid_yaml(tmp_path: pathlib.Path) -> None:
    """columns_dir に正常な dict 形式の YAML があるとき、 target をキーに columns と primary_key を返すことを確認する。"""
    (tmp_path / "rtc_stats.yml").write_text(
        "columns:\n  timestamp: TIMESTAMPTZ\n  id: VARCHAR\nprimary_key:\n  - id\n"
    )
    result = run.load_columns(targets=("rtc_stats",), columns_dir=str(tmp_path))
    assert result == {
        "rtc_stats": {
            "columns": {"timestamp": "TIMESTAMPTZ", "id": "VARCHAR"},
            "primary_key": ["id"],
        }
    }


def test_load_columns_returns_empty_primary_key_when_omitted(
    tmp_path: pathlib.Path,
) -> None:
    """primary_key が定義されていない YAML では空リストを返すことを確認する。"""
    (tmp_path / "rtc_stats.yml").write_text("columns:\n  timestamp: TIMESTAMPTZ\n")
    result = run.load_columns(targets=("rtc_stats",), columns_dir=str(tmp_path))
    assert result == {
        "rtc_stats": {
            "columns": {"timestamp": "TIMESTAMPTZ"},
            "primary_key": [],
        }
    }


def test_load_columns_raises_runtime_error_when_file_missing(
    tmp_path: pathlib.Path,
) -> None:
    """YAML ファイルが欠損している場合に RuntimeError を送出することを確認する。

    handle_cli_error では拾わずトレースバックで上位に伝播する (デプロイ / イメージビルド不備)。
    """
    with pytest.raises(
        RuntimeError, match="Column definition file not found"
    ) as exc_info:
        run.load_columns(targets=("rtc_stats",), columns_dir=str(tmp_path))
    assert "rtc_stats.yml" in str(exc_info.value)


def test_load_columns_raises_value_error_for_non_dict_yaml(
    tmp_path: pathlib.Path,
) -> None:
    """YAML が dict でない (list 形式) 場合に ValueError を送出することを確認する。"""
    (tmp_path / "rtc_stats.yml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="Invalid format") as exc_info:
        run.load_columns(targets=("rtc_stats",), columns_dir=str(tmp_path))
    assert "rtc_stats.yml" in str(exc_info.value)


def test_load_columns_raises_value_error_for_empty_yaml(tmp_path: pathlib.Path) -> None:
    """空 YAML (yaml.safe_load が None を返すケース) で ValueError を送出することを確認する。"""
    (tmp_path / "rtc_stats.yml").write_text("")
    with pytest.raises(ValueError, match="Invalid format") as exc_info:
        run.load_columns(targets=("rtc_stats",), columns_dir=str(tmp_path))
    assert "rtc_stats.yml" in str(exc_info.value)


# require_known_table の許可リスト検証


def test_require_known_table_accepts_known_name_in_dict() -> None:
    """dict のキーとして許可テーブル名が含まれていれば None を返すことを確認する。"""
    assert (
        run.require_known_table("rtc_stats", {"rtc_stats": {}, "session_webhook": {}})
        is None
    )


def test_require_known_table_rejects_unknown_name_in_dict() -> None:
    """dict のキーに含まれないテーブル名で ValueError を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown table name"):
        run.require_known_table("evil", {"rtc_stats": {}, "session_webhook": {}})


# delete_log_by_timestamp の table_name 許可リスト検証


def test_delete_log_by_timestamp_rejects_unknown_table() -> None:
    """LOG_TARGETS 外のテーブル名を渡すと ValueError を送出することを確認する (require_known_table 経由の Smoke)。"""
    # timestamp は require_known_table より先には使われないため、任意の有効値を渡す。
    timestamp = datetime.datetime.now(datetime.UTC)
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="Unknown table name"):
            run.delete_log_by_timestamp(
                con=con, table_name="evil_table", timestamp=timestamp
            )


# is_after_s3_cursor の比較ロジック


def test_is_after_s3_cursor_newer_last_modified() -> None:
    """last_modified がカーソルより新しければ True となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    assert run.is_after_s3_cursor((t_new, "a"), (t_old, "a")) is True


def test_is_after_s3_cursor_older_last_modified() -> None:
    """last_modified がカーソルより古ければ False となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    assert run.is_after_s3_cursor((t_old, "z"), (t_new, "a")) is False


def test_is_after_s3_cursor_same_last_modified_newer_object_name() -> None:
    """last_modified が同値なら object_name が大きい方を新しいと判定することを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    assert run.is_after_s3_cursor((t, "b"), (t, "a")) is True


def test_is_after_s3_cursor_same_last_modified_older_object_name() -> None:
    """last_modified が同値で object_name が小さい場合は False となることを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    assert run.is_after_s3_cursor((t, "a"), (t, "b")) is False


def test_is_after_s3_cursor_same_last_modified_same_object_name() -> None:
    """last_modified と object_name の両方が同値なら False となることを確認する (カーソル自身を除外)。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    assert run.is_after_s3_cursor((t, "a"), (t, "a")) is False


def test_is_after_s3_cursor_rejects_tz_naive_obj_last_modified() -> None:
    """obj 側の last_modified がタイムゾーン情報を含まないとき obj 側を示す ValueError を送出することを確認する。"""
    naive = datetime.datetime(2026, 1, 1)
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="S3 object has a timezone-naive"):
        run.is_after_s3_cursor((naive, "a"), (aware, "a"))


def test_is_after_s3_cursor_rejects_tz_naive_cursor_last_modified() -> None:
    """カーソル側の last_modified がタイムゾーン情報を含まないとき cursor 側を示す ValueError を送出することを確認する。"""
    naive = datetime.datetime(2026, 1, 1)
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="S3 cursor has a timezone-naive"):
        run.is_after_s3_cursor((aware, "a"), (naive, "a"))


def test_is_after_s3_cursor_rejects_none_last_modified() -> None:
    """どちらかの last_modified が None のとき ValueError を送出することを確認する。

    MinIO SDK の型上は None の可能性があるため、 明示的に拒否して
    タプル比較での TypeError を防ぐ。
    """
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="S3 object has a missing last_modified"):
        run.is_after_s3_cursor((None, "a"), (aware, "b"))
    with pytest.raises(ValueError, match="S3 cursor has a missing last_modified"):
        run.is_after_s3_cursor((aware, "a"), (None, "b"))


def test_is_after_s3_cursor_rejects_none_object_name() -> None:
    """どちらかの object_name が None のとき ValueError を送出することを確認する。

    MinIO SDK の型上は None の可能性があるため、 明示的に拒否して
    タプル比較での TypeError を防ぐ。
    """
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    with pytest.raises(ValueError, match="S3 object has a missing object_name"):
        run.is_after_s3_cursor((aware, None), (aware, "b"))
    with pytest.raises(ValueError, match="S3 cursor has a missing object_name"):
        run.is_after_s3_cursor((aware, "a"), (aware, None))


# init サブコマンドの初期化済み DB 早期 return


def test_init_skips_when_s3_objects_table_exists(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """s3_objects テーブルがある DB に対して init を呼ぶと、S3 認証情報がなくても return することを確認する。

    require_s3_credentials の呼び出し順序が変わって早期 return より先に認証チェックが
    走るようになるリグレッションを直接検出するため、S3 認証なしの args で例外が出ない
    ことを確認する。 stderr に「init skipped」 メッセージが出ることも併せて確認する。

    args に s3_access_key_id=None と s3_secret_access_key=None を明示する。
    require_s3_credentials まで到達した場合は CliUsageError("S3 credentials are required ...")
    が必ず送出されるため、 「例外が出ない」 ことが早期 return の証拠になる。 AttributeError
    での偶発的な落ち方と区別するための強化。
    """
    db_path = tmp_path / "initialized.db"
    # init を経由せずに s3_objects テーブルだけ手で作る。これで has_s3_objects_table が True になる。
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            "CREATE TABLE s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)"
        )

    # init が触りうる args 属性をすべて埋めておく (full_args で集約)。早期 return より
    # 先に他属性が参照されるリグレッションが起きた場合に AttributeError で偽通過させず、
    # require_s3_credentials の CliUsageError か別の想定例外として現れるようにする。
    args = full_args(db=str(db_path))

    # 早期 return で DB ファイルが触られないことを担保するため、前後で inode と内容を取る。
    before_ino = db_path.stat().st_ino
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # 例外なく完走し、戻り値が None であることを確認する
    assert run.init(args) is None

    # 早期 return 時に stderr へ「init skipped」 メッセージが出ることを確認する。
    captured = capsys.readouterr()
    assert "init skipped" in captured.err
    assert str(db_path) in captured.err

    # DB ファイルが書き換わっていないこと (inode と内容ともに不変)
    assert db_path.stat().st_ino == before_ino
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash

    # tmp_path 配下に .broken.<ts> や .copy などの付随ファイルが生成されていないことを確認する。
    assert [p.name for p in tmp_path.iterdir()] == ["initialized.db"]


# update サブコマンドの初期化済み DB 必須チェック


def test_initialize_log_table_rejects_silent_gap_state(tmp_path: pathlib.Path) -> None:
    """LOG_TARGETS テーブルが存在するが s3_objects カーソル行が無い状態で
    initialize_log_table が CliUsageError を送出することを確認する。

    手動 DELETE FROM s3_objects や s3_objects テーブル drop 後の init 再実行で発生する
    「テーブルはあるがカーソル行が無い状態」 をコード側で拒否することを担保する。
    この状態で処理を続けると、 create_log_table がスキップされる一方でカーソルだけが進み、
    過去オブジェクトが取り込まれない。 client には None を渡してもガードが早期に走るため
    iter_objects まで到達しない。
    到達してしまうリグレッションは AttributeError で顕在化する。
    """
    db_path = tmp_path / "silent_gap.db"
    target = "rtc_stats"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            "CREATE TABLE s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)"
        )
        con.execute(f"CREATE TABLE {target} (timestamp TIMESTAMPTZ)")

    args = full_args(db=str(db_path))
    with duckdb.connect(str(db_path)) as con:  # noqa: SIM117
        with pytest.raises(run.CliUsageError, match="s3_objects cursor is missing"):
            run.initialize_log_table(con, None, args, target)


def test_update_rejects_uninitialized_db(tmp_path: pathlib.Path) -> None:
    """s3_objects テーブルが無い DB に対して update が CliUsageError を送出することを確認する。

    has_s3_objects_table が False のときに update が事前チェックで弾くことを担保する。
    全 args 属性を full_args で埋めることで、 「事前チェックより先に他属性が参照される
    リグレッション」 を AttributeError で偽通過させない。
    """
    db_path = tmp_path / "uninitialized.db"
    # init を経由せずに DB ファイルだけ作る。s3_objects テーブルは存在しない。
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE dummy (id INTEGER)")

    args = full_args(db=str(db_path))
    with pytest.raises(run.CliUsageError, match="s3_objects table not found"):
        run.update(args)


def test_update_rejects_missing_s3_credentials(tmp_path: pathlib.Path) -> None:
    """s3_objects テーブルがある DB で S3 認証情報が無いと update が CliUsageError を送出する
    ことを確認する。

    has_s3_objects_table を通過した直後に require_s3_credentials が呼ばれる順序を担保する。
    順序が逆転して require_s3_credentials が先に走るリグレッションを直接検出する。
    """
    db_path = tmp_path / "initialized.db"
    # init を経由せずに s3_objects テーブルだけ手で作る。has_s3_objects_table が True になる。
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            "CREATE TABLE s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)"
        )

    # 認証情報を None で明示し (full_args のデフォルトが None)、require_s3_credentials
    # まで到達したら必ず CliUsageError で落ちる前提にする。他属性も full_args で埋めて
    # 「require_s3_credentials より先に他属性が参照されるリグレッション」 を AttributeError
    # で偽通過させない。
    args = full_args(db=str(db_path))
    with pytest.raises(run.CliUsageError, match="S3 credentials are required"):
        run.update(args)


@pytest.mark.parametrize("subcommand", [run.update, run.delete])
def test_update_or_delete_raises_file_not_found_when_db_missing(
    subcommand: Callable[[run.Args], None], tmp_path: pathlib.Path
) -> None:
    """DB ファイルが存在しないとき update / delete が FileNotFoundError を送出することを確認する。

    main 側の事前チェックを削った後、 各サブコマンド自身でファイル不在を検出して
    handle_cli_error 経由でメッセージを統一する経路を担保する。
    """
    missing = tmp_path / "missing.db"
    args = full_args(db=str(missing))
    with pytest.raises(FileNotFoundError, match="DB file not found"):
        subcommand(args)


# is_broken_db_error のキーワード判定


@pytest.mark.parametrize(
    "message",
    [
        "Database file is corrupt",
        "invalid database file",
        "File is not a valid duckdb file",
    ],
)
def test_is_broken_db_error_detects_known_patterns(message: str) -> None:
    """BROKEN_DB_ERROR_PATTERNS の各パターンを含むメッセージが True 判定されることを確認する。"""
    assert run.is_broken_db_error(Exception(message)) is True


def test_is_broken_db_error_is_case_insensitive() -> None:
    """大文字を含むメッセージでもパターン検出されることを確認する (`.lower()` 経由の判定担保)。"""
    assert run.is_broken_db_error(Exception("Database file is CORRUPT")) is True


def test_is_broken_db_error_returns_false_for_unrelated_message() -> None:
    """破損とは無関係なエラーメッセージで False を返すことを確認する。"""
    assert run.is_broken_db_error(Exception("Permission denied")) is False


# move_broken_db


def test_move_broken_db_handles_db_without_wal(tmp_path: pathlib.Path) -> None:
    """WAL ファイルが存在しない DB を退避できることを確認する。"""
    db_path = tmp_path / "broken.db"
    db_path.write_bytes(b"invalid db payload")

    broken_db_path = run.move_broken_db(str(db_path))

    # 元 DB が消えて、退避先が存在すること
    assert not db_path.exists()
    assert os.path.exists(broken_db_path)
    # WAL は元から無いため、退避先 WAL も作られないこと
    assert not os.path.exists(f"{broken_db_path}.wal")
    # broken_db_path の命名規則 (元パス + ".broken." + timestamp) に従うこと
    assert broken_db_path.startswith(f"{db_path}.broken.")


def test_move_broken_db_does_not_overwrite_when_called_in_quick_succession(
    tmp_path: pathlib.Path,
) -> None:
    """短時間に複数回呼び出されても退避先が衝突せず、過去の破損ファイルを失わないことを確認する。

    crash loop 等で同じ DB パスに対して連続して破損退避が走るケースで、退避先
    (.broken.<timestamp>) が衝突して shutil.move による上書きで過去の破損ファイルが
    消えないことを担保する。タイムスタンプにマイクロ秒を含める実装に依存する。
    """
    db_path = tmp_path / "broken.db"
    wal_path = tmp_path / "broken.db.wal"

    # 1 回目の破損退避
    db_path.write_bytes(b"invalid db payload 1")
    wal_path.write_bytes(b"wal payload 1")
    broken_db_path_1 = run.move_broken_db(str(db_path))

    # 同名 DB が再生成された直後に再び破損退避されるシナリオを再現する
    db_path.write_bytes(b"invalid db payload 2")
    wal_path.write_bytes(b"wal payload 2")
    broken_db_path_2 = run.move_broken_db(str(db_path))

    # 退避先パスが一意であること
    assert broken_db_path_1 != broken_db_path_2
    # 過去の破損 DB と WAL が上書きされず両方残っていること
    assert os.path.exists(broken_db_path_1)
    assert os.path.exists(broken_db_path_2)
    assert os.path.exists(f"{broken_db_path_1}.wal")
    assert os.path.exists(f"{broken_db_path_2}.wal")


# exit_with_stderr


def test_exit_with_stderr_writes_message_to_stderr_and_exits_with_code_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """exit_with_stderr がメッセージを stderr に書き、exit code 1 で終了することを確認する。"""
    with pytest.raises(SystemExit) as exc_info:
        run.exit_with_stderr("failure message")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "failure message"


# handle_cli_error


def _make_s3_error(code: str, message: str = "boom") -> S3Error:
    """テスト用の最小限の S3Error を生成する。

    response 引数は S3Error コンストラクタが urllib3 由来のレスポンス互換オブジェクトを
    要求するため形式上 HTTPResponse() を渡している (urllib3 のバージョン制約は
    pyproject.toml の urllib3 行のコメント参照)。
    """
    return S3Error(
        code=code,
        message=message,
        resource="/test",
        request_id="req-id",
        host_id="host-id",
        response=urllib3.HTTPResponse(),
        bucket_name="test-bucket",
    )


def test_handle_cli_error_exits_for_s3_no_such_bucket(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S3Error(NoSuchBucket) のとき bucket 名を含むメッセージで exit code 1 終了することを確認する。"""
    error = _make_s3_error("NoSuchBucket")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "S3 bucket not found: my-bucket" in captured.err


def test_handle_cli_error_exits_for_other_s3_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S3Error(NoSuchBucket 以外) のとき code と message を含むメッセージで exit code 1 終了することを確認する。"""
    error = _make_s3_error("AccessDenied", message="access denied")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "S3 error occurred (code=AccessDenied): access denied" in captured.err
    # bucket は NoSuchBucket 以外の経路では出力に混入しないことを確認する。
    assert "my-bucket" not in captured.err


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        pytest.param(
            FileNotFoundError("DB file not found: /tmp/missing.db"),
            "DB file not found: /tmp/missing.db",
            id="file-not-found",
        ),
        pytest.param(
            run.CliUsageError("invalid input"),
            "invalid input",
            id="cli-usage-error",
        ),
    ],
)
def test_handle_cli_error_exits_for_file_not_found_or_cli_usage_error(
    capsys: pytest.CaptureFixture[str], error: Exception, expected_message: str
) -> None:
    """FileNotFoundError / CliUsageError のとき str(error) を stderr に書いて exit code 1 終了することを確認する。"""
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert expected_message in captured.err
    # bucket は FileNotFoundError / CliUsageError 経路では出力に混入しないことを確認する。
    assert "my-bucket" not in captured.err


@pytest.mark.parametrize(
    ("error", "match_pattern"),
    [
        pytest.param(
            ValueError("Unknown table name: evil_table"),
            "Unknown table name",
            id="value-error",
        ),
        pytest.param(
            RuntimeError("unexpected"),
            "unexpected",
            id="runtime-error",
        ),
    ],
)
def test_handle_cli_error_reraises_non_handled_errors(
    error: Exception, match_pattern: str
) -> None:
    """S3Error / FileNotFoundError / CliUsageError 以外の例外はそのまま再送出することを確認する。

    内部用 ValueError (Unknown table name / load_columns の YAML 形式異常 等) や未知の
    エラータイプは CLI ユーザー入力エラーではないため、 CliUsageError で別経路に分離せず
    トレースバック付きで上位に伝播させる。
    """
    with pytest.raises(type(error), match=match_pattern):
        run.handle_cli_error(error, "my-bucket")


# prepare_db_for_init


def test_prepare_db_for_init_renames_broken_db_file(tmp_path: pathlib.Path) -> None:
    """壊れた DB を prepare_db_for_init が検出し、DB と WAL をリネームして退避したことを確認する。"""
    db_path = tmp_path / "broken.db"
    wal_path = tmp_path / "broken.db.wal"
    db_path.write_bytes(b"invalid db")
    wal_path.write_bytes(b"wal")

    run.prepare_db_for_init(str(db_path))

    renamed_files = list(tmp_path.glob("broken.db.broken.*"))
    assert len(renamed_files) == 2
    renamed_db_files = [
        path for path in renamed_files if not str(path).endswith(".wal")
    ]
    renamed_wal_files = [path for path in renamed_files if str(path).endswith(".wal")]
    assert len(renamed_db_files) == 1
    assert len(renamed_wal_files) == 1

    assert db_path.exists() is False
    assert wal_path.exists() is False
    assert renamed_db_files[0].exists()
    assert renamed_wal_files[0].exists()


def test_prepare_db_for_init_raises_on_permission_denied(
    tmp_path: pathlib.Path,
) -> None:
    """Permission denied のような破損ではない接続エラーは握りつぶさず再 raise することを確認する。

    握りつぶして return すると直後の has_s3_objects_table が同じパスへ再 connect して
    同じ例外を再発させ、ユーザーに二重出力を見せてしまうため、明示的に raise させる。
    破損ではないので退避ファイルも作られないことを併せて確認する。
    """
    # root 実行時は chmod 0 が無視されて Permission denied を再現できないため、
    # 偽通過を避けるためにテストを明示的に失敗させる。
    if os.geteuid() == 0:
        pytest.fail("root では chmod 0 を強制できないためテスト不能です")
    db_path = tmp_path / "permission.db"
    wal_path = tmp_path / "permission.db.wal"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    wal_path.write_bytes(b"wal")
    os.chmod(db_path, 0)

    try:
        with pytest.raises(duckdb.IOException) as exc_info:
            run.prepare_db_for_init(str(db_path))
        # 破損ではない接続エラー (Permission denied 等) であることを確認する
        assert run.is_broken_db_error(exc_info.value) is False

        renamed_files = list(tmp_path.glob("permission.db.broken.*"))
        # Permission denied は破損 DB ではないため、退避ファイルは作られない
        assert len(renamed_files) == 0
        assert db_path.exists()
        assert wal_path.exists()
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)


# raise_if_db_broken


def test_raise_if_db_broken_returns_for_non_existent_db(tmp_path: pathlib.Path) -> None:
    """DB ファイルが存在しないときは何もせずに return することを確認する。"""
    missing = tmp_path / "missing.db"
    assert run.raise_if_db_broken(str(missing)) is None


def test_raise_if_db_broken_returns_for_healthy_db(tmp_path: pathlib.Path) -> None:
    """正常な DB に対しては何もせずに return することを確認する。"""
    db_path = tmp_path / "healthy.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    assert run.raise_if_db_broken(str(db_path)) is None


def test_raise_if_db_broken_raises_cli_usage_error_for_broken_db(
    tmp_path: pathlib.Path,
) -> None:
    """破損 DB に対しては自動退避せず CliUsageError を送出することを確認する。

    update / delete では運用者の判断を優先するため、検出のみ行い、メッセージで
    init の再実行を促す挙動を担保する。 exit code / stderr への整形は
    handle_cli_error の責務なので、 ここでは例外内容のみ検査する。
    """
    db_path = tmp_path / "broken.db"
    db_path.write_bytes(b"invalid db")

    with pytest.raises(run.CliUsageError) as exc_info:
        run.raise_if_db_broken(str(db_path))
    message = str(exc_info.value)
    assert "DB file is broken" in message
    assert str(db_path) in message
    # 「'init' の再実行を促す」 という本番メッセージ固有の言い回しを直接確認する。
    assert "run 'init'" in message
    # 退避ファイルは作られないことを確認する
    renamed_files = list(tmp_path.glob("broken.db.broken.*"))
    assert len(renamed_files) == 0


def test_raise_if_db_broken_propagates_non_broken_errors(
    tmp_path: pathlib.Path,
) -> None:
    """Permission denied のような破損ではない接続エラーは再 raise することを確認する。"""
    # root 実行時は chmod 0 が無視されて Permission denied を再現できないため、
    # 偽通過を避けるためにテストを明示的に失敗させる。
    if os.geteuid() == 0:
        pytest.fail("root では chmod 0 を強制できないためテスト不能です")
    db_path = tmp_path / "permission.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    os.chmod(db_path, 0)

    try:
        with pytest.raises(duckdb.IOException) as exc_info:
            run.raise_if_db_broken(str(db_path))
        # 破損ではない接続エラー (Permission denied 等) であることを確認する
        assert run.is_broken_db_error(exc_info.value) is False
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)


# is_db_broken


def test_is_db_broken_does_not_replay_wal_for_healthy_db_with_broken_wal(
    tmp_path: pathlib.Path,
) -> None:
    """正常 DB に破損 WAL が併存しても False を返すことを確認する。

    is_db_broken は read_only=True で開くため WAL 再生を行わない。 これにより
    「DB 本体は正常だが WAL が破損」 のケースを破損扱いにしない (WAL 再生の副作用で
    破損状態を書き換えないための設計意図)。 read_only=False へ regression すると、
    duckdb は WAL 再生を試みて IOException を送出し、 is_db_broken が True を
    返すか例外を送出するため、 本テストは失敗する。
    """
    db_path = tmp_path / "healthy_with_broken_wal.db"
    wal_path = tmp_path / "healthy_with_broken_wal.db.wal"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    wal_path.write_bytes(b"broken wal data")

    assert run.is_db_broken(str(db_path)) is False


def test_is_db_broken_returns_true_for_missing_db_with_leftover_wal(
    tmp_path: pathlib.Path,
) -> None:
    """DB 本体が無く .wal だけ残っている状態を破損扱いにすることを確認する。

    DB 本体を削除した運用者が .wal を消し忘れた場合、 新規 DB 作成時に古い WAL が
    再生されて意図しない状態 (古いカーソルやテーブル) が復活する。 これを防ぐため、
    DB 本体が存在しないが .wal が残っている場合は破損として扱う。
    """
    db_path = tmp_path / "missing_with_wal.db"
    wal_path = tmp_path / "missing_with_wal.db.wal"
    wal_path.write_bytes(b"wal payload")

    assert run.is_db_broken(str(db_path)) is True


def test_is_db_broken_returns_false_for_missing_db_without_wal(
    tmp_path: pathlib.Path,
) -> None:
    """DB 本体も .wal も存在しない場合は False を返すことを確認する。"""
    db_path = tmp_path / "missing_without_wal.db"

    assert run.is_db_broken(str(db_path)) is False


def test_is_db_broken_raises_with_guidance_on_unrecognized_error(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """破損パターンに一致しない接続エラーは raise し、 破損の可能性を案内することを確認する。

    DuckDB のバージョン更新で破損文言が変わり、 BROKEN_DB_ERROR_PATTERNS に一致
    しなくなる場合でも、 ユーザーに破損の可能性を伝えられるようにする。 ロック競合や
    権限不足のような破損ではない接続エラーは破損として退避しない (伝播させる) 設計を
    維持する。
    """
    # root 実行時は chmod 0 が無視されて Permission denied を再現できないため、
    # 偽通過を避けるためにテストを明示的に失敗させる。
    if os.geteuid() == 0:
        pytest.fail("root では chmod 0 を強制できないためテスト不能です")
    db_path = tmp_path / "unrecognized_error.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    os.chmod(db_path, 0)

    try:
        with pytest.raises(duckdb.IOException):
            run.is_db_broken(str(db_path))
        captured = capsys.readouterr()
        assert "may be a broken DB" in captured.err
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)


def test_prepare_db_for_init_evacuates_leftover_wal_without_db(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DB 本体が無く .wal だけ残っている状態を prepare_db_for_init が退避することを確認する。

    新規 DB 作成時に古い WAL が再生されて意図しない状態が復活するのを防ぐため、
    .wal ごと退避し、 退避メッセージを stderr に出力する。
    """
    db_path = tmp_path / "leftover_wal.db"
    wal_path = tmp_path / "leftover_wal.db.wal"
    wal_path.write_bytes(b"wal payload")

    run.prepare_db_for_init(str(db_path))

    renamed_files = list(tmp_path.glob("leftover_wal.db.broken.*"))
    assert len(renamed_files) == 1
    assert wal_path.exists() is False
    assert renamed_files[0].exists()
    captured = capsys.readouterr()
    assert "Detected broken DB file. moved to" in captured.err


# full_args


def test_full_args_rejects_unknown_override_key() -> None:
    """未知の override キーを渡すと TypeError で拒否されることを確認する。

    テスト側で属性名を typo したときに黙って通過することを防ぐガードが機能する
    ことを担保する。
    """
    with pytest.raises(TypeError, match="Unknown override keys"):
        full_args(unknown_key="value")


def test_full_args_covers_all_args_fields() -> None:
    """full_args のデフォルトキー集合が Args のフィールド集合と一致することを確認する。

    Args にフィールドが追加されたのに full_args に追加し忘れると、 Args の本番
    デフォルトが黙って適用される (例: s3_use_ssl のテスト用 False が True に
    戻る)。 キー集合の一致で追加し忘れを検出する。 func は set_defaults が注入
    するため full_args では設定しない。
    """
    args_fields = {field for field in vars(run.Args()) if field != "func"}
    full_args_keys = set(vars(full_args()))
    assert full_args_keys - {"func"} == args_fields, (
        f"full_args のデフォルトキーが Args のフィールドと一致しません: "
        f"full_args のみ {sorted(full_args_keys - {'func'} - args_fields)}、 "
        f"Args のみ {sorted(args_fields - full_args_keys)}"
    )
