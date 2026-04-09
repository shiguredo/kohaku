import os
import io
import datetime
import gzip
import json

from run import init, update, delete, prepare_db_for_init

import uuid
import pytest
from testcontainers.minio import MinioContainer

import duckdb

BUCKET = "kohaku"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
PREFIX = "log"
# 出力されたままのログファイルを保存するディレクトリ
LOG_DIR = "./tests/log"
DUCKDB_DIR_PATH = "."

class Args:
    def __init__(self, db=None, s3_endpoint=None, s3_access_key_id=None, s3_secret_access_key=None, s3_use_ssl=None, s3_region=None, storage=None, s3_bucket=None, s3_prefix=None, retention_period=None, initial_maximum_load=None):
        self.db = db
        self.s3_endpoint = s3_endpoint
        self.s3_access_key_id = s3_access_key_id
        self.s3_secret_access_key = s3_secret_access_key
        self.s3_use_ssl = s3_use_ssl
        self.s3_region = s3_region
        self.storage = storage
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self.retention_period = retention_period
        self.initial_maximum_load = initial_maximum_load

def data_path(s3_prefix, tag, directory):
    """
    S3 のデータパスを生成する関数
    :param s3_prefix: S3 のプレフィックス
    :param tag: タグ
    :param directory: ディレクトリ名
    :return: フォーマットされた S3 パス
    """

    filename = f"{uuid.uuid4()}.gz"
    return f"{s3_prefix}/{tag}/{directory}/{filename}"

def list_objects(minio_client, bucket_name):
    """
    指定されたバケット内のオブジェクトをリストする関数
    :param minio_client: MinIO クライアント
    :param bucket_name: バケット名
    :return: オブジェクトのリスト
    """

    objects = minio_client.list_objects(bucket_name, recursive=True)
    return [obj.object_name for obj in objects]

def remove_objects(minio_client, bucket_name):
    """
    指定されたバケット内のすべてのオブジェクトを削除する関数
    :param minio_client: MinIO クライアント
    :param bucket_name: バケット名
    """

    objects = list_objects(minio_client, bucket_name)
    for obj in objects:
        minio_client.remove_object(bucket_name, obj)

def remove_bucket(minio_client, bucket_name):
    """
    指定されたバケットを削除する関数
    :param minio_client: MinIO クライアント
    :param bucket_name: バケット名
    """

    remove_objects(minio_client, bucket_name)
    minio_client.remove_bucket(bucket_name)

# 指定した期間だけ過去に更新する関数
def update_timestamp_for_rtc_stats(con, obj, period):
    """
    DuckDB のオブジェクトの更新日時を更新する関数
    :param con: DuckDB の接続オブジェクト
    :param obj: 更新対象のオブジェクト
    :param period: timestamp を過去に設定する期間（日数）
    """

    org_timestamp = obj[0]
    connection_id = obj[1]
    rtc_id = obj[2]
    rtc_type = obj[3]

    now = datetime.datetime.now(datetime.timezone.utc)
    # 指定された期間だけ過去に更新
    timestamp = now - datetime.timedelta(days=period)

    # 更新日時を更新するクエリを実行
    con.execute("""
        UPDATE rtc_stats
        SET timestamp = ?
        WHERE connection_id = ? AND rtc_id = ? AND rtc_type = ? AND timestamp = ?
    """, (timestamp, connection_id, rtc_id, rtc_type, org_timestamp))

    # 更新後の確認
    con.execute("SELECT timestamp FROM rtc_stats WHERE connection_id = ? AND rtc_id = ? AND rtc_type = ? AND timestamp = ?", (connection_id, rtc_id, rtc_type, timestamp))
    updated_timestamp = con.fetchone()
    if updated_timestamp:
        print(f"Updated timestamp for connection_id: {connection_id}, rtc_id: {rtc_id}, rtc_type: {rtc_type}: {updated_timestamp[0]}")
    else:
        print(f"No record found for connection_id: {connection_id}, rtc_id: {rtc_id}, rtc_type: {rtc_type}, org_timestamp: {org_timestamp}")

