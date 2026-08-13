import datetime
import gzip
import io
import json
import os
import sys
import tracemalloc
import uuid
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import duckdb
import minio
import pytest
from minio.datatypes import Object
from minio.error import S3Error

from run import (
    DEFAULT_INITIAL_MAXIMUM_LOAD,
    collect_update_targets,
    delete,
    init,
    keep_latest_objects,
    update,
)

from .conftest import ACCESS_KEY, BUCKET, PREFIX, SECRET_KEY
from .helpers import full_args, wait_until

# 出力されたままのログファイルを保存するディレクトリ
LOG_DIR = "./tests/log"


def data_path(s3_prefix: str, tag: str, directory: str) -> str:
    """S3 のデータパスを生成する。"""
    filename = f"{uuid.uuid4()}.gz"
    return f"{s3_prefix}/{tag}/{directory}/{filename}"


def make_object(object_name: str, last_modified: datetime.datetime) -> Object:
    return Object("test-bucket", object_name, last_modified, "etag", 10)


def list_objects(
    s3_client: minio.Minio, bucket_name: str, prefix: str | None = None
) -> list[str]:
    """指定されたバケット内のオブジェクト名一覧を返す。prefix 未指定時は全件取得する。"""
    objects = s3_client.list_objects(bucket_name, prefix=prefix, recursive=True)
    return [obj.object_name for obj in objects]


def remove_objects(s3_client: minio.Minio, bucket_name: str) -> None:
    """指定されたバケット内のすべてのオブジェクトを削除する。"""
    objects = list_objects(s3_client, bucket_name)
    for obj in objects:
        s3_client.remove_object(bucket_name, obj)


def remove_bucket(s3_client: minio.Minio, bucket_name: str) -> None:
    """指定されたバケットを中身ごと削除する。"""
    remove_objects(s3_client, bucket_name)
    s3_client.remove_bucket(bucket_name)


def reset_bucket(s3_client: minio.Minio, bucket_name: str) -> None:
    """テスト開始前に既存バケットを掃除してから作り直す。

    session スコープの RustFS を function スコープの s3_client 系 fixture で共有して
    いるため、前テストの teardown (fixture 内の remove_bucket) が落ちて残骸が残った場合
    でも次のテストを空のバケットから始められるようにする。残骸を検出したときは黙って
    吸収せず stderr に警告を出し、teardown 失敗を見える化する。
    """
    if s3_client.bucket_exists(bucket_name):
        print(
            f"reset_bucket: テスト開始前にバケット {bucket_name} が残っていました。 "
            "前回テストの teardown が失敗した可能性があります。 "
            "本テストの開始前に削除して作り直します。",
            file=sys.stderr,
        )
        remove_bucket(s3_client, bucket_name)
    s3_client.make_bucket(bucket_name)


def update_timestamp_for_rtc_stats(
    con: duckdb.DuckDBPyConnection, obj: Sequence[Any], period: int
) -> None:
    """rtc_stats の timestamp を現在時刻から period 日だけ過去に更新する。"""
    org_timestamp, connection_id, rtc_id, rtc_type = obj

    now = datetime.datetime.now(datetime.UTC)
    timestamp = now - datetime.timedelta(days=period)

    # rtc_stats の timestamp を更新する
    con.execute(
        """
        UPDATE rtc_stats
        SET timestamp = ?
        WHERE connection_id = ? AND rtc_id = ? AND rtc_type = ? AND timestamp = ?
    """,
        (timestamp, connection_id, rtc_id, rtc_type, org_timestamp),
    )


def make_args_for_s3(
    duckdb_filepath: str,
    rustfs_endpoint: str,
    *,
    s3_bucket: str = BUCKET,
    initial_maximum_load: int = 1000,
    update_maximum_load: int = 1000,
) -> SimpleNamespace:
    """init / update テスト用 args を共通設定で組み立てる。

    各テストではこの関数を呼んで個別差分だけキーワード引数で上書きする。
    """
    return SimpleNamespace(
        **full_args(
            db=duckdb_filepath,
            s3_endpoint=rustfs_endpoint,
            s3_access_key_id=ACCESS_KEY,
            s3_secret_access_key=SECRET_KEY,
            s3_bucket=s3_bucket,
            s3_prefix=PREFIX,
            initial_maximum_load=initial_maximum_load,
            update_maximum_load=update_maximum_load,
        )
    )


def make_args_for_delete(
    duckdb_filepath: str,
    *,
    retention_period: int,
) -> SimpleNamespace:
    """delete テスト用 args を db と retention_period のみで組み立てる。"""
    return SimpleNamespace(
        **full_args(db=duckdb_filepath, retention_period=retention_period)
    )


def get_latest_object(s3_client: minio.Minio, bucket: str, prefix: str) -> Any:
    """オブジェクトストレージ上で最新のオブジェクトを返す。

    本番コードの list_objects と同じく (last_modified, object_name) を比較キーにする。
    last_modified が同値の場合に object_name で順序が決まるため、本番とテストで
    「最新」の定義を一致させる。
    """
    objects = s3_client.list_objects(bucket, prefix=prefix, recursive=True)
    return max(objects, key=lambda obj: (obj.last_modified, obj.object_name))


def _setup_fresh_bucket_client(rustfs_endpoint: str) -> minio.Minio:
    """RustFS 用の Minio client を作成し、 接続待ちと BUCKET の reset を行う。

    3 つの s3_client 系 fixture が共通で行うセットアップを 1 箇所に集約する。
    データ投入 (fixture ごとに違う) と teardown (remove_bucket) は各 fixture 側に残す。
    """
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    wait_until(lambda: client.list_buckets() is not None)
    reset_bucket(client, BUCKET)
    return client


