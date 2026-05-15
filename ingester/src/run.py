import argparse
import os
import shutil
import stat
import datetime
import sys

import yaml
import duckdb
import minio
from minio.error import S3Error

DEFAULT_DUCKDB_FILE = "duck.db"
# /kohaku/log/connection/2025/06/01/a.gz のようなパスを想定
DEFAULT_S3_BUCKET_NAME = "kohaku"
DEFAULT_S3_PREFIX = "log"

DEFAULT_S3_REGION = "ap-northeast-1"
DEFAULT_RETENTION_PERIOD = 7
# init 時に読み込むファイル数の上限
DEFAULT_INITIAL_MAXIMUM_LOAD = 100
# update 時に 1 回で取り込むファイル数の上限。停止後の復帰時に大量蓄積したログを
# バッチ分割するために用いる。
DEFAULT_UPDATE_MAXIMUM_LOAD = 100

COLUMNS_DIR = "./DUCKDB_COLUMNS"
# DB ファイルが破損していると判断するためのエラーメッセージのパターン
BROKEN_DB_ERROR_PATTERNS = (
    "corrupt",
    "invalid database",
    "not a valid duckdb",
)

# Sora のログテーブル名兼 DuckDB のテーブル名
LOG_TARGETS = (
    # 現行のダッシュボードでは connection を使用していないため、使用したい場合にはコメントアウトを外します
    # "connection",
    "rtc_stats",
    "session_webhook",
)


def positive_int(value):
    int_value = int(value)
    if int_value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return int_value


def load_columns():
    duckdb_columns = {}
    for target in LOG_TARGETS:
        file_path = os.path.join(COLUMNS_DIR, f"{target}.yml")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Column definition file not found: {file_path}")

        with open(file_path, "r") as f:
            # YAML ファイルを読み込んで辞書に変換
            columns = yaml.safe_load(f)
            if not isinstance(columns, dict):
                raise ValueError(
                    f"Invalid format in {file_path}, expected a dictionary."
                )
            duckdb_columns[target] = columns

    return duckdb_columns


def require_s3_credentials(args):
    if not args.s3_access_key_id or not args.s3_secret_access_key:
        raise ValueError(
            "S3 credentials are required: provide --s3_access_key_id and --s3_secret_access_key"
        )


def init(args):
    print("init")
    require_s3_credentials(args)
    prepare_db_for_init(args.db)
    if is_initialized_db(args.db):
        return

    client = minio.Minio(
        args.s3_endpoint,
        access_key=args.s3_access_key_id,
        secret_key=args.s3_secret_access_key,
        secure=args.s3_use_ssl,
    )

    with duckdb.connect(args.db) as con:
        con.execute("INSTALL icu")
        con.execute("LOAD icu")

        # 取得済みの最後のオブジェクト情報を保存するテーブルを作成
        create_s3_object_table(con)

        s3_setup(
            con,
            args.s3_endpoint,
            args.s3_access_key_id,
            args.s3_secret_access_key,
            args.s3_use_ssl,
            args.s3_region,
        )
        sync_logs(con, client, args, "init")


def sync_logs(con, client, args, mode):
    for target in LOG_TARGETS:
        if mode == "init":
            sync_log_for_init(con, client, args, target)
        elif mode == "update":
            sync_log_for_update(con, client, args, target)
        else:
            raise ValueError(f"Unknown mode: {mode}")


def sync_log_for_init(con, client, args, target):
    log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
    log_urls = get_target_urls(args.s3_bucket, log_objects[: args.initial_maximum_load])

    # 初期化対象のログが存在しない場合は、テーブル作成をスキップする
    if len(log_urls) == 0:
        print(f"No log found for {target} in {args.s3_bucket}.")
        return

    try:
        create_log_table(con, target, log_urls)
        if len(log_objects) > 0:
            # list_objects は (last_modified, object_name) の降順なので先頭が最新
            update_s3_object_table(con, target, log_objects[0])
    except duckdb.InvalidInputException as e:
        # まだディレクトリがないため、エラーを表示して次へ
        print(f"InvalidInputException ({target}): {e}")


