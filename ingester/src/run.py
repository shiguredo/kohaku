import argparse
import enum
import os
import shutil
import stat
import datetime
import sys

import yaml
import duckdb
import minio
from minio.error import S3Error


class SyncMode(enum.Enum):
    """sync_logs の動作モード。"""

    INIT = "init"
    UPDATE = "update"


DEFAULT_DUCKDB_FILE = "duck.db"
# /kohaku/log/rtc_stats/2025/06/01/a.gz のようなパスを想定
DEFAULT_S3_BUCKET_NAME = "kohaku"
DEFAULT_S3_PREFIX = "log"

DEFAULT_S3_REGION = "ap-northeast-1"
DEFAULT_RETENTION_PERIOD = 7
# init 時に読み込むファイル数の上限。古すぎるデータを取り込まないために
# ユーザーが指定する上限であり、超過した古い側オブジェクトは意図的に取り込まれない。
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
    """LOG_TARGETS 各テーブルのカラム定義 YAML をロードして辞書として返す。"""
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
    prepare_db_for_init(args.db)
    if is_initialized_db(args.db):
        return

    # 初期化が必要なケースに限り S3 接続が必要なので、ここで認証情報を要求する。
    require_s3_credentials(args)

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
        sync_logs(con, client, args, SyncMode.INIT)


def sync_logs(con, client, args, mode):
    for target in LOG_TARGETS:
        if mode is SyncMode.INIT:
            sync_log_for_init(con, client, args, target)
        elif mode is SyncMode.UPDATE:
            sync_log_for_update(con, client, args, target)
        else:
            raise ValueError(f"Unknown mode: {mode}")


def initialize_log_table(con, client, args, target):
    """対象テーブルを初回作成する。

    list_objects は (last_modified, object_name) の降順 (新しい順) で並ぶため、先頭側
    initial_maximum_load 件 = 新しい側 N 件のみを取り込んでテーブルを作成し、カーソルは
    log_objects[0] (= 全体最新オブジェクト) に進める。対象オブジェクトが無ければ何もしない。

    initial_maximum_load を超えるオブジェクトが S3 上に存在する場合、超過した古い側は
    意図的に取り込まない。これは「古すぎるデータを取り込まない」ためにユーザーが指定する
    上限であり、超過分は DB に取り込まれず S3 に残り続ける。カーソルを全体最新まで進める
    ことで、以降の update では新たに到着したオブジェクトのみが取り込まれる。

    insert_log_from_s3 は逆に「停止後の復帰時に大量蓄積したログを古い側からバッチで取り
    込む」目的のため古い側 (末尾側) を取るが、init は新しい側 (先頭側) を取る。この非対称は
    両関数の目的が異なるためで意図的なものである。
    """
    log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
    if len(log_objects) == 0:
        print(f"No log found for {target} in {args.s3_bucket}.")
        return

    log_urls = get_target_urls(args.s3_bucket, log_objects[: args.initial_maximum_load])
    create_log_table(con, target, log_urls)
    # 先頭が全体最新
    update_s3_object_table(con, target, log_objects[0])


def sync_log_for_init(con, client, args, target):
    try:
        initialize_log_table(con, client, args, target)
    except duckdb.InvalidInputException as e:
        # まだディレクトリがないため、エラーを表示して次へ
        print(f"InvalidInputException ({target}): {e}")


def sync_log_for_update(con, client, args, target):
    cursor = select_s3_object(con, target)
    if cursor is None:
        # 対象 log_type が初登場するケース (init 時点で該当ターゲットの S3 オブジェクトが
        # 1 件も無く、s3_objects にも行が作られなかった状況であとから登場した場合)。
        # update 経路でも新規初期化として initialize_log_table を呼ぶ。取り込み件数の上限は
        # initialize_log_table の仕様通り initial_maximum_load を用いる。
        initialize_log_table(con, client, args, target)
    else:
        # テーブルが存在しているのでログを追加する
        insert_log_from_s3(
            con,
            client,
            target,
            args.s3_bucket,
            args.s3_prefix,
            args.update_maximum_load,
        )