@pytest.fixture
def s3_client(rustfs_endpoint: str) -> Iterator[minio.Minio]:
    """テスト用ログ (rtc_stats / session_webhook 両方) をアップロードした S3 クライアント。"""
    client = _setup_fresh_bucket_client(rustfs_endpoint)

    now = datetime.datetime.now(datetime.UTC)
    for root, _, filenames in os.walk(LOG_DIR):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            if os.path.isfile(file_path):
                # ログデータのパスを生成
                log_file_path = os.path.join(LOG_DIR, filename)
                # ファイル名からタグを取得
                tag = filename.split(".")[0]
                with open(log_file_path, "rb") as data:
                    for line in data:
                        directory = now.strftime("%Y/%m/%d")
                        s3_path = data_path(PREFIX, tag, directory)
                        # gzip 圧縮
                        compressed_log_data = gzip.compress(line)

                        # アップロード
                        client.put_object(
                            BUCKET,
                            s3_path,
                            io.BytesIO(compressed_log_data),
                            length=len(compressed_log_data),
                        )

    yield client
    remove_bucket(client, BUCKET)


@pytest.fixture
def s3_client_without_session_webhook(rustfs_endpoint: str) -> Iterator[minio.Minio]:
    """session_webhook を意図的に除外し、 rtc_stats のみアップロードした S3 クライアント。"""
    client = _setup_fresh_bucket_client(rustfs_endpoint)

    now = datetime.datetime.now(datetime.UTC)
    log_file_path = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(log_file_path, "rb") as data:
        for line in data:
            directory = now.strftime("%Y/%m/%d")
            s3_path = data_path(PREFIX, "rtc_stats", directory)
            # gzip 圧縮
            compressed_log_data = gzip.compress(line)
            # アップロード
            client.put_object(
                BUCKET,
                s3_path,
                io.BytesIO(compressed_log_data),
                length=len(compressed_log_data),
            )

    yield client
    remove_bucket(client, BUCKET)


@pytest.fixture
def s3_client_empty(rustfs_endpoint: str) -> Iterator[minio.Minio]:
    """ログオブジェクトを 1 件もアップロードしない空バケットを準備する S3 クライアント。"""
    client = _setup_fresh_bucket_client(rustfs_endpoint)
    yield client
    remove_bucket(client, BUCKET)


def test_default_initial_maximum_load():
    """DEFAULT_INITIAL_MAXIMUM_LOAD が複数 fluent-bit 運用のスケールに足る値であることを確認する。

    デフォルト値 1000 は 10 台構成で単一運用と同等の約 8 時間のカバーを実現する値であり、
    デフォルト 100 のままでは複数 fluent-bit 環境で初期取り込みのカバー範囲が
    想定より短くなるため、この値が意図せず変更されないことを検証する。
    """
    assert DEFAULT_INITIAL_MAXIMUM_LOAD == 1000


def test_keep_latest_objects_limits_memory():
    """keep_latest_objects が上限件数しか保持せず、降順で返すことを確認する。

    上限を超える数のオブジェクトを渡しても保持は上限件数に留まる (全件をメモリ展開
    しない) ことを、先頭が最新になる降順とあわせて検証する。
    """
    now = datetime.datetime.now(datetime.UTC)
    objects = [
        make_object(f"{i}.gz", now - datetime.timedelta(minutes=i)) for i in range(1000)
    ]

    kept = keep_latest_objects(iter(objects), 10)

    assert len(kept) == 10
    assert [obj.object_name for obj in kept] == [f"{i}.gz" for i in range(10)]


def test_keep_latest_objects_same_last_modified():
    """last_modified が同値のオブジェクトが object_name の辞書順降順で並ぶことを確認する。

    カーソル比較 (is_after_s3_cursor) のタプル辞書順と一致させるため、同値キーでは
    object_name の辞書順で並ぶ必要がある。
    """
    now = datetime.datetime.now(datetime.UTC)
    objects = [make_object(f"{i}.gz", now) for i in range(10)]

    kept = keep_latest_objects(iter(objects), 5)

    assert len(kept) == 5
    assert [obj.object_name for obj in kept] == ["9.gz", "8.gz", "7.gz", "6.gz", "5.gz"]


def test_keep_latest_objects_does_not_expand_all_objects():
    """keep_latest_objects が全件をメモリ展開しないことを確認する。

    全件を list 化してから切り詰める実装に退化するとピークメモリが全件分に膨らむ。
    2 万件の入力でピークメモリが上限件数相当に留まることを tracemalloc で検証する
    (全件展開では実験値で約 6 MB、 上限保持では数十 KB)。
    """
    now = datetime.datetime.now(datetime.UTC)
    object_count = 20000

    def generate_objects() -> Iterator[Object]:
        for i in range(object_count):
            yield make_object(f"{i}.gz", now - datetime.timedelta(minutes=i))

    tracemalloc.start()
    try:
        kept = keep_latest_objects(generate_objects(), 10)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(kept) == 10
    # 全件を list 化する実装は 2 万件分で 1 MB を超えるため、 閾値 1 MB で退化を検出する
    assert peak < 1024 * 1024