def sync_log_for_update(con, client, args, target):
    cursor = select_s3_object(con, target)
    if cursor is None:
        log_objects = list_objects(
            client, args.s3_bucket, f"{args.s3_prefix}/{target}/"
        )
        if len(log_objects) == 0:
            print(f"No log found for {target} in {args.s3_bucket}.")
            # 対象のオブジェクトが存在しない場合はスキップする
            return

        log_urls = get_target_urls(
            args.s3_bucket, log_objects[: args.initial_maximum_load]
        )
        create_log_table(con, target, log_urls)
        if len(log_objects) > 0:
            # list_objects は (last_modified, object_name) の降順なので先頭が最新
            update_s3_object_table(con, target, log_objects[0])
    else:
        # テーブルが存在しているのでログを追加する
        insert_log_from_s3(con, client, target, args)


def is_after_s3_cursor(obj, last_modified, object_name):
    """
    s3_objects テーブルに保存したカーソルより新しいオブジェクトかを判定する
    """
    if obj.last_modified > last_modified:
        return True
    if obj.last_modified < last_modified:
        return False
    return obj.object_name > object_name


def create_s3_object_table(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS s3_objects (type TEXT PRIMARY KEY, object_name TEXT, last_modified TIMESTAMPTZ)"
    )


def table_exists(con, table_name):
    rel = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name=?",
        (table_name,),
    )
    count = rel.fetchone()
    return count[0] > 0


def is_broken_db_error(error):
    """
    DB ファイルが破損しているかどうかを判定する
    """
    message = str(error).lower()
    # 下記のエラーメッセージが含まれている場合は DB ファイルが破損していると判断する
    return any(pattern in message for pattern in BROKEN_DB_ERROR_PATTERNS)


