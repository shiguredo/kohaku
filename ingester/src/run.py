import argparse
import os
import shutil
import stat
import datetime
import sys

import yaml
import duckdb
import minio

DEFAULT_DUCKDB_FILE = "duck.db"
# /kohaku/log/connection/2025/06/01/a.gz のようなパスを想定
DEFAULT_S3_BUCKET_NAME = "kohaku"
DEFAULT_S3_PREFIX = "log"

DEFAULT_S3_REGION="ap-northeast-1"
DEFAULT_S3_USE_SSL=True
DEFAULT_RETENTION_PERIOD=7
# init 時に読み込むファイル数の上限
DEFAULT_INITIAL_MAXIMUM_LOAD=100

COLUMNS_DIR = "./DUCKDB_COLUMNS"

# Sora のログテーブル名兼 DuckDB のテーブル名
LOG_TARGETS = [
#    "connection",
    "rtc_stats",
]

def load_columns():
    duckdb_columns = {}
    for target in LOG_TARGETS:
        file_path = os.path.join(COLUMNS_DIR, f"{target}.yml")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Column definition file not found: {file_path}")

        with open(file_path, 'r') as f:
            # YAML ファイルを読み込んで辞書に変換
            columns = yaml.safe_load(f)
            if not isinstance(columns, dict):
                raise ValueError(f"Invalid format in {file_path}, expected a dictionary.")
            duckdb_columns[target] = columns

    return duckdb_columns


def init(args):
    print("init")
    if os.path.exists(args.db):
        return

    client = minio.Minio(args.s3_endpoint,
                         access_key=args.s3_access_key_id,
                         secret_key=args.s3_secret_access_key,
                         secure=args.s3_use_ssl)


    with duckdb.connect(args.db) as con:
        con.execute("INSTALL icu")
        con.execute("LOAD icu")

        # 取得済みの最後のオブジェクト情報を保存するテーブルを作成
        create_s3_object_table(con)

        s3_setup(con, args.storage, args.s3_endpoint, args.s3_access_key_id, args.s3_secret_access_key, args.s3_use_ssl, args.s3_region)

        for target in LOG_TARGETS:
            log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
            log_urls = get_target_urls(args.s3_bucket, log_objects[:args.initial_maximum_load])

            try:
                create_log_table(con, target, log_urls)
                if len(log_objects) > 0:
                    object = latest_object(log_objects)
                    update_s3_object_table(con, target, object)

            except duckdb.InvalidInputException as e:
                # まだディレクトリがないため、エラーを表示して次へ
                print(f"InvalidInputException ({target}): {e}")
            except Exception as e:
                raise e

def latest_object(objects):
    if not objects:
        return None

    # 最後に更新されたオブジェクトを取得する
    return max(objects, key=lambda obj: obj.last_modified)

def create_s3_object_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)")

def update_s3_object_table(con, log_type, object):
    con.execute("""
        MERGE INTO s3_objects AS target
        USING (SELECT ? AS type, ? AS object_name, ? AS last_modified) AS source
        ON target.type = source.type
        WHEN MATCHED THEN
            UPDATE SET object_name = source.object_name, last_modified = source.last_modified
        WHEN NOT MATCHED THEN
            INSERT (type, object_name, last_modified) VALUES (source.type, source.object_name, source.last_modified);
    """, (log_type, object.object_name, object.last_modified))

def list_objects(client, bucket, prefix):
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    return list(sorted(objects, key=lambda obj: obj.last_modified, reverse=True))

def get_target_urls(bucket, objects):
    urls = []
    for obj in objects:
        # テーブル作成時に読み込むファイルのパスを作成
        urls.append(f"s3://{bucket}/{obj.object_name}")

    return urls

def remove_delete_incompleted_copy_files(copyfile):
    """
    削除処理が失敗した時に残る可能性があるコピー先の .copy ファイルと .copy.wal ファイルを削除する
    """
    files = [copyfile, ".".join([copyfile, "wal"])]
    for file in files:
        try:
            os.remove(file)
        except FileNotFoundError:
            pass

