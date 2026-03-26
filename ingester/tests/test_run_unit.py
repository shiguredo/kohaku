import argparse
import datetime
import hashlib
import os
from types import SimpleNamespace

import duckdb
import pytest

import run


def test_positive_int_accepts_positive_value():
    """正の整数文字列を受け取った場合に整数へ変換することを確認する。"""
    assert run.positive_int("1") == 1


def test_positive_int_rejects_zero():
    """0 を渡した場合に引数エラーとなることを確認する。"""
    with pytest.raises(argparse.ArgumentTypeError, match="initial_maximum_load must be >= 1"):
        run.positive_int("0")


def test_sync_logs_raises_for_unknown_mode():
    """未知の実行モードを指定した場合に例外を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown mode"):
        run.sync_logs(con=None, client=None, args=None, mode="invalid")


def test_sync_log_for_update_skips_when_checkpoint_and_logs_are_missing():
    """更新対象が 1 件もない場合に作成処理と更新処理をスキップすることを確認する。"""
    class EmptyClient:
        @staticmethod
        def list_objects(_bucket, prefix=None, recursive=True):
            return []

    args = SimpleNamespace(s3_bucket="bucket", s3_prefix="log", initial_maximum_load=10)
    with duckdb.connect(":memory:") as con:
        run.create_s3_object_table(con)
        run.sync_log_for_update(con=con, client=EmptyClient(), args=args, target="rtc_stats")

        assert run.select_s3_object(con, "rtc_stats") is None
        assert run.table_exists(con, "rtc_stats") is False


def test_insert_log_from_s3_skips_when_no_newer_logs():
    """保存済みより新しいログがない場合に挿入も更新も行わないことを確認する。"""
    class FakeObject:
        def __init__(self, object_name, last_modified):
            self.object_name = object_name
            self.last_modified = last_modified

    class FixedClient:
        def __init__(self, objects):
            self._objects = objects

        def list_objects(self, _bucket, prefix=None, recursive=True):
            return self._objects

    now = datetime.datetime.now(datetime.timezone.utc)
    older = now - datetime.timedelta(seconds=1)

    with duckdb.connect(":memory:") as con:
        run.create_s3_object_table(con)
        con.execute(
            "INSERT INTO s3_objects(type, object_name, last_modified) VALUES (?, ?, ?)",
            ("rtc_stats", "log/rtc_stats/current.gz", now),
        )

        client = FixedClient(
            [
                FakeObject("log/rtc_stats/old.gz", older),
                FakeObject("log/rtc_stats/current.gz", now),
            ]
        )
        run.insert_log_from_s3(con, client, "rtc_stats", "bucket", "log")

        row = con.execute("SELECT object_name, last_modified FROM s3_objects WHERE type=?", ("rtc_stats",)).fetchone()
        assert row is not None
        assert row[0] == "log/rtc_stats/current.gz"
        assert row[1] == now


def test_delete_returns_without_copy_when_no_rows_deleted(tmp_path):
    """削除件数が 0 件の場合に DB コピー処理へ進まず元 DB が変化しないことを確認する。"""
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

    assert db_path.exists()
    assert after_stat.st_ino == before_stat.st_ino
    assert after_hash == before_hash
    assert os.path.exists(f"{db_path}.copy") is False