def is_after_s3_cursor(obj, last_modified, object_name):
    """
    s3_objects テーブルに保存したカーソルより新しいオブジェクトかを判定する。

    obj.last_modified と last_modified の両方がタイムゾーン情報を含んでいることを前提とする。
    MinIO SDK の Object.last_modified と DuckDB の TIMESTAMPTZ カラムはどちらも
    タイムゾーン情報を含む datetime を返すため、タイムゾーン情報を含まない datetime が
    渡るのは設計違反として明示的に拒否する。

    MinIO SDK の last_modified と DuckDB の TIMESTAMPTZ はどちらも現状マイクロ秒精度のため
    精度差による誤判定は起きないが、将来 DuckDB や MinIO SDK の精度が変わると、同一オブジェクト
    がカーソルより僅かに古いと判定されて重複取り込みが起きる可能性がある。実害が確認されたら
    比較前に明示的に切り捨てる等の対策を検討する。
    """
    if obj.last_modified is None or last_modified is None:
        raise ValueError("S3 object cursor has a missing last_modified timestamp")
    if (obj.last_modified.tzinfo is None) or (last_modified.tzinfo is None):
        raise ValueError(
            "S3 object cursor has a timezone-naive last_modified timestamp"
        )
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
    DB ファイルが破損していると判断した場合、DB ファイルをリネームする。

    タイムスタンプにはマイクロ秒まで含める。crash loop 等で短時間に複数回 init が走り、
    同じパスの DB を連続して破損退避するケースで、退避先 (.broken.<ts>) が衝突して
    shutil.move による上書きでフォレンジック情報を失うことを防ぐ。
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
    broken_db_path = f"{db_path}.broken.{timestamp}"
    shutil.move(db_path, broken_db_path)

    wal_path = f"{db_path}.wal"
    if os.path.exists(wal_path):
        shutil.move(wal_path, f"{broken_db_path}.wal")

    return broken_db_path


def prepare_db_for_init(db_path):
    """
    DB ファイルが存在する場合に、DB ファイルが破損していないかを確認する。

    DB が正常な状態であれば何もしない。破損と判定された場合のみ .broken.<timestamp>
    に退避する。それ以外の接続エラー (ロック競合、権限不足等) は呼び出し元へ伝播
    させる。握りつぶして return すると直後の is_initialized_db が同じパスへ再
    connect して同じ例外を再発させ、ユーザーに二重出力を見せてしまうため、明示的に
    raise する。
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
        if not is_broken_db_error(error):
            # 破損以外のエラー (ロック競合、権限不足等) は退避せず呼び出し元へ伝播させる
            raise
        broken_db_path = move_broken_db(db_path)
        print(f"Detected broken DB file. moved to {broken_db_path}")


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


def check_db_not_broken(db_path):
    """update / delete の前処理として DB 破損を検出する。

    破損と判定したら exit_with_stderr で終了し、運用者に init 再実行を促す。
    init は prepare_db_for_init で自動退避するが、update / delete では運用者の判断を
    優先するため自動退避せず、エラーメッセージで意思決定の主導権を運用者に残す。
    破損以外 (ロック競合、権限不足等) は呼び出し元へ伝播させる。
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
        if is_broken_db_error(error):
            exit_with_stderr(
                f"DB file is broken: {db_path}. Move or remove the file and run 'init' to re-initialize."
            )
        raise


_UPSERT_S3_OBJECT_SQL = """
MERGE INTO s3_objects AS target
USING (SELECT ? AS type, ? AS object_name, ? AS last_modified) AS source
ON target.type = source.type
WHEN MATCHED THEN
    UPDATE SET object_name = source.object_name, last_modified = source.last_modified
WHEN NOT MATCHED THEN
    INSERT (type, object_name, last_modified) VALUES (source.type, source.object_name, source.last_modified);
"""


def update_s3_object_table(con, log_type, obj):
    con.execute(_UPSERT_S3_OBJECT_SQL, (log_type, obj.object_name, obj.last_modified))