def s3_setup(con, storage, s3_endpoint, s3_access_key_id, s3_secret_access_key, s3_use_ssl, s3_region):
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET s3_url_style='path'")
    con.execute(f"SET s3_endpoint='{s3_endpoint}'")
    con.execute(f"SET s3_access_key_id='{s3_access_key_id}'")
    con.execute(f"SET s3_secret_access_key='{s3_secret_access_key}'")
    con.execute(f"SET s3_use_ssl={s3_use_ssl}")
    if storage == "s3":
        # minio で設定すると minio に接続できなくてエラーになるためタイプごとに設定の有無を決められて方が良さそう
        con.execute(f"SET s3_region='{s3_region}'")

def create_log_table(con, table_name, target_urls):
    # s3://log/connection/2021/06/01/a.gz, s3://log/connection/2021/06/02/b.gz, ... のようなパスを想定
    # テーブル作成時は全てのファイルを読み込む
    # TODO: 指定した時間以降にするかは別途検討する
    duckdb_columns = load_columns()
    if table_name not in duckdb_columns:
        raise ValueError(f"Unknown table name: {table_name}. Available tables: {list(duckdb_columns.keys())}")

    # テーブルが存在する場合はすぐにリターンする
    rel = con.execute(f"SELECT table_name FROM duckdb_tables WHERE table_name='{table_name}';")
    if rel.fetchone() is not None:
        print(f"Table {table_name} already exists.")
        return

    # テーブルを作成する
    print(target_urls)
    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.create(table_name)

def update(args):
    if not os.path.exists(args.db):
        raise Exception("DB-FILE-NOT-FOUND")

    client = minio.Minio(args.s3_endpoint,
                         access_key=args.s3_access_key_id,
                         secret_key=args.s3_secret_access_key,
                         secure=args.s3_use_ssl)

    with duckdb.connect(args.db) as con:
        s3_setup(con, args.storage, args.s3_endpoint, args.s3_access_key_id, args.s3_secret_access_key, args.s3_use_ssl, args.s3_region)

        for target in LOG_TARGETS:
            object = select_s3_object(con, target)
            if object is None:
                log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
                if len(log_objects) == 0:
                    print(f"No log found for {target} in {args.s3_bucket}.")
                    # 対象のオブジェクトが存在しない場合はスキップする
                    continue

                log_urls = get_target_urls(args.s3_bucket, log_objects[:args.initial_maximum_load])
                create_log_table(con, target, log_urls)
                if len(log_objects) > 0:
                    object = latest_object(log_objects)
                    update_s3_object_table(con, target, object)
            else:
                # テーブルが存在しているのでログを追加する
                insert_log_from_s3(con, client, target, args.s3_bucket, args.s3_prefix)

