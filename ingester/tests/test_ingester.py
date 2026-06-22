import datetime
import gzip
import io
import json
import os
import sys
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import duckdb
import minio
import pytest
from minio.error import S3Error

from run import delete, init, update

from .conftest import ACCESS_KEY, BUCKET, PREFIX, SECRET_KEY
from .helpers import wait_until

# 出力されたままのログファイルを保存するディレクトリ
LOG_DIR = "./tests/log"


class Args:
    """ingester/src/run.py が受け取る argparse.Namespace を模した DTO。

    テストから init / update / delete を直接呼び出すために、本番で argparse が組み立てる
    Namespace と同じ属性名を揃えてある。各サブコマンドが参照するフィールドの組は異なる
    (例: delete は db と retention_period のみ参照する) ため、すべて Optional にしてある。
    """

    def __init__(
        self,
        db: str | None = None,
        s3_endpoint: str | None = None,
        s3_access_key_id: str | None = None,
        s3_secret_access_key: str | None = None,
        s3_use_ssl: bool | None = None,
        s3_region: str | None = None,
        s3_bucket: str | None = None,
        s3_prefix: str | None = None,
        retention_period: int | None = None,
        initial_maximum_load: int | None = None,
        update_maximum_load: int | None = None,
    ) -> None:
        self.db = db
        self.s3_endpoint = s3_endpoint
        self.s3_access_key_id = s3_access_key_id
        self.s3_secret_access_key = s3_secret_access_key
        self.s3_use_ssl = s3_use_ssl
        self.s3_region = s3_region
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self.retention_period = retention_period
        self.initial_maximum_load = initial_maximum_load
        self.update_maximum_load = update_maximum_load


def data_path(s3_prefix: str, tag: str, directory: str) -> str:
    """S3 のデータパスを生成する。"""
    filename = f"{uuid.uuid4()}.gz"
    return f"{s3_prefix}/{tag}/{directory}/{filename}"


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

    セッションスコープの RustFS を function スコープの s3_client 系 fixture で共有して
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


# rtc_stats の timestamp を period 日だけ過去にずらすヘルパー関数
def update_timestamp_for_rtc_stats(
    con: duckdb.DuckDBPyConnection, obj: Sequence[Any], period: int
) -> None:
    """rtc_stats の timestamp を現在時刻から period 日だけ過去に更新する。"""
    org_timestamp, connection_id, rtc_id, rtc_type = obj

    now = datetime.datetime.now(datetime.UTC)
    # 指定された期間だけ過去に更新
    timestamp = now - datetime.timedelta(days=period)

    # 更新日時を更新するクエリを実行
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
    initial_maximum_load: int | None = 1000,
    update_maximum_load: int | None = 1000,
) -> Args:
    """init / update テスト用 Args を共通設定で組み立てる。

    各テストではこの関数を呼んで個別差分だけキーワード引数で上書きする。
    """
    return Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=s3_bucket,
        s3_prefix=PREFIX,
        initial_maximum_load=initial_maximum_load,
        update_maximum_load=update_maximum_load,
    )


def make_args_for_delete(
    duckdb_filepath: str,
    *,
    retention_period: int,
) -> Args:
    """delete テスト用 Args を db と retention_period のみで組み立てる。"""
    return Args(
        db=duckdb_filepath,
        retention_period=retention_period,
    )


def get_latest_object(s3_client: minio.Minio, bucket: str, prefix: str) -> Any:
    """オブジェクトストレージ上で最新のオブジェクトを返す。

    本番コードの list_objects と同じく (last_modified, object_name) を比較キーにする。
    last_modified が同値の場合に object_name で順序が決まるため、本番とテストで
    「最新」の定義を一致させる。
    """
    objects = s3_client.list_objects(bucket, prefix=prefix, recursive=True)
    return max(objects, key=lambda obj: (obj.last_modified, obj.object_name))


@pytest.fixture
def s3_client(rustfs_endpoint: str) -> Iterator[minio.Minio]:
    """テスト用ログ (rtc_stats / session_webhook 両方) をアップロードした S3 クライアント。"""
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    wait_until(lambda: client.list_buckets() is not None)
    reset_bucket(client, BUCKET)

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
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    wait_until(lambda: client.list_buckets() is not None)
    reset_bucket(client, BUCKET)

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
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    wait_until(lambda: client.list_buckets() is not None)
    reset_bucket(client, BUCKET)
    yield client
    remove_bucket(client, BUCKET)


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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # DuckDB に保存されている last_modified が、最新オブジェクト ((last_modified, object_name) の最大) の last_modified と一致することを確認する
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

    # 1 回目の init 後の状態を検証する。 検証用 connection を保持したまま 2 回目の
    # init を呼ぶと、 init 内部の DB 接続挙動 (prepare_db_for_init や
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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # DuckDB に保存されている last_modified が、最新オブジェクト ((last_modified, object_name) の最大) の last_modified と一致することを確認する
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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数より少ないことを確認する
        assert result[0] < len(objects)
        assert result[0] == initial_maximum_load

        # DuckDB に保存されている last_modified が、最新オブジェクト ((last_modified, object_name) の最大) の last_modified と一致することを確認する
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

    # init / update の呼び出しと検証用 connection を交差させないため、 検証ごとに
    # with duckdb.connect(...) を独立させる。 こうしておくと init / update 内部の DB
    # 接続挙動が変わってもテスト側が暗黙の前提に依存しない。
    objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
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
    new_log_file = os.path.join(LOG_DIR, "rtc_stats.jsonl")
    with open(new_log_file, "rb") as data:
        for line in data:
            parsed_log = json.loads(line)
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

        # DuckDB に保存されている last_modified が、最新オブジェクト ((last_modified, object_name) の最大) の last_modified と一致することを確認する
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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
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
    """保持期間内のデータのみの場合に delete を実行しても削除されないことを確認する。"""
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
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
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

    # S3 バケット名規約に従いつつ、RustFS に存在しないバケット名を指定する
    args = make_args_for_s3(
        duckdb_filepath, rustfs_endpoint, s3_bucket="non-existent-bucket"
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

    for line in new_lines:
        now = datetime.datetime.now(datetime.UTC)
        directory = now.strftime("%Y/%m/%d")
        s3_path = data_path(PREFIX, "rtc_stats", directory)
        compressed_log_data = gzip.compress(line)
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

    target_log_objects[-args.update_maximum_load :] のスライスが [-1:] になる境界値で、
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
        now = datetime.datetime.now(datetime.UTC)
        directory = now.strftime("%Y/%m/%d")
        s3_path = data_path(PREFIX, "rtc_stats", directory)
        compressed_log_data = gzip.compress(line)
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
