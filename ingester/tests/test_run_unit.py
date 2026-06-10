import argparse
import datetime
import hashlib
import os
import stat
from types import SimpleNamespace

import duckdb
import pytest
import urllib3
from minio.error import S3Error

import run


def test_positive_int_rejects_zero():
    """0 を渡した場合に引数エラーとなることを確認する。"""
    with pytest.raises(argparse.ArgumentTypeError, match="value must be >= 1"):
        run.positive_int("0")


def test_sync_logs_raises_for_unknown_mode():
    """未知の実行モードを指定した場合に例外を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown mode"):
        run.sync_logs(con=None, client=None, args=None, mode="invalid")


def test_insert_log_from_s3_rejects_none_update_maximum_load():
    """update_maximum_load が None のとき ValueError を送出することを確認する。

    本体に到達する前に弾くことで、len(target_log_objects) > None の TypeError を
    回避し、診断しやすいメッセージで早期に失敗させる。
    """
    with pytest.raises(ValueError, match="update_maximum_load is required"):
        run.insert_log_from_s3(
            con=None,
            client=None,
            table_name="rtc_stats",
            bucket="kohaku",
            prefix="log",
            update_maximum_load=None,
        )


def test_delete_returns_without_copy_when_no_rows_deleted(tmp_path):
    """削除件数が 0 件の場合に DB コピー処理へ進まず、元 DB も変化しないことを確認する。"""
    db_path = tmp_path / "delete_no_rows.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")

    before_stat = db_path.stat()
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    args = SimpleNamespace(db=str(db_path), retention_period=1)
    run.delete(args)

    after_stat = db_path.stat()
    after_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # DB ファイルが存在し、inode が変わらず、内容も変わっていないことを確認する。
    assert db_path.exists()
    assert after_stat.st_ino == before_stat.st_ino
    assert after_hash == before_hash
    assert os.path.exists(f"{db_path}.copy") is False


def test_delete_handles_single_quote_in_db_path(tmp_path):
    """シングルクォートを含む DB ファイルパスでも ATTACH 文が成立し、delete が完走することを確認する。

    escape_sql_string_literal によるエスケープが実際の ATTACH 文で有効であることを、
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

    args = SimpleNamespace(db=str(db_path), retention_period=1)
    run.delete(args)

    # delete 後、古い行が削除されていることを確認する。
    # ATTACH/COPY 経路が破綻していればここまで到達せず、データも消えない。
    with duckdb.connect(str(db_path)) as con:
        result = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()
        assert result is not None
        assert result[0] == 0

    # COPY 用の一時ファイルが残っていないことを確認する (ATTACH/COPY/move が完了している)。
    assert os.path.exists(f"{db_path}.copy") is False


def test_delete_restricts_db_file_permission(tmp_path):
    """delete 完了後の DB ファイルのパーミッションが owner と group のみに縮小されることを確認する。

    chmod 対象は COPY 先の copy ファイルだが、shutil.move 後に同じパーミッションが
    args.db に反映される。other 読み書きと group 書き込み以外の権限が落ちることを保証する。
    """
    db_path = tmp_path / "delete_permission.db"

    # retention_period=1 で削除対象となるよう、2 日前の timestamp を持つ行を挿入する。
    old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE rtc_stats (timestamp TIMESTAMPTZ)")
        con.execute("CREATE TABLE session_webhook (timestamp TIMESTAMPTZ)")
        con.execute("INSERT INTO rtc_stats VALUES (?)", (old_timestamp,))

    # 元 DB を過剰権限にしておき、delete が明示的に縮小していることを示せるようにする。
    os.chmod(db_path, 0o666)

    args = SimpleNamespace(db=str(db_path), retention_period=1)
    run.delete(args)

    # owner: rw, group: rw, other: なし
    expected_mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
    actual_mode = stat.S_IMODE(db_path.stat().st_mode)
    assert actual_mode == expected_mode