def test_collect_update_targets_does_not_expand_all_objects():
    """collect_update_targets が全件をメモリ展開しないことを確認する。

    カーソルより新しいオブジェクトを大量に走査しても、 保持されるのは最古
    update_maximum_load 件のみに留まることを tracemalloc で検証する (全件を list 化して
    切り詰める実装に退化するとピークメモリが全件分に膨らむ)。
    """
    now = datetime.datetime.now(datetime.UTC)
    cursor_key = (now, "cursor.gz")
    object_count = 20000

    def generate_objects() -> Iterator[Object]:
        for i in range(object_count):
            yield make_object(f"new-{i}.gz", now + datetime.timedelta(minutes=1 + i))

    tracemalloc.start()
    try:
        target_log_objects, same_last_modified_objects = collect_update_targets(
            generate_objects(), cursor_key, update_maximum_load=10
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(target_log_objects) == 10
    assert same_last_modified_objects == []
    # 全件を list 化する実装は 2 万件分で 1 MB を超えるため、 閾値 1 MB で退化を検出する
    assert peak < 1024 * 1024


def test_collect_update_targets_limits_memory():
    """collect_update_targets が全件を保持せず、 target 側のみ上限内のオブジェクトを保持することを確認する。

    カーソルより古い多数のオブジェクトとカーソルより新しい多数のオブジェクト、 および
    カーソルと同値の last_modified のオブジェクトを混在させ、 target 側の保持する
    オブジェクト数が上限を超えないこと (同値グループは設計上全件保持) を検証する。
    """
    now = datetime.datetime.now(datetime.UTC)
    cursor_key = (now, "cursor.gz")
    # カーソルより古い多数のオブジェクト (update の取り込み対象外)
    older = [
        make_object(f"older-{i}.gz", now - datetime.timedelta(minutes=60 + i))
        for i in range(5000)
    ]
    # カーソルより新しい多数のオブジェクト (うち古い側 update_maximum_load 件のみ保持)
    newer = [
        make_object(f"newer-{i}.gz", now + datetime.timedelta(minutes=1 + i))
        for i in range(30)
    ]
    # カーソルと同値の last_modified のオブジェクト (全件保持)
    same = [make_object(f"same-{i}.gz", now) for i in range(5)]
    # カーソル行自身 (same にも target にも属さない)
    cursor_object = make_object("cursor.gz", now)
    # カーソルと同値かつ辞書順がカーソルより小さいオブジェクト (same のみに属する)
    same_older_name = make_object("a-same.gz", now)

    target_log_objects, same_last_modified_objects = collect_update_targets(
        iter(older + newer + same + [cursor_object, same_older_name]),
        cursor_key,
        update_maximum_load=10,
    )

    # 古い側 10 件のみが昇順 (古い順) で保持される。 カーソルと同値の last_modified の
    # オブジェクトはカーソルより新しいため target にも属する
    assert [obj.object_name for obj in target_log_objects] == [
        "same-0.gz",
        "same-1.gz",
        "same-2.gz",
        "same-3.gz",
        "same-4.gz",
        "newer-0.gz",
        "newer-1.gz",
        "newer-2.gz",
        "newer-3.gz",
        "newer-4.gz",
    ]
    # カーソルと同値の last_modified のオブジェクトは全件保持される。 カーソル行自身
    # (cursor.gz) は除外され、 辞書順がカーソルより小さい a-same.gz も含まれる
    assert [obj.object_name for obj in same_last_modified_objects] == [
        "same-0.gz",
        "same-1.gz",
        "same-2.gz",
        "same-3.gz",
        "same-4.gz",
        "a-same.gz",
    ]


def test_collect_update_targets_no_candidate():
    """カーソルより新しいオブジェクトも同値グループも無い場合に空の 2 集合を返すことを確認する。"""
    now = datetime.datetime.now(datetime.UTC)
    cursor_key = (now, "cursor.gz")
    older = [
        make_object(f"older-{i}.gz", now - datetime.timedelta(minutes=60 + i))
        for i in range(100)
    ]

    target_log_objects, same_last_modified_objects = collect_update_targets(
        iter(older), cursor_key, update_maximum_load=10
    )

    assert target_log_objects == []
    assert same_last_modified_objects == []


def test_init(s3_client, rustfs_endpoint, tmp_path):
    """init 実行でログを取り込み、DuckDB とオブジェクトカーソルが作成されることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    init(args)

    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # カーソルの last_modified が最新オブジェクトと一致することを確認する
        latest_object = get_latest_object(
            s3_client, BUCKET, "/".join([PREFIX, "rtc_stats"])
        )
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?",
            (
                "rtc_stats",
                latest_object.last_modified,
            ),
        )
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] == 1


def test_re_init(s3_client, rustfs_endpoint, tmp_path):
    """init を再実行してもデータ件数とカーソル情報が変化しないことを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 1 回目の init
    init(args)
    assert os.path.exists(duckdb_filepath)

    # 1 回目の init 後の状態を検証する。検証用 connection を保持したまま 2 回目の
    # init を呼ぶと、init 内部の DB 接続挙動 (prepare_db_for_init や
    # has_s3_objects_table) との競合をテスト側で抱え込むことになるため、
    # 検証 → 一旦クローズ → 2 回目 init → 再検証 の順で組む。
    objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
    latest_object = get_latest_object(
        s3_client, BUCKET, "/".join([PREFIX, "rtc_stats"])
    )
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # カーソルの last_modified が最新オブジェクトと一致することを確認する
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?",
            (
                "rtc_stats",
                latest_object.last_modified,
            ),
        )
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] == 1

    # 2 回目の init はテスト側の DB 接続を閉じてから呼び出す。
    init(args)

    # 再実行後も内容が変わらないことを別 connection で確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が最初の init 実行後から変わらないことを確認する
        assert result is not None
        assert result[0] == len(objects)

        # 再実行後も、s3_objects の last_modified が変わらないことを確認する
        # 前回の実行時に取得した latest_object をそのまま利用する
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?",
            (
                "rtc_stats",
                latest_object.last_modified,
            ),
        )
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] == 1