def list_objects(client, bucket, prefix):
    """指定 prefix 配下のオブジェクトを (last_modified, object_name) の降順で返す。

    戻り値の先頭が最新のオブジェクト、最後が最古のオブジェクトになる。
    last_modified が同値の場合は object_name の辞書順降順で並ぶ
    (is_after_s3_cursor のカーソル比較順序と一致させるため)。

    オブジェクトキーが時系列順とは限らない (UUID 等を含むケースがある) ため、
    MinIO の start_after でカーソル以降を絞り込むのは取り逃しのリスクがあり使用しない。
    """
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
    """DuckDB の ATTACH 等で使う SQL 文字列リテラルとして value を安全に埋め込めるよう
    シングルクォートをエスケープする。

    DuckDB はファイルパスをプリペアドステートメントでバインドできないため、ATTACH 等で
    パス文字列を直接埋め込む必要がある。本関数は信頼された CLI 引数 (args.db 等) のみを
    通す想定で、制御文字 (0x00 から 0x1f および 0x7f) を含む値は ValueError で拒否する。
    NUL バイトはファイルパスとして無効、改行や DEL 等は DuckDB パーサで予期せぬ挙動を
    起こす可能性があるため、暗黙の補正でなく明示的に弾く。
    """
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError("SQL string literal must not contain control characters")
    return value.replace("'", "''")


def remove_delete_incompleted_copy_files(copyfile):
    """
    削除処理が失敗した時に残る可能性があるコピー先の .copy ファイルと .copy.wal ファイルを削除する
    """
    for file in (copyfile, f"{copyfile}.wal"):
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
    """指定された S3 オブジェクト URL から DuckDB テーブルを新規作成する。

    target_urls の JSON 内容を読み込み、DUCKDB_COLUMNS で定義したスキーマでテーブル化する。
    既にテーブルが存在する場合は何もしない。LOG_TARGETS 外のテーブル名は ValueError で弾く。
    """
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
    print(f"Creating table {table_name} from {len(target_urls)} object(s).")
    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.create(table_name)


def update(args):
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}")

    check_db_not_broken(args.db)

    # s3_objects テーブル不在の DB に対しては update を拒否する。init が未実行のまま
    # update を呼ぶと select_s3_object が CatalogException で落ちるため、明示的に弾く。
    if not is_initialized_db(args.db):
        raise ValueError(f"DB file is not initialized: {args.db}. Run 'init' first.")

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
        sync_logs(con, client, args, SyncMode.UPDATE)


def delete(args):
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}")

    check_db_not_broken(args.db)

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

        # コピーしたファイルを、元の DB ファイルに上書きする
        # other の読み込み権限、書き込み権限は不要なので 0o660 に揃える
        os.chmod(
            copy_file,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP,
        )
        shutil.move(copy_file, args.db)
    except Exception:
        # 処理に失敗したときの残る可能性のあるファイルを削除する
        remove_delete_incompleted_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise


def insert_log_from_s3(con, client, table_name, bucket, prefix, update_maximum_load):
    if update_maximum_load is None:
        raise ValueError("update_maximum_load is required but got None")
    cursor = select_s3_object(con, table_name)
    object_name, object_last_modified = cursor

    log_objects = list_objects(client, bucket, f"{prefix}/{table_name}/")

    target_log_objects = [
        obj
        for obj in log_objects
        if is_after_s3_cursor(obj, object_last_modified, object_name)
    ]

    # 長時間停止後に大量ファイルが蓄積したケースに備え、古い方からバッチで取り込む。
    # 降順ソートされているため、末尾側 update_maximum_load 件が古い順のバッチになる。
    # update_maximum_load を超えた新しい側のオブジェクトは今回取り込まず、次回以降の
    # update で残りを取得する。カーソルをバッチ内最新までしか進めないため、is_after_s3_cursor
    # で次回 True と判定されて順次取り込まれる。
    if len(target_log_objects) > update_maximum_load:
        target_log_objects = target_log_objects[-update_maximum_load:]

    if len(target_log_objects) == 0:
        return

    target_urls = get_target_urls(bucket, target_log_objects)
    con.begin()
    try:
        insert_log(con, table_name, target_urls)
        # 先頭がこのバッチの最新
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

    con.execute(f"DELETE FROM {table_name} WHERE timestamp < ?", (timestamp,))
    result = con.fetchone()
    if result is None:
        raise RuntimeError(f"DELETE on {table_name} returned no row")
    deleted_rows = result[0]
    print(f"Deleted {deleted_rows} rows from {table_name}.")
    return deleted_rows