def delete(args):
    if not os.path.exists(args.db):
        raise Exception("DB-FILE-NOT-FOUND")

    copy_file = ".".join([args.db, "copy"])

    deleted_rows = 0
    with duckdb.connect(args.db) as con:
        timestamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.retention_period))
        for target in LOG_TARGETS:
            deleted_rows += delete_log_by_timestamp(con, target, timestamp)

    # 削除された行がない場合は DB ファイルのコピーは作成せずに終了する
    if deleted_rows == 0:
        return

    try:
        with duckdb.connect() as con:
            # DB サイズ削減のため、DB ファイルをコピーする
            con.execute(f"ATTACH '{args.db}' AS db")
            con.execute(f"ATTACH '{copy_file}' AS copy")
            con.execute("COPY FROM DATABASE db TO copy")
    except Exception as e:
        # 処理に失敗したときの残る可能性のあるファイルを削除する
        remove_delete_incompleted_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise e

    try:
        # コピーしたファイルを、元の DB ファイルに上書きする
        os.chmod(copy_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
        shutil.move(copy_file, args.db)
    except Exception as e:
        # 処理に失敗したときの残る可能性のあるファイルを削除する
        remove_delete_incompleted_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise e


def insert_log_from_s3(con, client, table_name, bucket, prefix):
    object = select_s3_object(con, table_name)
    _, _, object_last_modified = object

    log_objects = list_objects(client, bucket, f"{prefix}/{table_name}/")

    target_log_objects = [obj for obj in log_objects if obj.last_modified > object_last_modified]
    target_urls = get_target_urls(bucket, target_log_objects)

    if len(target_log_objects) > 0:
        con.begin()
        try:
            insert_log(con, table_name, target_urls)
            update_s3_object_table(con, table_name, latest_object(target_log_objects))
            con.commit()
        except Exception as e:
            con.rollback()
            raise e

def insert_log(con, table_name, target_urls):
    duckdb_columns = load_columns()
    if table_name not in duckdb_columns:
        raise ValueError(f"Unknown table name: {table_name}. Available tables: {list(duckdb_columns.keys())}")

    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.insert_into(table_name)

def select_s3_object(con, log_type):
    q = f"SELECT * FROM s3_objects WHERE type = '{log_type}'"
    return con.execute(q).fetchone()

def delete_log_by_timestamp(con, table_name, timestamp):
    object = con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{table_name}'")
    count = object.fetchone()
    if count[0] < 1:
        # テーブルが存在しない場合はスキップする
        # delete サブコマンドはテーブル名を指定して実行ではないため、テーブルが存在しない場合もエラーにはしない
        print(f"Table {table_name} does not exist.")
        return 0

    print(f"DELETE FROM {table_name} WHERE timestamp < '{timestamp}'")
    con.execute(f"DELETE FROM {table_name} WHERE timestamp < '{timestamp}'")
    result = con.fetchone()
    return result[0]

def main():
    parser = argparse.ArgumentParser()
    # 共通オプション
    parser.add_argument("--db", default=DEFAULT_DUCKDB_FILE, help="DB file path")
    parser.add_argument("--storage", default="s3", help="Storage type(s3, minio)")
    parser.add_argument("--s3_endpoint", default="127.0.0.1:9000", help="S3 endpoint")
    parser.add_argument("--s3_access_key_id", default="rootuser", help="S3 access key id")
    parser.add_argument("--s3_secret_access_key", default="password", help="S3 secret access key")
    parser.add_argument("--s3_use_ssl", action="store_true", help="S3 use SSL")
    parser.add_argument("--s3_region", default=DEFAULT_S3_REGION, help="S3 region")
    parser.add_argument("--s3_bucket", default=DEFAULT_S3_BUCKET_NAME, help="S3 bucket name")
    parser.add_argument("--s3_prefix", default=DEFAULT_S3_PREFIX, help="S3 prefix")
    parser.add_argument("--retention_period", default=DEFAULT_RETENTION_PERIOD, help="retention period", type=int)
    parser.add_argument("--initial_maximum_load", default=DEFAULT_INITIAL_MAXIMUM_LOAD, help="Initial maximum load", type=int)


    subparsers = parser.add_subparsers()
    subparsers_init = subparsers.add_parser("init")
    subparsers_init.set_defaults(func=init)

    subparsers_update = subparsers.add_parser("update")
    subparsers_update.set_defaults(func=update)

    subparsers_delete = subparsers.add_parser("delete")
    subparsers_delete.set_defaults(func=delete)

    args = parser.parse_args()
    db = args.db

    if args.func == init:
        # init は DB ファイルがない、または、DB にデータが入っていない場合のみ実行する想定のため、ファイル更新比較処理の対象外
        args.func(args)
    else:
        # DB ファイルがない場合は終了する
        if not os.path.exists(db):
            parser.print_usage()
            sys.exit(1)
        else:
            # DB ファイルの最終更新時刻を取得する
            statinfo = os.stat(db)
            mtime = statinfo.st_mtime

            args.func(args)

            statinfo = os.stat(db)
            if mtime == statinfo.st_mtime:
                # DB ファイルが更新されていない場合は終了する
                return

    # DuckDB は、DB ファイルへの書き込み時には他のプロセスからアクセスできないため、
    # 書き込み終了後に、DB ファイルのコピーを作成してから、読み込み専用の DB ファイルにリネームする
    # 読み込みは、複数プロセスからアクセス可能なこの読み込み専用の DB ファイルに対しておこなう
    # 参考: https://github.com/motherduckdb/grafana-duckdb-datasource?tab=readme-ov-file#updating-data-in-the-duckdb-file
    tmp_file = ".".join([args.db, "bacon"])
    shutil.copyfile(args.db, tmp_file)

    # grafana から読み込むために 666 に設定する
    os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
    readonly_file = ".".join([args.db, "readonly"])
    shutil.move(tmp_file, readonly_file)

if __name__ == "__main__":
    main()