def test_init_evacuates_leftover_wal_and_reinitializes(
    s3_client, rustfs_endpoint, tmp_path
):
    """DB 本体が無く .wal だけ残っている状態で init が .wal を退避して新規作成することを確認する。

    DB ファイルを削除した運用者が .wal を消し忘れた場合、 新規 DB 作成時に古い WAL が
    再生されて init が「already present」でスキップされる事故を防ぐため、 .wal を
    退避してから新規作成することを検証する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 1 回目の init で DB を作成する
    init(args)
    assert os.path.exists(duckdb_filepath)

    # DB 本体を削除して .wal だけ残す (運用者の消し忘れを再現)
    os.remove(duckdb_filepath)
    wal_filepath = f"{duckdb_filepath}.wal"
    with open(wal_filepath, "wb") as f:
        f.write(b"wal payload")

    # 2 回目の init は .wal を退避して新規作成する
    init(args)
    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        assert result[0] > 0

    # .wal が退避され、 元位置に残っていないこと
    assert not os.path.exists(wal_filepath)
    broken_files = list(tmp_path.glob("duck.db.broken.*"))
    assert len(broken_files) == 1


def test_file_count_limit_for_init(s3_client, rustfs_endpoint, tmp_path):
    """init の初期読み込み上限で取り込み件数が制限されることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    # 初期最大読み込み数を設定する
    initial_maximum_load = 50

    args = make_args_for_s3(
        duckdb_filepath, rustfs_endpoint, initial_maximum_load=initial_maximum_load
    )

    init(args)

    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数より少ないことを確認する
        assert result[0] < len(objects)
        assert result[0] == initial_maximum_load

        # カーソルの last_modified が最新オブジェクトと一致することを確認する
        latest_object = get_latest_object(
            s3_client, BUCKET, "/".join([PREFIX, "rtc_stats"])
        )
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?",
            ("rtc_stats", latest_object.last_modified),
        )
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] == 1


def test_update(s3_client, rustfs_endpoint, tmp_path):
    """update 実行時に差分ログのみが追加され、件数とカーソルが更新されることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 初期 init
    init(args)
    assert os.path.exists(duckdb_filepath)

    # init / update の呼び出しと検証用 connection を交差させないため、検証ごとに
    # with duckdb.connect(...) を独立させる。こうしておくと init / update 内部の DB
    # 接続挙動が変わってもテスト側が暗黙の前提に依存しない。
    objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

    # log データに変化がないため、update を呼び出してもデータ数が変わらないことを確認する
    update(args)
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が変わらないことを確認する
        assert result is not None
        assert result[0] == len(objects)

    # 新規の log データを RustFS に追加した後に update を呼び出して、データ数が増えることを確認する
    # (既存行をそのまま再 put すると natural key の PK で重複吸収されるため、 connection_id を
    # 変更して一意な行として put する)
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        for line in data:
            parsed_log = json.loads(line)
            parsed_log["connection_id"] = f"{parsed_log['connection_id']}-update"
            now = datetime.datetime.now(datetime.UTC)
            log_data = json.dumps(parsed_log).encode("utf-8")
            compressed_log_data = gzip.compress(log_data)

            directory = now.strftime("%Y/%m/%d")
            s3_path = data_path(PREFIX, "rtc_stats", directory)
            # アップロード
            s3_client.put_object(
                BUCKET,
                s3_path,
                io.BytesIO(compressed_log_data),
                length=len(compressed_log_data),
            )

    # update を呼び出して、データ数が増えることを確認する
    update(args)
    objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
    latest_object = get_latest_object(
        s3_client, BUCKET, "/".join([PREFIX, "rtc_stats"])
    )
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が増えていることを確認する
        assert result is not None
        assert result[0] > 0
        assert result[0] == len(objects)

        # カーソルの last_modified が最新オブジェクトと一致することを確認する
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?",
            (
                "rtc_stats",
                latest_object.last_modified,
            ),
        )
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] == 1


@pytest.mark.parametrize(
    ("broken_payload", "expected_exc_name"),
    [
        pytest.param(
            b"this is not valid gzip data",
            "IOException",
            id="broken-gzip",
        ),
        pytest.param(
            gzip.compress(b"this is not valid json{invalid"),
            "InvalidInputException",
            id="malformed-json",
        ),
        pytest.param(
            gzip.compress(
                json.dumps(
                    {"timestamp": "2025-01-01T00:00:00+09:00", "req": {}}
                ).encode("utf-8")
            ),
            "ConstraintException",
            id="missing-primary-key",
        ),
    ],
)
def test_update_skips_broken_object_and_continues_other_targets(
    s3_client, rustfs_endpoint, tmp_path, capsys, broken_payload, expected_exc_name
):
    """壊れた session_webhook オブジェクトがあっても update が完走することを確認する。

    sync_log_for_update が捕捉する両例外型で、 壊れた target があっても他の target への
    波及を防ぐことを確認する。 あわせて insert_log_from_s3 の rollback により、
    壊れたオブジェクトのカーソル更新が残らないことを session_webhook カーソルで検証する。
    - broken-gzip: gzip として復号できない生バイト列を投入すると DuckDB は IOException を送出する。
    - malformed-json: 有効な gzip 内に JSON parse できないバイト列を投入すると DuckDB は InvalidInputException を送出する。
    - missing-primary-key: PK カラム (id) が欠落した JSON 行を投入すると DuckDB は ConstraintException を送出する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 初期 init で rtc_stats / session_webhook の両方が取り込まれる。
    init(args)

    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        initial_rtc_count = result[0]
        con.execute("SELECT COUNT(*) FROM session_webhook")
        result = con.fetchone()
        assert result is not None
        initial_webhook_count = result[0]
        # rollback 検証用に、update 前の session_webhook カーソルを保持しておく。
        con.execute(
            "SELECT last_modified, object_name FROM s3_objects WHERE type='session_webhook'"
        )
        initial_webhook_cursor = con.fetchone()
        assert initial_webhook_cursor is not None

    # 壊れた session_webhook オブジェクトを S3 に配置する。
    now = datetime.datetime.now(datetime.UTC)
    directory = now.strftime("%Y/%m/%d")
    s3_client.put_object(
        BUCKET,
        data_path(PREFIX, "session_webhook", directory),
        io.BytesIO(broken_payload),
        length=len(broken_payload),
    )

    # 正常な rtc_stats オブジェクトも同時に配置する。
    # (既存行をそのまま再 put すると natural key の PK で重複吸収されるため、 connection_id を
    # 変更して一意な行として put する)
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        first_line = data.readline()
    parsed_log = json.loads(first_line)
    parsed_log["connection_id"] = f"{parsed_log['connection_id']}-broken-test"
    compressed = gzip.compress(json.dumps(parsed_log).encode("utf-8"))
    s3_client.put_object(
        BUCKET,
        data_path(PREFIX, "rtc_stats", directory),
        io.BytesIO(compressed),
        length=len(compressed),
    )

    # update は壊れた session_webhook で例外を投げず完走することを確認する。
    update(args)

    # rtc_stats は新規オブジェクトが取り込まれ件数が増え、session_webhook は変わらない。
    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        assert result[0] == initial_rtc_count + 1
        con.execute("SELECT COUNT(*) FROM session_webhook")
        result = con.fetchone()
        assert result is not None
        assert result[0] == initial_webhook_count
        # insert_log_from_s3 の rollback が効いていれば、壊れたオブジェクトのカーソル更新は
        # 残らず、session_webhook カーソルは init 時点のままになる。カーソル更新だけが
        # 残ってしまう実装に戻ると、ここで更新済みカーソルが検出される。
        con.execute(
            "SELECT last_modified, object_name FROM s3_objects WHERE type='session_webhook'"
        )
        assert con.fetchone() == initial_webhook_cursor

    # stderr に catch 対象例外型 (session_webhook) が出力されていることを確認する。
    captured = capsys.readouterr()
    assert f"{expected_exc_name} (session_webhook):" in captured.err


