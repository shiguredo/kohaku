import argparse
import hashlib
import os
from types import SimpleNamespace

import duckdb
import pytest

import run


def test_positive_int_rejects_zero():
    """0 を渡した場合に引数エラーとなることを確認する。"""
    with pytest.raises(argparse.ArgumentTypeError, match="initial_maximum_load must be >= 1"):
        run.positive_int("0")


def test_sync_logs_raises_for_unknown_mode():
    """未知の実行モードを指定した場合に例外を送出することを確認する。"""
    with pytest.raises(ValueError, match="Unknown mode"):
        run.sync_logs(con=None, client=None, args=None, mode="invalid")


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