# should_create_readonly


def test_should_create_readonly_returns_false_when_db_missing(tmp_path):
    """DB ファイルが存在しないとき False を返すことを確認する。"""
    missing = tmp_path / "missing.db"
    assert run.should_create_readonly(str(missing), initial_mtime=0.0) is False


def test_should_create_readonly_returns_false_when_mtime_unchanged(tmp_path):
    """initial_mtime と現在の mtime が一致するとき False を返すことを確認する。"""
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    mtime = db_path.stat().st_mtime
    assert run.should_create_readonly(str(db_path), initial_mtime=mtime) is False


def test_should_create_readonly_returns_true_when_mtime_changed(tmp_path):
    """initial_mtime と現在の mtime が異なるとき True を返すことを確認する。"""
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    initial = db_path.stat().st_mtime
    # mtime を未来日時に更新して initial と差をつける
    future = initial + 100.0
    os.utime(db_path, (future, future))
    assert run.should_create_readonly(str(db_path), initial_mtime=initial) is True


def test_should_create_readonly_returns_true_when_initial_mtime_is_none_and_db_created(
    tmp_path,
):
    """initial_mtime=None かつ DB ファイルが存在するとき True を返すことを確認する。

    init で DB ファイルを新規作成したケース (主要呼び出し経路) を担保する。
    """
    db_path = tmp_path / "db.db"
    db_path.write_bytes(b"payload")
    assert run.should_create_readonly(str(db_path), initial_mtime=None) is True


# create_readonly_copy


def test_create_readonly_copy_generates_readonly_with_restricted_permission(tmp_path):
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


def test_create_readonly_copy_overwrites_existing_readonly(tmp_path):
    """既に .readonly が存在しても、最新の DB 内容で上書きされることを確認する。"""
    db_path = tmp_path / "source.db"
    db_path.write_bytes(b"new-payload")

    readonly_path = tmp_path / "source.db.readonly"
    # 既存の .readonly を別内容で配置しておく
    readonly_path.write_bytes(b"stale-payload")

    run.create_readonly_copy(str(db_path))

    assert readonly_path.read_bytes() == b"new-payload"


def test_create_readonly_copy_does_not_leave_tmp_file(tmp_path):
    """create_readonly_copy が一時ファイル .tmp を後始末することを確認する。"""
    db_path = tmp_path / "source.db"
    db_path.write_bytes(b"payload")

    run.create_readonly_copy(str(db_path))

    tmp_file = tmp_path / "source.db.tmp"
    assert not tmp_file.exists()


# require_s3_credentials


def test_require_s3_credentials_rejects_missing_access_key():
    """access_key が未指定の場合に CliUsageError を送出することを確認する。"""
    args = SimpleNamespace(s3_access_key_id=None, s3_secret_access_key="secret")
    with pytest.raises(run.CliUsageError, match="S3 credentials are required"):
        run.require_s3_credentials(args)


def test_require_s3_credentials_rejects_missing_secret():
    """secret が未指定の場合に CliUsageError を送出することを確認する。"""
    args = SimpleNamespace(s3_access_key_id="access", s3_secret_access_key=None)
    with pytest.raises(run.CliUsageError, match="S3 credentials are required"):
        run.require_s3_credentials(args)


def test_require_s3_credentials_accepts_valid_credentials():
    """両方の値が指定されている場合は例外を送出しないことを確認する。"""
    args = SimpleNamespace(s3_access_key_id="access", s3_secret_access_key="secret")
    # 例外が発生しないことを確認する (戻り値は None)
    assert run.require_s3_credentials(args) is None


# escape_sql_string_literal


def test_escape_sql_string_literal_doubles_single_quote():
    """シングルクォートが 1 個含まれる場合に 2 個に変換することを確認する。"""
    assert run.escape_sql_string_literal("a'b") == "a''b"


