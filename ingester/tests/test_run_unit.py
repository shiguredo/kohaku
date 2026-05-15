import argparse
import datetime
import hashlib
import os
from types import SimpleNamespace

import duckdb
import pytest

import run


def test_positive_int_rejects_zero():
    """0 を渡した場合に引数エラーとなることを確認する。"""
    with pytest.raises(argparse.ArgumentTypeError, match="value must be >= 1"):
        run.positive_int("0")


def test_sync_logs_raises_for_unknown_mode():
    """未知の実行モードを指定した場合に例外を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown mode"):
        run.sync_logs(con=None, client=None, args=None, mode="invalid")


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


# require_s3_credentials


def test_require_s3_credentials_rejects_missing_access_key():
    """access_key が未指定の場合に ValueError を送出することを確認する。"""
    args = SimpleNamespace(s3_access_key_id=None, s3_secret_access_key="secret")
    with pytest.raises(ValueError, match="S3 credentials are required"):
        run.require_s3_credentials(args)


def test_require_s3_credentials_rejects_missing_secret():
    """secret が未指定の場合に ValueError を送出することを確認する。"""
    args = SimpleNamespace(s3_access_key_id="access", s3_secret_access_key=None)
    with pytest.raises(ValueError, match="S3 credentials are required"):
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


# delete_log_by_timestamp の table_name 許可リスト検証


def test_delete_log_by_timestamp_rejects_unknown_table():
    """LOG_TARGETS 外のテーブル名を渡すと ValueError を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown table name"):
        run.delete_log_by_timestamp(con=None, table_name="evil_table", timestamp=None)


def test_delete_log_by_timestamp_rejects_sql_injection_attempt():
    """SQL インジェクションを試みる文字列も許可リストではじかれることを確認する。"""
    with pytest.raises(ValueError, match="Unknown table name"):
        run.delete_log_by_timestamp(
            con=None,
            table_name="rtc_stats; DROP TABLE x",
            timestamp=None,
        )


def test_delete_log_by_timestamp_rejects_empty_table_name():
    """空文字のテーブル名も許可リストではじかれることを確認する。"""
    with pytest.raises(ValueError, match="Unknown table name"):
        run.delete_log_by_timestamp(con=None, table_name="", timestamp=None)


# is_after_s3_cursor の比較ロジック


def test_is_after_s3_cursor_newer_last_modified():
    """last_modified がカーソルより新しければ True となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    obj = SimpleNamespace(last_modified=t_new, object_name="a")
    assert run.is_after_s3_cursor(obj, t_old, "a") is True


def test_is_after_s3_cursor_older_last_modified():
    """last_modified がカーソルより古ければ False となることを確認する。"""
    t_old = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    t_new = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    obj = SimpleNamespace(last_modified=t_old, object_name="z")
    assert run.is_after_s3_cursor(obj, t_new, "a") is False


def test_is_after_s3_cursor_same_last_modified_newer_object_name():
    """last_modified が同値なら object_name が大きい方を新しいと判定することを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    obj = SimpleNamespace(last_modified=t, object_name="b")
    assert run.is_after_s3_cursor(obj, t, "a") is True


def test_is_after_s3_cursor_same_last_modified_older_object_name():
    """last_modified が同値で object_name が小さい場合は False となることを確認する。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    obj = SimpleNamespace(last_modified=t, object_name="a")
    assert run.is_after_s3_cursor(obj, t, "b") is False


def test_is_after_s3_cursor_same_last_modified_same_object_name():
    """last_modified と object_name の両方が同値なら False となることを確認する (カーソル自身を除外)。"""
    t = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    obj = SimpleNamespace(last_modified=t, object_name="a")
    assert run.is_after_s3_cursor(obj, t, "a") is False