def get_latest_object(minio_client, bucket, prefix):
    """
    オブジェクトストレージ上で処理対象の最新のオブジェクトを取得する関数
    :param minio_client: MinIO クライアント
    :return: 最新のオブジェクト
    """

    objects = minio_client.list_objects(bucket, prefix=prefix, recursive=True)
    return max(objects, key=lambda obj: obj.last_modified)

@pytest.fixture(scope="session")
def minio_container():
    with MinioContainer() as minio:
        yield minio

@pytest.fixture
def minio_client(minio_container):
    # MinIO クライアントの作成
    client = minio_container.get_client()
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
                with open(log_file_path, 'rb') as data:
                    for line in data:
                        directory = now.strftime("%Y/%m/%d")
                        s3_path = data_path(PREFIX, tag, directory)
                        # gzip 圧縮
                        compressed_log_data = gzip.compress(line)

                        # アップロード
                        result = client.put_object(
                            BUCKET,
                            s3_path,
                            io.BytesIO(compressed_log_data),
                            length=len(compressed_log_data),
                        )

    return client

@pytest.fixture
def duckdb_connection(filepath):
    """
    DuckDBの接続を提供するフィクスチャ
    :return: DuckDBの接続オブジェクト
    """
    con = duckdb.connect(filepath)
    yield con
    con.close()

def test_init(request, minio_client, minio_container):
    """init 実行でログを取り込み、DuckDB とオブジェクトカーソルが作成されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)

    # テスト開始時に BUCKET が存在することを確認
    assert minio_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
    duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
    result = duckdb_connection.fetchone()

    # 取得したデータ数が正しいことを確認する
    # データが取得できていることを確認する
    assert result is not None
    # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
    assert result[0] > 0
    # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
    assert result[0] == len(objects)

    # DuckDB に保存されている last_modified が、最新のオブジェクト の last_modified と一致することを確認する
    latest_object = get_latest_object(minio_client, BUCKET, "/".join([PREFIX, "rtc_stats"]))
    duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?", ("rtc_stats", latest_object.last_modified,))
    result = duckdb_connection.fetchone()
    assert result is not None
    assert result[0] == 1

def test_re_init(request, minio_client, minio_container):
    """init を再実行してもデータ件数とカーソル情報が変化しないことを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)

    # テスト開始時に BUCKET が存在することを確認
    assert minio_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
    duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
    result = duckdb_connection.fetchone()
    # 取得したデータ数が正しいことを確認する
    assert result is not None
    # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
    assert result[0] > 0
    # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
    assert result[0] == len(objects)

    # DuckDB に保存されている last_modified が、最新のオブジェクト の last_modified と一致することを確認する
    latest_object = get_latest_object(minio_client, BUCKET, "/".join([PREFIX, "rtc_stats"]))
    duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?", ("rtc_stats", latest_object.last_modified,))
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
    duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?", ("rtc_stats", latest_object.last_modified,))
    result = duckdb_connection.fetchone()
    assert result is not None
    assert result[0] == 1