def move_broken_db(db_path):
    """
    DB ファイルが破損していると判断した場合、DB ファイルをリネームする
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    broken_db_path = f"{db_path}.broken.{timestamp}"
    shutil.move(db_path, broken_db_path)

    wal_path = f"{db_path}.wal"
    if os.path.exists(wal_path):
        shutil.move(wal_path, f"{broken_db_path}.wal")

    return broken_db_path


def prepare_db_for_init(db_path):
    """
    DB ファイルが存在する場合に、DB ファイルが破損していないかを確認する
    """

    if not os.path.exists(db_path):
        return

    try:
        with duckdb.connect(db_path) as con:
            con.execute("SELECT 1")
    except (
        duckdb.IOException,
        duckdb.InternalException,
        duckdb.FatalException,
    ) as error:
        print(f"Error occurred while connecting to DB: {error}")
        if is_broken_db_error(error):
            broken_db_path = move_broken_db(db_path)
            print(f"Detected broken DB file. moved to {broken_db_path}")
            return
    except Exception as error:
        print(f"Unexpected error occurred while connecting to DB: {error}")
        raise


def is_initialized_db(db_path):
    """
    DB ファイルの初期化が完了しているかどうかを確認する
    """

    if not os.path.exists(db_path):
        return False

    with duckdb.connect(db_path) as con:
        if not table_exists(con, "s3_objects"):
            return False

    return True


def update_s3_object_table(con, log_type, obj):
    con.execute(
        """
        MERGE INTO s3_objects AS target
        USING (SELECT ? AS type, ? AS object_name, ? AS last_modified) AS source
        ON target.type = source.type
        WHEN MATCHED THEN
            UPDATE SET object_name = source.object_name, last_modified = source.last_modified
        WHEN NOT MATCHED THEN
            INSERT (type, object_name, last_modified) VALUES (source.type, source.object_name, source.last_modified);
    """,
        (log_type, obj.object_name, obj.last_modified),
    )


def list_objects(client, bucket, prefix):
    # オブジェクトキーが時系列順とは限らない (UUID 等を含むケースがある) ため、
    # MinIO の start_after でカーソル以降を絞り込むのは取り逃しのリスクがあり使用しない。
    # last_modified が同値の場合のカーソル比較のため、object_name もソートキーに含める。
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    return sorted(
        objects, key=lambda obj: (obj.last_modified, obj.object_name), reverse=True
    )


def get_target_urls(bucket, objects):
    urls = []
    for obj in objects:
        # テーブル作成時に読み込むファイルのパスを作成
        urls.append(f"s3://{bucket}/{obj.object_name}")

    return urls


def escape_sql_string_literal(value):
    # DuckDB の ATTACH はファイルパスを文字列リテラルとして受け取るが、
    # プリペアドステートメントでバインドできないため、シングルクォートをエスケープして埋め込む。
    return value.replace("'", "''")


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


def s3_setup(
    con, s3_endpoint, s3_access_key_id, s3_secret_access_key, s3_use_ssl, s3_region
):
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET s3_url_style='path'")
    con.execute("SET s3_endpoint=?", (s3_endpoint,))
    con.execute("SET s3_access_key_id=?", (s3_access_key_id,))
    con.execute("SET s3_secret_access_key=?", (s3_secret_access_key,))
    con.execute("SET s3_use_ssl=?", (s3_use_ssl,))
    con.execute("SET s3_region=?", (s3_region,))


def create_log_table(con, table_name, target_urls):
    # s3://log/connection/2021/06/01/a.gz, s3://log/connection/2021/06/02/b.gz, ... のようなパスを想定
    # テーブル作成時は全てのファイルを読み込む
    # TODO: 指定した時間以降にするかは別途検討する
    duckdb_columns = load_columns()
    if table_name not in duckdb_columns:
        raise ValueError(
            f"Unknown table name: {table_name}. Available tables: {list(duckdb_columns.keys())}"
        )

    # テーブルが存在する場合はすぐにリターンする
    if table_exists(con, table_name):
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

    require_s3_credentials(args)

    client = minio.Minio(
        args.s3_endpoint,
        access_key=args.s3_access_key_id,
        secret_key=args.s3_secret_access_key,
        secure=args.s3_use_ssl,
    )

    with duckdb.connect(args.db) as con:
        s3_setup(
            con,
            args.s3_endpoint,
            args.s3_access_key_id,
            args.s3_secret_access_key,
            args.s3_use_ssl,
            args.s3_region,
        )
        sync_logs(con, client, args, "update")


def delete(args):
    if not os.path.exists(args.db):
        raise Exception("DB-FILE-NOT-FOUND")

    copy_file = ".".join([args.db, "copy"])

    deleted_rows = 0
    with duckdb.connect(args.db) as con:
        timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=args.retention_period
        )
        for target in LOG_TARGETS:
            deleted_rows += delete_log_by_timestamp(con, target, timestamp)

    # 削除された行がない場合は DB ファイルのコピーは作成せずに終了する
    if deleted_rows == 0:
        return

    try:
        with duckdb.connect() as con:
            # DB サイズ削減のため、DB ファイルをコピーする
            con.execute(f"ATTACH '{escape_sql_string_literal(args.db)}' AS db")
            con.execute(f"ATTACH '{escape_sql_string_literal(copy_file)}' AS copy")
            con.execute("COPY FROM DATABASE db TO copy")
    except Exception:
        # 処理に失敗したときの残る可能性のあるファイルを削除する
        remove_delete_incompleted_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise

    try:
        # コピーしたファイルを、元の DB ファイルに上書きする
        # other への書き込み権限は不要なので 0o644 に揃える
        os.chmod(
            copy_file,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
        )
        shutil.move(copy_file, args.db)
    except Exception:
        # 処理に失敗したときの残る可能性のあるファイルを削除する
        remove_delete_incompleted_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise


def insert_log_from_s3(con, client, table_name, args):
    cursor = select_s3_object(con, table_name)
    object_name, object_last_modified = cursor

    log_objects = list_objects(
        client, args.s3_bucket, f"{args.s3_prefix}/{table_name}/"
    )

    target_log_objects = [
        obj
        for obj in log_objects
        if is_after_s3_cursor(obj, object_last_modified, object_name)
    ]

    # 長時間停止後に大量ファイルが蓄積したケースに備え、古い方からバッチで取り込む。
    # 降順ソートされているため、末尾側 update_maximum_load 件が古い順のバッチになる。
    if len(target_log_objects) > args.update_maximum_load:
        target_log_objects = target_log_objects[-args.update_maximum_load :]

    if len(target_log_objects) == 0:
        return

    target_urls = get_target_urls(args.s3_bucket, target_log_objects)
    con.begin()
    try:
        insert_log(con, table_name, target_urls)
        # list_objects は (last_modified, object_name) の降順なので先頭がこのバッチの最新
        update_s3_object_table(con, table_name, target_log_objects[0])
        con.commit()
    except Exception:
        con.rollback()
        raise


def insert_log(con, table_name, target_urls):
    duckdb_columns = load_columns()
    if table_name not in duckdb_columns:
        raise ValueError(
            f"Unknown table name: {table_name}. Available tables: {list(duckdb_columns.keys())}"
        )

    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.insert_into(table_name)


def select_s3_object(con, log_type):
    return con.execute(
        "SELECT object_name, last_modified FROM s3_objects WHERE type=?",
        (log_type,),
    ).fetchone()


def delete_log_by_timestamp(con, table_name, timestamp):
    # table_name は SQL に直接埋め込むため、許可リストで縛る
    if table_name not in LOG_TARGETS:
        raise ValueError(
            f"Unknown table name: {table_name}. Available tables: {list(LOG_TARGETS)}"
        )

    if not table_exists(con, table_name):
        # テーブルが存在しない場合はスキップする
        # delete サブコマンドはテーブル名を指定して実行ではないため、テーブルが存在しない場合もエラーにはしない
        print(f"Table {table_name} does not exist.")
        return 0

    print(f"DELETE FROM {table_name} WHERE timestamp < '{timestamp}'")
    con.execute(f"DELETE FROM {table_name} WHERE timestamp < ?", (timestamp,))
    result = con.fetchone()
    return result[0]


def main():
    parser = argparse.ArgumentParser()
    # 共通オプション
    parser.add_argument("--db", default=DEFAULT_DUCKDB_FILE, help="DB file path")
    parser.add_argument("--s3_endpoint", default="s3.amazonaws.com", help="S3 endpoint")
    parser.add_argument("--s3_access_key_id", default=None, help="S3 access key id")
    parser.add_argument(
        "--s3_secret_access_key", default=None, help="S3 secret access key"
    )
    parser.add_argument("--s3_use_ssl", action="store_true", help="S3 use SSL")
    parser.add_argument("--s3_region", default=DEFAULT_S3_REGION, help="S3 region")
    parser.add_argument(
        "--s3_bucket", default=DEFAULT_S3_BUCKET_NAME, help="S3 bucket name"
    )
    parser.add_argument("--s3_prefix", default=DEFAULT_S3_PREFIX, help="S3 prefix")
    parser.add_argument(
        "--retention_period",
        default=DEFAULT_RETENTION_PERIOD,
        help="retention period",
        type=positive_int,
    )
    parser.add_argument(
        "--initial_maximum_load",
        default=DEFAULT_INITIAL_MAXIMUM_LOAD,
        help="Initial maximum load",
        type=positive_int,
    )
    parser.add_argument(
        "--update_maximum_load",
        default=DEFAULT_UPDATE_MAXIMUM_LOAD,
        help="Update maximum load",
        type=positive_int,
    )

    subparsers = parser.add_subparsers(required=True)
    subparsers_init = subparsers.add_parser("init")
    subparsers_init.set_defaults(func=init)

    subparsers_update = subparsers.add_parser("update")
    subparsers_update.set_defaults(func=update)

    subparsers_delete = subparsers.add_parser("delete")
    subparsers_delete.set_defaults(func=delete)

    args = parser.parse_args()
    db = args.db

    def exit_with_stderr(message):
        print(message, file=sys.stderr)
        sys.exit(1)

    def handle_storage_error(error):
        if isinstance(error, S3Error):
            if error.code == "NoSuchBucket":
                exit_with_stderr(f"S3 bucket not found: {args.s3_bucket}")
            exit_with_stderr(f"S3 error occurred (code={error.code}): {error.message}")
        raise error

    if args.func == init:
        # init は DB ファイルがない、または、DB にデータが入っていない場合のみ実行する想定のため、ファイル更新比較処理の対象外
        try:
            args.func(args)
        except Exception as error:
            handle_storage_error(error)
    else:
        # DB ファイルがない場合は終了する
        if not os.path.exists(db):
            parser.print_usage()
            sys.exit(1)
        else:
            # DB ファイルの最終更新時刻を取得する
            statinfo = os.stat(db)
            mtime = statinfo.st_mtime

            try:
                args.func(args)
            except Exception as error:
                handle_storage_error(error)

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

    # grafana から読み込むために other 読み取り権を付与する。書き込みは不要なので 0o644 に揃える
    os.chmod(
        tmp_file,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
    )
    readonly_file = ".".join([args.db, "readonly"])
    shutil.move(tmp_file, readonly_file)


if __name__ == "__main__":
    main()