@pytest.mark.parametrize(
    ("broken_payload", "expected_exc_name"),
    [
        pytest.param(
            b"this is not valid gzip data",
            "IOException",
            id="broken-gzip",
        ),
        pytest.param(
            gzip.compress(b"this is not valid json{invalid"),
            "InvalidInputException",
            id="malformed-json",
        ),
        pytest.param(
            gzip.compress(
                json.dumps(
                    {"timestamp": "2025-01-01T00:00:00+09:00", "req": {}}
                ).encode("utf-8")
            ),
            "ConstraintException",
            id="missing-primary-key",
        ),
    ],
)
def test_init_skips_broken_object_and_continues_other_targets(
    s3_client_without_session_webhook,
    rustfs_endpoint,
    tmp_path,
    capsys,
    broken_payload,
    expected_exc_name,
):
    """壊れた session_webhook オブジェクトがあっても init が完走することを確認する。

    sync_log_for_init が捕捉する両例外型で、 壊れた target があっても他の target への
    波及を防ぎ、 rtc_stats は取り込まれることを確認する。
    test_update_skips_broken_object_and_continues_other_targets と同構造の init 版で、
    sync_log_for_update 側だけカバーされていた挙動の非対称を解消する。
    - broken-gzip: gzip として復号できない生バイト列を投入すると DuckDB は IOException を送出する。
    - malformed-json: 有効な gzip 内に JSON parse できないバイト列を投入すると DuckDB は InvalidInputException を送出する。
    - missing-primary-key: PK カラム (id) が欠落した JSON 行を投入すると DuckDB は ConstraintException を送出する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")

    # s3_client_without_session_webhook は rtc_stats のみアップロード済み。
    # そこに壊れた session_webhook オブジェクトを追加投入する。
    now = datetime.datetime.now(datetime.UTC)
    directory = now.strftime("%Y/%m/%d")
    s3_client_without_session_webhook.put_object(
        BUCKET,
        data_path(PREFIX, "session_webhook", directory),
        io.BytesIO(broken_payload),
        length=len(broken_payload),
    )

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # init は壊れた session_webhook で例外を投げず完走することを確認する。
    init(args)

    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as con:
        # rtc_stats は正常に取り込まれる。
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        assert result[0] > 0

        # session_webhook は create_log_table で例外が発生し、sync_log_for_init が捕捉して
        # rollback するため、テーブル自体が作成されない。
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'"
        )
        result = con.fetchone()
        assert result is not None
        assert result[0] == 0

        # initialize_log_table の rollback が effective なら、テーブルと同時にカーソル
        # 登録も巻き戻り、s3_objects の session_webhook 行は入らない。「create_log_table
        # 失敗 → update_s3_objects_table スキップ」 だけの実装に戻ると、テーブル未作成
        # なのに s3_objects にカーソル行だけ残る非対称状態が検出される
        # (test_update_skips_broken_object_and_continues_other_targets の
        # initial_webhook_cursor 検証と対称)。
        con.execute("SELECT COUNT(*) FROM s3_objects WHERE type='session_webhook'")
        result = con.fetchone()
        assert result is not None
        assert result[0] == 0

    # stderr に catch 対象例外型 (session_webhook) が出力されていることを確認する。
    captured = capsys.readouterr()
    assert f"{expected_exc_name} (session_webhook):" in captured.err


def test_all_delete(s3_client, rustfs_endpoint, tmp_path):
    """保持期間外のデータだけで構成された場合に delete で全件削除されることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    init(args)
    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # DuckDB のオブジェクトを取得する
        duckdb_connection.execute(
            "SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats"
        )
        objects = duckdb_connection.fetchall()
        # すべてのオブジェクトの timestamp を 2 日前に更新する
        for obj in objects:
            update_timestamp_for_rtc_stats(duckdb_connection, obj, 2)

    # delete 関数を呼び出すための引数を設定
    # retention_period を 1 日に設定して、2 日前のデータが削除されることを確認する
    # delete は内部で同じファイルを ATTACH するため、duckdb_connection を閉じてから呼び出す
    args = make_args_for_delete(duckdb_filepath, retention_period=1)
    delete(args)

    # delete 後のデータ件数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # 全てのオブジェクトの timestamp を 2 日前に更新したため、全てのデータが削除される
        assert result[0] == 0