def test_file_count_limit_for_init(request, minio_client, minio_container):
    """init の初期読み込み上限で取り込み件数が制限されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加する
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)

    # テスト開始時に BUCKET が存在することを確認
    assert minio_client.bucket_exists(BUCKET)

    # 初期最大読み込み数を設定する
    initial_maximum_load = 50

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=initial_maximum_load
    )

    # init関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
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

    # DuckDB に保存されている last_modified が、最新のオブジェクト の last_modified と一致することを確認する
    latest_object = get_latest_object(minio_client, BUCKET, "/".join([PREFIX, "rtc_stats"]))
    duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?", ("rtc_stats", latest_object.last_modified))
    result = duckdb_connection.fetchone()
    assert result is not None
    assert result[0] == 1

def test_update(request, minio_client, minio_container):
    """update 実行時に差分ログのみが追加され、件数とカーソルが更新されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)

    # テスト開始時に BUCKET が存在することを確認
    assert minio_client.bucket_exists(BUCKET)

    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)

    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
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
    with open(new_log_file, 'rb') as data:
        for line in data:
            parsed_log = json.loads(line)
            now = datetime.datetime.now(datetime.timezone.utc)
            log_data = json.dumps(parsed_log).encode('utf-8')
            compressed_log_data = gzip.compress(log_data)

            directory = now.strftime("%Y/%m/%d")
            s3_path = data_path(PREFIX, "rtc_stats", directory)
            # アップロード
            result = minio_client.put_object(
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
    objects = list_objects(minio_client, BUCKET)
    assert result[0] == len(objects)

    # DuckDB に保存されている last_modified が、最新のオブジェクト の last_modified と一致することを確認する
    latest_object = get_latest_object(minio_client, BUCKET, "/".join([PREFIX, "rtc_stats"]))
    duckdb_connection.execute("SELECT COUNT(*) FROM s3_objects WHERE type=? and last_modified = ?", ("rtc_stats", latest_object.last_modified,))
    result = duckdb_connection.fetchone()
    assert result is not None
    assert result[0] == 1

def test_all_delete(request, minio_client, minio_container):
    """保持期間外のデータだけで構成された場合に delete で全件削除されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)
    # テスト開始時に BUCKET が存在することを確認

    assert minio_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
    duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
    result = duckdb_connection.fetchone()
    # 取得したデータ数が正しいことを確認する
    assert result is not None
    # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
    assert result[0] > 0
    # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
    assert result[0] == len(objects)


    # DuckDB のオブジェクトを取得する
    duckdb_connection.execute("SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats")
    objects = duckdb_connection.fetchall()
    # すべてのオブジェクトの timestamp を 2 日前に更新する
    for _, obj in enumerate(objects):
        update_timestamp_for_rtc_stats(duckdb_connection, obj, 2)

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
    # 全てのオブジェクトの timestamp を 2 日前に更新したため、全てのデータが削除される
    assert result[0] == 0

def test_delete(request, minio_client, minio_container):
    """保持期間外と期間内が混在する場合に delete で期間外のみ削除されることを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)
    # テスト開始時に BUCKET が存在することを確認

    assert minio_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
    duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
    result = duckdb_connection.fetchone()
    # 取得したデータ数が正しいことを確認する
    assert result is not None
    # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
    assert result[0] > 0
    # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
    assert result[0] == len(objects)


    # DuckDB のオブジェクトを取得する
    duckdb_connection.execute("SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats")
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

def test_delete_within_retention_period(request, minio_client, minio_container):
    """保持期間内のデータのみの場合に delete を実行しても削除されないことを確認する。"""
    # node.name を使用して DuckDB のファイル名を生成する
    duckdb_filename = f"{request.node.name}.db"
    duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

    # テスト後に BUCKET を削除するためのクリーンアップ処理を追加
    request.addfinalizer(lambda: remove_bucket(minio_client, BUCKET))
    request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)
    # テスト開始時に BUCKET が存在することを確認

    assert minio_client.bucket_exists(BUCKET)
    # ingester/src/run.py の init 関数を呼び出すための引数を設定
    config = minio_container.get_config()
    endpoint = config["endpoint"]
    args = Args(
        db=duckdb_filepath,
        s3_endpoint=endpoint,
        s3_access_key_id=ACCESS_KEY,
        s3_secret_access_key=SECRET_KEY,
        s3_use_ssl=False,
        s3_region="ap-northeast-1",
        storage="rustfs",
        s3_bucket=BUCKET,
        s3_prefix=PREFIX,
        initial_maximum_load=1000
    )

    # init関数を呼び出して初期化する
    init(args)
    # DB ファイルが存在することを確認する
    assert os.path.exists(duckdb_filepath)

    # DB に保存したデータ数を確認する
    duckdb_connection = duckdb.connect(duckdb_filepath)
    objects = list_objects(minio_client, BUCKET)
    duckdb_connection.execute("SELECT COUNT(*) FROM rtc_stats")
    result = duckdb_connection.fetchone()
    # 取得したデータ数が正しいことを確認する
    assert result is not None
    # RustFS にオブジェクトがアップロードできずに、RustFS と DuckDB のデータ数が 0 ではないことを確認する
    assert result[0] > 0
    # 取得したデータ数が、RustFS にアップロードしたオブジェクトの数と一致することを確認する
    assert result[0] == len(objects)


    # DuckDB のオブジェクトを取得する
    duckdb_connection.execute("SELECT timestamp, connection_id, rtc_id, rtc_type FROM rtc_stats")
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