def exit_with_stderr(message):
    """エラーメッセージを stderr に書き出して exit code 1 で終了する。"""
    print(message, file=sys.stderr)
    sys.exit(1)


def create_readonly_copy(db_path):
    """書き込み済みの DB ファイルから読み込み専用コピーを生成する。

    DuckDB は書き込み中に他プロセスからアクセスできないため、書き込み終了後に同 FS 内で
    一時ファイルを作成し、rename で .readonly に切り替えることでアトミックな差し替えにする。
    Grafana は .readonly のみを参照する想定。
    参考: https://github.com/motherduckdb/grafana-duckdb-datasource?tab=readme-ov-file#updating-data-in-the-duckdb-file
    """
    tmp_file = ".".join([db_path, "tmp"])
    shutil.copyfile(db_path, tmp_file)
    # other の読み込み権限、書き込み権限は不要なので 0o660 に揃える
    os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    readonly_file = ".".join([db_path, "readonly"])
    shutil.move(tmp_file, readonly_file)


def handle_cli_error(error, bucket):
    """CLI トップレベル例外ハンドラ。main から呼び出された関数の例外を分類して整形する。

    ストレージ系の S3Error だけでなく、DB ファイル不在の FileNotFoundError や
    require_s3_credentials などの入力バリデーション失敗で送出される ValueError も
    併せて受けるため、命名は storage 限定にせず CLI 全般のエラーハンドラとして扱う。

    既知の例外は exit_with_stderr で終了し、それ以外は呼び出し元へ再送出する。
    bucket は NoSuchBucket メッセージ用の表示値として受け取る。
    """
    if isinstance(error, S3Error):
        if error.code == "NoSuchBucket":
            exit_with_stderr(f"S3 bucket not found: {bucket}")
        else:
            exit_with_stderr(f"S3 error occurred (code={error.code}): {error.message}")
    elif isinstance(error, (FileNotFoundError, ValueError)):
        # DB ファイル不在 (FileNotFoundError) や require_s3_credentials などの入力
        # バリデーション失敗 (ValueError) は、トレースバックなしで原因のみ表示して終了する
        exit_with_stderr(str(error))
    else:
        raise error


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
        help=(
            "Maximum number of S3 objects to import in init. "
            "Older objects beyond this limit are intentionally skipped."
        ),
        type=positive_int,
    )
    parser.add_argument(
        "--update_maximum_load",
        default=DEFAULT_UPDATE_MAXIMUM_LOAD,
        help=(
            "Maximum number of S3 objects to import per update call. "
            "Used to split a large backlog accumulated during downtime into batches."
        ),
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

    # update / delete の DB 不在チェックは args.func 内で FileNotFoundError を送出して
    # handle_cli_error に集約する。init は DB 不在からの新規作成も扱うため、いずれの
    # サブコマンドでも「存在すれば mtime、無ければ None」を共通で initial_mtime に入れる。
    initial_mtime = os.stat(db).st_mtime if os.path.exists(db) else None

    try:
        args.func(args)
    except Exception as error:
        handle_cli_error(error, args.s3_bucket)

    # DB ファイルが書き換わったかを mtime で判定し、変化が無ければ .readonly 生成をスキップする。
    # init で何もしなかったケース (既に初期化済み) は initial_mtime と一致してスキップされる。
    # init で DB を新規作成したケースは initial_mtime=None と新 mtime が一致せず readonly を生成する。
    if not os.path.exists(db):
        return
    if initial_mtime == os.stat(db).st_mtime:
        return

    create_readonly_copy(args.db)


if __name__ == "__main__":
    main()