def test_delete(s3_client, rustfs_endpoint, tmp_path):
    """保持期間外と期間内が混在する場合に delete で期間外のみ削除されることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    init(args)
    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # DuckDB のオブジェクトを取得する
        duckdb_connection.execute(
            "SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats"
        )
        objects = duckdb_connection.fetchall()
        # オブジェクトの半数の timestamp を 2 日前に更新する
        for i, obj in enumerate(objects):
            if i % 2 == 0:
                # 偶数番目のオブジェクトは 2 日前に更新
                update_timestamp_for_rtc_stats(duckdb_connection, obj, 2)
            else:
                # 奇数番目のオブジェクトは今の日時に更新
                update_timestamp_for_rtc_stats(duckdb_connection, obj, 0)

    # delete 関数を呼び出すための引数を設定
    # retention_period を 1 日に設定して、2 日前のデータが削除されることを確認する
    # delete は内部で同じファイルを ATTACH するため、duckdb_connection を閉じてから呼び出す
    args = make_args_for_delete(duckdb_filepath, retention_period=1)
    delete(args)

    # delete 後のデータ件数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # 偶数番目のオブジェクトの timestamp を 2 日前に更新したため、半分のデータが残る
        assert result[0] == len(objects) // 2


def test_delete_within_retention_period(s3_client, rustfs_endpoint, tmp_path):
    """保持期間外と期間内が混在する場合でも、 retention_period を長くとるとどの行も削除対象にならないことを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    init(args)
    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS のログオブジェクトが DuckDB に 1 件以上取り込まれていることを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # DuckDB のオブジェクトを取得する
        duckdb_connection.execute(
            "SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats"
        )
        objects = duckdb_connection.fetchall()
        # オブジェクトの半数の timestamp を 2 日前に更新する
        for i, obj in enumerate(objects):
            if i % 2 == 0:
                # 偶数番目のオブジェクトは 2 日前に更新
                update_timestamp_for_rtc_stats(duckdb_connection, obj, 2)
            else:
                # 奇数番目のオブジェクトは今の日時に更新
                update_timestamp_for_rtc_stats(duckdb_connection, obj, 0)

    # delete 関数を呼び出すための引数を設定
    # retention_period を 3 日に設定して、対象のオブジェクトがないため、データが削除されないことを確認する
    # delete は内部で同じファイルを ATTACH するため、duckdb_connection を閉じてから呼び出す
    args = make_args_for_delete(duckdb_filepath, retention_period=3)
    delete(args)

    # delete 後のデータ件数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # データの保持期間が 3 日のため、データは削除されない
        assert result[0] == len(objects)


def test_no_bucket(rustfs_endpoint, tmp_path):
    """RustFS のバケットが存在しない場合に init が S3Error(NoSuchBucket) を送出することを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    # S3 バケット名規約に従いつつ、RustFS に存在しないバケット名を指定する。
    # 他テストが偶発的に同名バケットを作って偽通過するのを防ぐため uuid で一意化する。
    non_existent_bucket = f"non-existent-{uuid.uuid4().hex[:8]}"
    args = make_args_for_s3(
        duckdb_filepath, rustfs_endpoint, s3_bucket=non_existent_bucket
    )

    with pytest.raises(S3Error) as exc_info:
        init(args)
    assert exc_info.value.code == "NoSuchBucket"


def test_init_skips_missing_session_webhook(
    s3_client_without_session_webhook, rustfs_endpoint, tmp_path
):
    """session_webhook が S3 に存在しない場合でも init が成功し、rtc_stats のみ取り込まれることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client_without_session_webhook.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # session_webhook が欠損していても init が例外を送出しないことを確認する
    init(args)

    assert os.path.exists(duckdb_filepath)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        # rtc_stats は正常に取り込まれること
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        assert result[0] > 0

        # S3 に存在しない session_webhook のテーブルは作成されないこと
        duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'"
        )
        table_count = duckdb_connection.fetchone()
        assert table_count is not None
        assert table_count[0] == 0

        # rtc_stats のみ取り込まれるため、カーソルテーブルも 1 行のみであること
        duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects")
        cursor_count = duckdb_connection.fetchone()
        assert cursor_count is not None
        assert cursor_count[0] == 1