def test_no_bucket(request, minio_container):
    """RustFS のバケットが存在しない場合に init が例外を送出することを確認する。"""

    with pytest.raises(Exception):
        duckdb_filename = f"{request.node.name}.db"
        duckdb_filepath = os.path.join(DUCKDB_DIR_PATH, duckdb_filename)

        # テスト後に DuckDB のファイルを削除するためのクリーンアップ処理を追加
        request.addfinalizer(lambda: os.remove(duckdb_filepath) if os.path.exists(duckdb_filepath) else None)

        # ingester/src/run.py の init 関数を呼び出すための引数を設定
        config = minio_container.get_config()
        endpoint = config["endpoint"]
        args = Args(
            db=duckdb_filepath,
            s3_endpoint=endpoint,
            s3_access_key_id=ACCESS_KEY,
            s3_secret_access_key=SECRET_KEY,
            s3_use_ssl=False,
            s3_region="ap-northeast-1",
            storage="rustfs",
            # 存在しないバケット名
            s3_bucket="non_existent_bucket",
            s3_prefix=PREFIX,
            initial_maximum_load=1000
        )

        init(args)

def test_prepare_db_for_init_renames_broken_db_file(tmp_path, monkeypatch):
    """壊れた DB を prepare_db_for_init が検出し、DB と WAL を退避リネームすることを確認する。"""
    db_path = tmp_path / "broken.db"
    wal_path = tmp_path / "broken.db.wal"
    db_path.write_bytes(b"invalid db")
    wal_path.write_bytes(b"wal")

    def mock_connect(_):
        raise duckdb.IOException("invalid database file")

    monkeypatch.setattr(duckdb, "connect", mock_connect)

    prepare_db_for_init(str(db_path))

    renamed_files = list(tmp_path.glob("broken.db.broken.*"))
    assert len(renamed_files) == 2
    renamed_db_files = [path for path in renamed_files if not str(path).endswith(".wal")]
    renamed_wal_files = [path for path in renamed_files if str(path).endswith(".wal")]
    assert len(renamed_db_files) == 1
    assert len(renamed_wal_files) == 1

    assert db_path.exists() is False
    assert wal_path.exists() is False
    assert renamed_db_files[0].exists()
    assert renamed_wal_files[0].exists()


def test_prepare_db_for_init_skips_permission_error(tmp_path, monkeypatch):
    """Permission denied 時は prepare_db_for_init がファイルをリネームせず終了することを確認する。"""
    db_path = tmp_path / "permission.db"
    wal_path = tmp_path / "permission.db.wal"
    db_path.write_bytes(b"db")
    wal_path.write_bytes(b"wal")

    def mock_connect(_):
        raise duckdb.IOException("Permission denied")

    monkeypatch.setattr(duckdb, "connect", mock_connect)

    prepare_db_for_init(str(db_path))

    renamed_files = list(tmp_path.glob("permission.db.broken.*"))
    # Permission denied エラーの場合はファイルをリネームせずにスキップするため、リネームされたファイルが存在しないことを確認する
    assert len(renamed_files) == 0
    assert db_path.exists()
    assert wal_path.exists()