def test_escape_sql_string_literal_handles_multiple_quotes():
    """複数のシングルクォートをすべて二重化することを確認する。"""
    assert run.escape_sql_string_literal("'a'b'c'") == "''a''b''c''"


def test_escape_sql_string_literal_handles_consecutive_quotes():
    """連続したシングルクォートも正しく二重化することを確認する。"""
    assert run.escape_sql_string_literal("a''b") == "a''''b"


def test_escape_sql_string_literal_passes_through_safe_string():
    """シングルクォートを含まない文字列はそのまま返すことを確認する。"""
    assert (
        run.escape_sql_string_literal("/var/lib/kohaku/duck.db")
        == "/var/lib/kohaku/duck.db"
    )


def test_escape_sql_string_literal_handles_empty_string():
    """空文字列を渡しても例外なく空文字列を返すことを確認する。"""
    assert run.escape_sql_string_literal("") == ""


def test_escape_sql_string_literal_neutralizes_injection_payload():
    """SQL インジェクション風の payload も単純なエスケープで無害化されることを確認する。"""
    payload = "'; DROP TABLE x; --"
    expected = "''; DROP TABLE x; --"
    assert run.escape_sql_string_literal(payload) == expected


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
def test_escape_sql_string_literal_rejects_control_characters(control_char):
    """制御文字を含む値は CliUsageError で拒否されることを確認する。"""
    value = f"/var/lib/kohaku/duck{control_char}.db"
    with pytest.raises(run.CliUsageError, match="control characters"):
        run.escape_sql_string_literal(value)


@pytest.mark.parametrize(
    "boundary_char",
    [
        " ",  # 0x20 (空白): 制御文字範囲の直後
        "~",  # 0x7E (チルダ): DEL の直前
        "\xa0",  # 0xA0 (NO-BREAK SPACE): DEL の直後の非 ASCII 文字
        "あ",  # マルチバイト文字 (U+3042)
    ],
)
def test_escape_sql_string_literal_accepts_boundary_characters(boundary_char):
    """制御文字範囲の境界 (0x20 と 0x7E) と非 ASCII 文字は受容することを確認する。"""
    value = f"/var/lib/kohaku/duck{boundary_char}.db"
    # シングルクォートを含まないので元の値がそのまま返る
    assert run.escape_sql_string_literal(value) == value


# delete_log_by_timestamp の table_name 許可リスト検証


def test_delete_log_by_timestamp_rejects_unknown_table():
    """LOG_TARGETS 外のテーブル名を渡すと ValueError を送出することを確認する。"""
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="Unknown table name"):
            run.delete_log_by_timestamp(
                con=con, table_name="evil_table", timestamp=None
            )


def test_delete_log_by_timestamp_rejects_sql_injection_attempt():
    """SQL インジェクションを試みる文字列も許可リストではじかれることを確認する。"""
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="Unknown table name"):
            run.delete_log_by_timestamp(
                con=con,
                table_name="rtc_stats; DROP TABLE x",
                timestamp=None,
            )


def test_delete_log_by_timestamp_rejects_empty_table_name():
    """空文字のテーブル名も許可リストではじかれることを確認する。"""
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="Unknown table name"):
            run.delete_log_by_timestamp(con=con, table_name="", timestamp=None)


# is_after_s3_cursor の比較ロジック


def test_is_after_s3_cursor_newer_last_modified():
    """last_modified がカーソルより新しければ True となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=t_new, object_name="a")
    assert run.is_after_s3_cursor(obj, t_old, "a") is True


def test_is_after_s3_cursor_older_last_modified():
    """last_modified がカーソルより古ければ False となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=t_old, object_name="z")
    assert run.is_after_s3_cursor(obj, t_new, "a") is False


def test_is_after_s3_cursor_same_last_modified_newer_object_name():
    """last_modified が同値なら object_name が大きい方を新しいと判定することを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=t, object_name="b")
    assert run.is_after_s3_cursor(obj, t, "a") is True


def test_is_after_s3_cursor_same_last_modified_older_object_name():
    """last_modified が同値で object_name が小さい場合は False となることを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=t, object_name="a")
    assert run.is_after_s3_cursor(obj, t, "b") is False