def test_init_and_update_on_empty_bucket(s3_client_empty, rustfs_endpoint, tmp_path):
    """全ターゲットが空のバケットに対して init/update がエラーなく完走し、データテーブルが作成されないことを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client_empty.bucket_exists(BUCKET)

    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 空バケットでも init は完走し、DB ファイルが作成される
    init(args)
    assert os.path.exists(duckdb_filepath)

    def assert_only_s3_objects_table(con: duckdb.DuckDBPyConnection) -> None:
        """LOG_TARGETS のテーブルは作成されず、s3_objects のみ存在することを確認する。"""
        for target in ("rtc_stats", "session_webhook"):
            con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?",
                (target,),
            )
            table_count = con.fetchone()
            assert table_count is not None
            assert table_count[0] == 0, f"{target} テーブルが作成されてはならない"
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='s3_objects'"
        )
        s3_objects_table_count = con.fetchone()
        assert s3_objects_table_count is not None
        assert s3_objects_table_count[0] == 1
        # オブジェクトが 1 件もないため、カーソル行も入らない
        con.execute("SELECT COUNT(*) FROM s3_objects")
        cursor_count = con.fetchone()
        assert cursor_count is not None
        assert cursor_count[0] == 0

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        assert_only_s3_objects_table(duckdb_connection)

    # 空バケットのまま update を呼んでもエラーにならず、データテーブルも作成されない
    update(args)

    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        assert_only_s3_objects_table(duckdb_connection)


def test_update_maximum_load_splits_batches(s3_client, rustfs_endpoint, tmp_path):
    """update_maximum_load より多い新規ログを 1 回の update で取り込まず、複数回呼び出しで取り込み切ることを確認する。"""
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    # 既存ログをすべて取り込んでカーソルを最新に揃える
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)
    init(args)

    def fetch_rtc_stats_count() -> int:
        """rtc_stats の現在の件数を取得する。"""
        with duckdb.connect(duckdb_filepath) as con:
            con.execute("SELECT COUNT(*) FROM rtc_stats")
            result = con.fetchone()
            assert result is not None
            return result[0]

    # init で 1 件以上は取り込まれている前提
    initial_count = fetch_rtc_stats_count()
    assert initial_count > 0

    # 新規ログを 5 件追加する
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        new_lines = data.readlines()[:5]
    assert len(new_lines) == 5

    new_s3_paths = []
    for line in new_lines:
        # 既存行をそのまま再 put すると natural key の PK で重複吸収されるため、
        # connection_id を変更して一意な行として put する
        parsed_log = json.loads(line)
        parsed_log["connection_id"] = f"{parsed_log['connection_id']}-batch"
        now = datetime.datetime.now(datetime.UTC)
        directory = now.strftime("%Y/%m/%d")
        s3_path = data_path(PREFIX, "rtc_stats", directory)
        new_s3_paths.append(s3_path)
        compressed_log_data = gzip.compress(json.dumps(parsed_log).encode("utf-8"))
        s3_client.put_object(
            BUCKET,
            s3_path,
            io.BytesIO(compressed_log_data),
            length=len(compressed_log_data),
        )

    # update_maximum_load=2 で update を呼び出すと、1 回あたり最大 2 件しか取り込まれない
    batch_args = make_args_for_s3(
        duckdb_filepath, rustfs_endpoint, update_maximum_load=2
    )

    # 1 回目: 2 件取り込まれること
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 2

    # カーソルがバッチ内最新 (古い順 2 件のうち新しい側) に進むことを確認する。
    # バッチ内最古に進む実装に退化すると、この検証で検出される。
    # 期待カーソルは S3 上の実 last_modified から求める (put 順とは独立)
    new_objects = [
        obj
        for obj in s3_client.list_objects(
            BUCKET, prefix=f"{PREFIX}/rtc_stats/", recursive=True
        )
        if obj.object_name in new_s3_paths
    ]
    assert len(new_objects) == 5
    expected_cursor = sorted(
        new_objects, key=lambda obj: (obj.last_modified, obj.object_name)
    )[1]
    with duckdb.connect(duckdb_filepath) as con:
        con.execute(
            "SELECT object_name, last_modified FROM s3_objects WHERE type='rtc_stats'"
        )
        cursor = con.fetchone()
    assert cursor is not None
    assert cursor == (expected_cursor.object_name, expected_cursor.last_modified)

    # 2 回目: さらに 2 件取り込まれて合計 4 件追加
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 4

    # 3 回目: 残り 1 件取り込まれて合計 5 件追加
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 5

    # 4 回目: 取り込むものが無いため件数が変わらない
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 5


def test_update_maximum_load_one_takes_single_object_per_call(
    s3_client, rustfs_endpoint, tmp_path
):
    """update_maximum_load=1 (positive_int の最小値) で 1 回あたり 1 件ずつ取り込むことを確認する。

    collect_update_targets が保持する最古側 1 件のみを 1 回の update で取り込む境界値で、
    残件が複数あっても 1 件だけ取り込み、複数回呼び出しで取り込み切ることを確認する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")

    assert s3_client.bucket_exists(BUCKET)

    # 既存ログをすべて取り込んでカーソルを最新に揃える
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)
    init(args)

    def fetch_rtc_stats_count() -> int:
        """rtc_stats の現在の件数を取得する。"""
        with duckdb.connect(duckdb_filepath) as con:
            con.execute("SELECT COUNT(*) FROM rtc_stats")
            result = con.fetchone()
            assert result is not None
            return result[0]

    # init で 1 件以上は取り込まれている前提
    initial_count = fetch_rtc_stats_count()
    assert initial_count > 0

    # 新規ログを 3 件追加する
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        new_lines = data.readlines()[:3]
    assert len(new_lines) == 3

    for line in new_lines:
        # 既存行をそのまま再 put すると natural key の PK で重複吸収されるため、
        # connection_id を変更して一意な行として put する
        parsed_log = json.loads(line)
        parsed_log["connection_id"] = f"{parsed_log['connection_id']}-batch"
        now = datetime.datetime.now(datetime.UTC)
        directory = now.strftime("%Y/%m/%d")
        s3_path = data_path(PREFIX, "rtc_stats", directory)
        compressed_log_data = gzip.compress(json.dumps(parsed_log).encode("utf-8"))
        s3_client.put_object(
            BUCKET,
            s3_path,
            io.BytesIO(compressed_log_data),
            length=len(compressed_log_data),
        )

    # update_maximum_load=1 で update を呼び出すと、1 回あたり 1 件しか取り込まれない
    batch_args = make_args_for_s3(
        duckdb_filepath, rustfs_endpoint, update_maximum_load=1
    )

    # 1 回目: 1 件取り込まれること
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 1

    # 2 回目: さらに 1 件取り込まれて合計 2 件追加
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 2

    # 3 回目: 残り 1 件取り込まれて合計 3 件追加
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 3

    # 4 回目: 取り込むものが無いため件数が変わらない
    update(batch_args)
    assert fetch_rtc_stats_count() == initial_count + 3


