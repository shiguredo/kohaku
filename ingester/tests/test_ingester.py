import os
import io
import datetime
import gzip
import json
from collections.abc import Sequence
from typing import Any

from run import init, update, delete, prepare_db_for_init

import uuid
import minio
import pytest
from minio.error import S3Error

from .conftest import ACCESS_KEY, BUCKET, PREFIX, SECRET_KEY
from .helpers import wait_until

import duckdb

# 出力されたままのログファイルを保存するディレクトリ
LOG_DIR = "./tests/log"
# テスト中に DuckDB ファイルを置くディレクトリ (カレント直下)
DUCKDB_DIR_PATH = "."


class Args:
    """ingester/src/run.py が受け取る argparse.Namespace を模した DTO。

    テストから init / update / delete を直接呼び出すために、本番で argparse が組み立てる
    Namespace と同じ属性名を揃えてある。各サブコマンドが参照するフィールドの組は異なる
    (例: delete は db と retention_period のみ参照する) ため、すべて Optional にしてある。
    """

    def __init__(
        self,
        # DuckDB ファイルのパス (例: "./test_init.db")。init/update/delete すべてで参照する
        db: str | None = None,
        # S3 互換ストレージの HTTP エンドポイント (例: "127.0.0.1:9000")。テストでは RustFS コンテナを指す
        s3_endpoint: str | None = None,
        # S3 互換ストレージのアクセスキー (RustFS の RUSTFS_ACCESS_KEY と同じ値)
        s3_access_key_id: str | None = None,
        # S3 互換ストレージのシークレットキー (RustFS の RUSTFS_SECRET_KEY と同じ値)
        s3_secret_access_key: str | None = None,
        # S3 接続時に SSL/TLS を使うかどうか。テストの RustFS は http なので False
        s3_use_ssl: bool | None = None,
        # S3 のリージョン名 (例: "ap-northeast-1")。リクエスト署名に使う
        s3_region: str | None = None,
        # 取り込み対象の S3 バケット名 (テストでは BUCKET 定数)
        s3_bucket: str | None = None,
        # ログオブジェクトキー先頭のプレフィックス (テストでは PREFIX 定数、"log/rtc_stats/..." のように使う)
        s3_prefix: str | None = None,
        # delete サブコマンド用: 何日より古い行を削除するかの閾値 (単位: 日)
        retention_period: int | None = None,
        # init サブコマンド用: 一度に取り込む S3 オブジェクト件数の上限
        initial_maximum_load: int | None = None,
        # update サブコマンド用: 一度に取り込む S3 オブジェクト件数の上限
        update_maximum_load: int = 1000,
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


# rtc_stats の timestamp を period 日だけ過去にずらすヘルパー関数
def update_timestamp_for_rtc_stats(
    con: duckdb.DuckDBPyConnection, obj: Sequence[Any], period: int
) -> None:
    """rtc_stats の timestamp を現在時刻から period 日だけ過去に更新する。"""
    org_timestamp = obj[0]
    connection_id = obj[1]
    rtc_id = obj[2]
    rtc_type = obj[3]

    now = datetime.datetime.now(datetime.timezone.utc)
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


def get_latest_object(s3_client: minio.Minio, bucket: str, prefix: str) -> Any:
    """オブジェクトストレージ上で最新のオブジェクトを返す。

    本番コードの list_objects と同じく (last_modified, object_name) を比較キーにする。
    last_modified が同値の場合に object_name で順序が決まるため、本番とテストで
    「最新」の定義を一致させる。
    """
    objects = s3_client.list_objects(bucket, prefix=prefix, recursive=True)
    return max(objects, key=lambda obj: (obj.last_modified, obj.object_name))


@pytest.fixture
def s3_client(rustfs_endpoint: str) -> minio.Minio:
    # RustFS に接続する MinIO クライアントを作成する
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    # RustFS が利用可能になるまで待機する
    wait_until(lambda: client.list_buckets() is not None)
    # バケットの作成
    found = client.bucket_exists(BUCKET)
    # バケットは常に存在しない
    assert found is False
    client.make_bucket(BUCKET)

    now = datetime.datetime.now(datetime.timezone.utc)
    for root, dirs, filenames in os.walk(LOG_DIR):
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

    return client


@pytest.fixture
def s3_client_without_session_webhook(rustfs_endpoint: str) -> minio.Minio:
    # session_webhook を意図的に除外し、ログ種別が欠損した状態を再現する S3 クライアントを作成する
    # RustFS に接続する MinIO クライアントを作成する
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    # RustFS が利用可能になるまで待機する
    wait_until(lambda: client.list_buckets() is not None)
    # バケットの作成
    found = client.bucket_exists(BUCKET)
    # バケットは常に存在しない
    assert found is False
    client.make_bucket(BUCKET)

    # rtc_stats のみアップロードし、session_webhook はアップロードしない
    now = datetime.datetime.now(datetime.timezone.utc)
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

    return client


@pytest.fixture
def s3_client_empty(rustfs_endpoint: str) -> minio.Minio:
    """ログオブジェクトを 1 件もアップロードしない空バケットを準備する S3 クライアント。"""
    client = minio.Minio(
        rustfs_endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    # RustFS が利用可能になるまで待機する
    wait_until(lambda: client.list_buckets() is not None)
    # バケットの作成
    found = client.bucket_exists(BUCKET)
    # バケットは常に存在しない
    assert found is False
    client.make_bucket(BUCKET)
    # オブジェクトは意図的に 1 件も置かない
    return client


def test_init(request, s3_client, rustfs_endpoint):
    """init 実行でログを取り込み、DuckDB とオブジェクトカーソルが作成されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # テスト開始時に BUCKET が存在することを確認
    assert s3_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # データが取得できていることを確認する
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


def test_re_init(request, s3_client, rustfs_endpoint):
    """init を再実行してもデータ件数とカーソル情報が変化しないことを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # テスト開始時に BUCKET が存在することを確認
    assert s3_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # データが取得できていることを確認する
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

        # 再度 init を呼び出しても、内容が変わらないことを確認する
        init(args)
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


def test_file_count_limit_for_init(request, s3_client, rustfs_endpoint):
    """init の初期読み込み上限で取り込み件数が制限されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # テスト開始時に BUCKET が存在することを確認
    assert s3_client.bucket_exists(BUCKET)

    # 初期最大読み込み数を設定する
    initial_maximum_load = 50

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=initial_maximum_load,
    )

    # init 関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が正しいことを確認する
        # データが取得できていることを確認する
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


def test_update(request, s3_client, rustfs_endpoint):
    """update 実行時に差分ログのみが追加され、件数とカーソルが更新されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # テスト開始時に BUCKET が存在することを確認
    assert s3_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が正しいことを確認する
        assert result is not None
        # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
        assert result[0] > 0
        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        assert result[0] == len(objects)

        # log データに変化がないため、update を呼び出してもデータ数が変わらないことを確認する
        update(args)
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
                now = datetime.datetime.now(datetime.timezone.utc)
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
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が増えていることを確認する
        assert result is not None
        assert result[0] > len(objects)

        # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
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


def test_all_delete(request, s3_client, rustfs_endpoint):
    """保持期間外のデータだけで構成された場合に delete で全件削除されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )
    # テスト開始時に BUCKET が存在することを確認

    assert s3_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が正しいことを確認する
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
        for _, obj in enumerate(objects):
            update_timestamp_for_rtc_stats(duckdb_connection, obj, 2)

        # delete 関数を呼び出すための引数を設定
        # retention_period を 1 日に設定して、2 日前のデータが削除されることを確認する
        args = Args(
            db=duckdb_filepath,
            retention_period=1,
        )
        # delete 関数を呼び出して、データが削除されることを確認する
        delete(args)
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # 全てのオブジェクトの timestamp を 2 日前に更新したため、全てのデータが削除される
        assert result[0] == 0


def test_delete(request, s3_client, rustfs_endpoint):
    """保持期間外と期間内が混在する場合に delete で期間外のみ削除されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )
    # テスト開始時に BUCKET が存在することを確認

    assert s3_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が正しいことを確認する
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
        # delete 関数を呼び出すための引数を設定
        args = Args(
            db=duckdb_filepath,
            retention_period=1,
        )
        # delete 関数を呼び出して、データが削除されることを確認する
        delete(args)
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # 偶数番目のオブジェクトの timestamp を 2 日前に更新したため、半分のデータが残る
        assert result[0] == len(objects) // 2


def test_delete_within_retention_period(request, s3_client, rustfs_endpoint):
    """保持期間内のデータのみの場合に delete を実行しても削除されないことを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )
    # テスト開始時に BUCKET が存在することを確認

    assert s3_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # init 関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    with duckdb.connect(duckdb_filepath) as duckdb_connection:
        objects = list_objects(s3_client, BUCKET, prefix=f"{PREFIX}/rtc_stats/")
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        # 取得したデータ数が正しいことを確認する
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
        # delete 関数を呼び出すための引数を設定
        args = Args(
            db=duckdb_filepath,
            retention_period=3,
        )
        # delete 関数を呼び出して、データが削除されることを確認する
        delete(args)
        duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
        result = duckdb_connection.fetchone()
        assert result is not None
        # データの保持期間が 3 日のため、データは削除されない
        assert result[0] == len(objects)


def test_no_bucket(request, rustfs_endpoint):
    """RustFS のバケットが存在しない場合に init が S3Error(NoSuchBucket) を送出することを確認する。"""
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に DuckDB のファイルを削除するためのクリーンアップ処理を追加
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        # S3 バケット名規約に従いつつ、RustFS に存在しないバケット名を指定する
        s3_bucket="non-existent-bucket",
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    with pytest.raises(S3Error) as exc_info:
        init(args)
    assert exc_info.value.code == "NoSuchBucket"


def test_init_skips_missing_session_webhook(
    request, s3_client_without_session_webhook, rustfs_endpoint
):
    """session_webhook が S3 に存在しない場合でも init が成功し、rtc_stats のみ取り込まれることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加する
    request.addfinalizer(
        lambda: remove_bucket(s3_client_without_session_webhook, BUCKET)
    )
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    # テスト開始時に BUCKET が存在することを確認
    assert s3_client_without_session_webhook.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

    # session_webhook が欠損していても init が例外を送出しないことを確認する
    init(args)

    # DB ファイルが存在することを確認する
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


def test_init_and_update_on_empty_bucket(request, s3_client_empty, rustfs_endpoint):
    """全ターゲットが空のバケットに対して init/update がエラーなく完走し、データテーブルが作成されないことを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET と DuckDB ファイルを削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(s3_client_empty, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    assert s3_client_empty.bucket_exists(BUCKET)

    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )

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
            assert table_count[0] == 0, f"{target} should not be created"
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


def test_prepare_db_for_init_renames_broken_db_file(tmp_path):
    """壊れた DB を prepare_db_for_init が検出し、DB と WAL をリネームして退避したことを確認する。"""
    db_path = tmp_path / "broken.db"
    wal_path = tmp_path / "broken.db.wal"
    db_path.write_bytes(b"invalid db")
    wal_path.write_bytes(b"wal")

    prepare_db_for_init(str(db_path))

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


def test_update_maximum_load_splits_batches(request, s3_client, rustfs_endpoint):
    """update_maximum_load より多い新規ログを 1 回の update で取り込まず、複数回呼び出しで取り込み切ることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET と DuckDB ファイルを削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    assert s3_client.bucket_exists(BUCKET)

    # 既存ログをすべて取り込んでカーソルを最新に揃える
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )
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
        now = datetime.datetime.now(datetime.timezone.utc)
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
    batch_args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
        update_maximum_load=2,
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
    request, s3_client, rustfs_endpoint
):
    """update_maximum_load=1 (positive_int の最小値) で 1 回あたり 1 件ずつ取り込むことを確認する。

    target_log_objects[-args.update_maximum_load :] のスライスが [-1:] になる境界値で、
    残件が複数あっても 1 件だけ取り込み、複数回呼び出しで取り込み切ることを確認する。
    """
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET と DuckDB ファイルを削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(s3_client, BUCKET))
    request.addfinalizer(
        lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None
    )

    assert s3_client.bucket_exists(BUCKET)

    # 既存ログをすべて取り込んでカーソルを最新に揃える
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
    )
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
        now = datetime.datetime.now(datetime.timezone.utc)
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
    batch_args = Args(
        db=duckdb_filepath,
        s3_endpoint=rustfs_endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000,
        update_maximum_load=1,
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
            prepare_db_for_init(str(db_path))

        renamed_files = list(tmp_path.glob("permission.db.broken.*"))
        # Permission denied は破損 DB ではないため、退避ファイルは作られない
        assert len(renamed_files) == 0
        assert db_path.exists()
        assert wal_path.exists()
    finally:
        # tmp ディレクトリのクリーンアップが失敗しないようにパーミッションを戻す
        os.chmod(db_path, 0o600)