def test_is_after_s3_cursor_same_last_modified_same_object_name():
    """last_modified と object_name の両方が同値なら False となることを確認する (カーソル自身を除外)。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=t, object_name="a")
    assert run.is_after_s3_cursor(obj, t, "a") is False


def test_is_after_s3_cursor_rejects_tz_naive_obj_last_modified():
    """obj.last_modified がタイムゾーン情報を含まないとき ValueError を送出することを確認する。"""
    naive = datetime.datetime(2026, 1, 1)
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=naive, object_name="a")
    with pytest.raises(ValueError, match="timezone-naive last_modified"):
        run.is_after_s3_cursor(obj, aware, "a")


def test_is_after_s3_cursor_rejects_tz_naive_cursor_last_modified():
    """カーソル側の last_modified がタイムゾーン情報を含まないとき ValueError を送出することを確認する。"""
    naive = datetime.datetime(2026, 1, 1)
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=aware, object_name="a")
    with pytest.raises(ValueError, match="timezone-naive last_modified"):
        run.is_after_s3_cursor(obj, naive, "a")


def test_is_after_s3_cursor_rejects_none_obj_last_modified():
    """obj.last_modified が None のとき ValueError を送出することを確認する。

    minio SDK の Object.last_modified は Optional[datetime] のため、tzinfo を参照する前に
    None を ValueError として明示的に拒否し、AttributeError を表出させない。
    """
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=None, object_name="a")
    with pytest.raises(ValueError, match="missing last_modified"):
        run.is_after_s3_cursor(obj, aware, "a")


def test_is_after_s3_cursor_rejects_none_cursor_last_modified():
    """カーソル側の last_modified が None のとき ValueError を送出することを確認する。"""
    aware = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    obj = SimpleNamespace(last_modified=aware, object_name="a")
    with pytest.raises(ValueError, match="missing last_modified"):
        run.is_after_s3_cursor(obj, None, "a")


# init サブコマンドの初期化済み DB 早期 return


def test_init_returns_without_s3_credentials_when_db_initialized(tmp_path):
    """s3_objects テーブルがある DB に対して init を呼ぶと、S3 認証情報がなくても return することを確認する。

    require_s3_credentials の呼び出し順序が変わって早期 return より先に認証チェックが
    走るようになるリグレッションを直接検出するため、S3 認証なしの args で例外が出ない
    ことを確認する。
    """
    db_path = tmp_path / "initialized.db"
    # init を経由せずに s3_objects テーブルだけ手で作る。これで is_initialized_db が True になる。
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            "CREATE TABLE s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)"
        )

    # s3_* 系を持たない最小構成の args。require_s3_credentials が呼ばれれば AttributeError か ValueError で落ちる。
    args = SimpleNamespace(db=str(db_path))

    # 例外なく完走し、戻り値が None であることを確認する
    assert run.init(args) is None


# update サブコマンドの初期化済み DB 必須チェック


def test_update_rejects_uninitialized_db(tmp_path):
    """s3_objects テーブルが無い DB に対して update が CliUsageError を送出することを確認する。"""
    db_path = tmp_path / "uninitialized.db"
    # init を経由せずに DB ファイルだけ作る。s3_objects テーブルは存在しない。
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE dummy (id INTEGER)")

    args = SimpleNamespace(db=str(db_path))
    with pytest.raises(run.CliUsageError, match="DB file is not initialized"):
        run.update(args)


def test_update_raises_file_not_found_when_db_missing(tmp_path):
    """DB ファイルが存在しないとき update が FileNotFoundError を送出することを確認する。

    main 側の事前チェックを削った後、update 自身でファイル不在を検出して
    handle_cli_error 経由でメッセージを統一する経路を担保する。
    """
    missing = tmp_path / "missing.db"
    args = SimpleNamespace(db=str(missing))
    with pytest.raises(FileNotFoundError, match="DB file not found"):
        run.update(args)


def test_delete_raises_file_not_found_when_db_missing(tmp_path):
    """DB ファイルが存在しないとき delete が FileNotFoundError を送出することを確認する。"""
    missing = tmp_path / "missing.db"
    args = SimpleNamespace(db=str(missing))
    with pytest.raises(FileNotFoundError, match="DB file not found"):
        run.delete(args)


# is_broken_db_error のキーワード判定


@pytest.mark.parametrize(
    "message",
    [
        "Database file is corrupt",
        "invalid database file",
        "File is not a valid duckdb file",
    ],
)
def test_is_broken_db_error_detects_known_patterns(message):
    """BROKEN_DB_ERROR_PATTERNS の各パターンを含むメッセージが True 判定されることを確認する。"""
    assert run.is_broken_db_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Database file is CORRUPT",
        "INVALID DATABASE file",
        "File is NOT A VALID DUCKDB",
    ],
)
def test_is_broken_db_error_is_case_insensitive(message):
    """大文字を含むメッセージでもパターン検出されることを確認する。"""
    assert run.is_broken_db_error(Exception(message)) is True


def test_is_broken_db_error_returns_false_for_unrelated_message():
    """破損とは無関係なエラーメッセージで False を返すことを確認する。"""
    assert run.is_broken_db_error(Exception("Permission denied")) is False


def test_is_broken_db_error_returns_false_for_empty_message():
    """空のエラーメッセージで False を返すことを確認する。"""
    assert run.is_broken_db_error(Exception("")) is False


# move_broken_db


def test_move_broken_db_handles_db_without_wal(tmp_path):
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


def test_move_broken_db_does_not_overwrite_when_called_in_quick_succession(tmp_path):
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


def test_exit_with_stderr_writes_message_to_stderr_and_exits_with_code_1(capsys):
    """exit_with_stderr がメッセージを stderr に書き、exit code 1 で終了することを確認する。"""
    with pytest.raises(SystemExit) as exc_info:
        run.exit_with_stderr("failure message")
    assert exc_info.value.code == 1
    # stderr を取得する
    captured = capsys.readouterr()
    assert captured.err.strip() == "failure message"


# handle_cli_error


def _make_s3_error(code: str, message: str = "boom") -> S3Error:
    """テスト用の最小限の S3Error を生成する。"""
    return S3Error(
        code=code,
        message=message,
        resource="/test",
        request_id="req-id",
        host_id="host-id",
        response=urllib3.HTTPResponse(),
        bucket_name="test-bucket",
    )


def test_handle_cli_error_exits_for_s3_no_such_bucket(capsys):
    """S3Error(NoSuchBucket) のとき bucket 名を含むメッセージで exit code 1 終了することを確認する。"""
    error = _make_s3_error("NoSuchBucket")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    # stderr を取得する
    captured = capsys.readouterr()
    assert "S3 bucket not found: my-bucket" in captured.err


def test_handle_cli_error_exits_for_other_s3_error(capsys):
    """S3Error(NoSuchBucket 以外) のとき code と message を含むメッセージで exit code 1 終了することを確認する。"""
    error = _make_s3_error("AccessDenied", message="access denied")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    # stderr を取得する
    captured = capsys.readouterr()
    assert "S3 error occurred (code=AccessDenied): access denied" in captured.err


def test_handle_cli_error_exits_for_file_not_found_error(capsys):
    """FileNotFoundError のとき str(error) を stderr に書いて exit code 1 終了することを確認する。"""
    error = FileNotFoundError("DB file not found: /tmp/missing.db")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    # stderr を取得する
    captured = capsys.readouterr()
    assert "DB file not found: /tmp/missing.db" in captured.err


def test_handle_cli_error_exits_for_cli_usage_error(capsys):
    """CliUsageError のとき str(error) を stderr に書いて exit code 1 終了することを確認する。"""
    error = run.CliUsageError("invalid input")
    with pytest.raises(SystemExit) as exc_info:
        run.handle_cli_error(error, "my-bucket")
    assert exc_info.value.code == 1
    # stderr を取得する
    captured = capsys.readouterr()
    assert "invalid input" in captured.err


def test_handle_cli_error_reraises_value_error():
    """内部用 ValueError (Unknown mode / Unknown table name 等) はそのまま再送出することを確認する。

    CLI ユーザー入力エラーは CliUsageError で別経路に分離している。
    """
    error = ValueError("Unknown mode: invalid")
    with pytest.raises(ValueError, match="Unknown mode"):
        run.handle_cli_error(error, "my-bucket")


def test_handle_cli_error_reraises_unknown_error():
    """未知のエラータイプはそのまま再送出することを確認する。"""
    error = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        run.handle_cli_error(error, "my-bucket")


# prepare_db_for_init


def test_prepare_db_for_init_renames_broken_db_file(tmp_path):
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


def test_prepare_db_for_init_raises_on_permission_denied(tmp_path):
    """Permission denied のような破損ではない接続エラーは握りつぶさず再 raise することを確認する。

    握りつぶして return すると直後の is_initialized_db が同じパスへ再 connect して
    同じ例外を再発させ、ユーザーに二重出力を見せてしまうため、明示的に raise させる。
    破損ではないので退避ファイルも作られないことを併せて確認する。
    """
    db_path = tmp_path / "permission.db"
    wal_path = tmp_path / "permission.db.wal"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    wal_path.write_bytes(b"wal")
    os.chmod(db_path, 0)

    try:
        with pytest.raises(
            (duckdb.IOException, duckdb.InternalException, duckdb.FatalException)
        ):
            run.prepare_db_for_init(str(db_path))

        renamed_files = list(tmp_path.glob("permission.db.broken.*"))
        # Permission denied は破損 DB ではないため、退避ファイルは作られない
        assert len(renamed_files) == 0
        assert db_path.exists()
        assert wal_path.exists()
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)


# check_db_not_broken


def test_check_db_not_broken_returns_for_non_existent_db(tmp_path):
    """DB ファイルが存在しないときは何もせずに return することを確認する。"""
    missing = tmp_path / "missing.db"
    assert run.check_db_not_broken(str(missing)) is None


def test_check_db_not_broken_returns_for_healthy_db(tmp_path):
    """正常な DB に対しては何もせずに return することを確認する。"""
    db_path = tmp_path / "healthy.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    assert run.check_db_not_broken(str(db_path)) is None


def test_check_db_not_broken_exits_for_broken_db(tmp_path, capsys):
    """破損 DB に対しては自動退避せず exit_with_stderr で終了することを確認する。

    update / delete では運用者の判断を優先するため、検出のみ行い、メッセージで
    init の再実行を促す挙動を担保する。
    """
    db_path = tmp_path / "broken.db"
    db_path.write_bytes(b"invalid db")

    with pytest.raises(SystemExit) as exc_info:
        run.check_db_not_broken(str(db_path))
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "DB file is broken" in captured.err
    assert str(db_path) in captured.err
    assert "init" in captured.err
    # 退避ファイルは作られないことを確認する
    renamed_files = list(tmp_path.glob("broken.db.broken.*"))
    assert len(renamed_files) == 0


def test_check_db_not_broken_propagates_non_broken_errors(tmp_path):
    """Permission denied のような破損ではない接続エラーは再 raise することを確認する。"""
    db_path = tmp_path / "permission.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE t(id INTEGER)")
    os.chmod(db_path, 0)

    try:
        with pytest.raises(
            (duckdb.IOException, duckdb.InternalException, duckdb.FatalException)
        ):
            run.check_db_not_broken(str(db_path))
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)