def test_update_ingests_same_last_modified_object(s3_client, rustfs_endpoint, tmp_path):
    """カーソルと同値の last_modified を持つオブジェクトが、カーソルより辞書順が小さくても取り込まれることを確認する。

    カーソル通過後に同値の last_modified で現れたオブジェクトは、 旧実装では is_after_s3_cursor
    (タプル辞書順比較) で False になり永久脱落する。 本テストは s3_objects のカーソル行の書き換えで
    「カーソルが同値グループの辞書順最大に進んだ状態」をタイミングの偶然に依存せずに再現し、
    同値グループの再走査でオブジェクトが取り込まれることを検証する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # init で fixture の全行を取り込む
    init(args)

    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        initial_count = result[0]
    assert initial_count > 0

    # カーソルと同値の last_modified になる新規オブジェクトを put する
    # (既存行と重複しないよう connection_id を変更する)
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        line = data.readline()
    parsed_log = json.loads(line)
    parsed_log["connection_id"] = f"{parsed_log['connection_id']}-same-lm"
    compressed = gzip.compress(json.dumps(parsed_log).encode("utf-8"))
    now = datetime.datetime.now(datetime.UTC)
    directory = now.strftime("%Y/%m/%d")
    s3_client.put_object(
        BUCKET,
        data_path(PREFIX, "rtc_stats", directory),
        io.BytesIO(compressed),
        length=len(compressed),
    )

    # put したオブジェクトの last_modified を取得する (S3 上の最新オブジェクト)
    objects = list(
        s3_client.list_objects(BUCKET, prefix=f"{PREFIX}/rtc_stats/", recursive=True)
    )
    new_obj = max(objects, key=lambda obj: (obj.last_modified, obj.object_name))

    # カーソルを「新規オブジェクトと同値の last_modified の辞書順最大」に書き換える
    with duckdb.connect(duckdb_filepath) as con:
        con.execute(
            "UPDATE s3_objects SET last_modified=?, object_name=? WHERE type=?",
            (new_obj.last_modified, "zzzzzzzzzzzzzzzzzzzz.gz", "rtc_stats"),
        )

    # update で同値グループの再走査により新規オブジェクトが取り込まれる
    update(args)

    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        result = result[0]
    assert result == initial_count + 1


def test_update_deduplicates_retransmitted_object(
    s3_client_empty, rustfs_endpoint, tmp_path
):
    """fluent-bit の再送 (同一 event の別 UUID put) で重複行が 1 行のみになることを確認する。

    PK 制約 + INSERT ... ON CONFLICT DO NOTHING により、 再送された同一 natural key の行が
    吸収されることを、 クロスバッチ手順 (U1 取り込み → U2 put → update) で検証する。
    """
    duckdb_filepath = str(tmp_path / "duck.db")
    args = make_args_for_s3(duckdb_filepath, rustfs_endpoint)

    # 空バケットで init する (オブジェクトが無いため LOG_TARGETS テーブルは作られない)
    init(args)

    # 同一 event を 2 つの UUID で put する (同一 last_modified になった場合のカーソル比較に
    # 備えて辞書順 U1 < U2 を固定する)
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        line = data.readline()
    compressed = gzip.compress(line)
    now = datetime.datetime.now(datetime.UTC)
    directory = now.strftime("%Y/%m/%d")
    s3_path_u1 = f"{PREFIX}/rtc_stats/{directory}/a.gz"
    s3_path_u2 = f"{PREFIX}/rtc_stats/{directory}/b.gz"
    s3_client_empty.put_object(
        BUCKET,
        s3_path_u1,
        io.BytesIO(compressed),
        length=len(compressed),
    )

    # U1 を取り込む (初登場 target のため initialize_log_table が走る)
    update(args)
    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        count_after_u1 = result[0]
    assert count_after_u1 == 1

    # U2 (同一 event の別 UUID) を put して update する
    s3_client_empty.put_object(
        BUCKET,
        s3_path_u2,
        io.BytesIO(compressed),
        length=len(compressed),
    )
    update(args)

    with duckdb.connect(duckdb_filepath) as con:
        con.execute("SELECT COUNT(*) FROM rtc_stats")
        result = con.fetchone()
        assert result is not None
        result = result[0]
    # 重複行は PK 制約で吸収され 1 行のみ
    assert result == 1
