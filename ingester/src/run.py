import argparse
import datetime
import os
import shutil
import stat
import sys

import duckdb
import minio
import yaml
from minio.error import S3Error


class CliUsageError(Exception):
    """CLI ユーザーの入力・操作順序の不備を表す例外。

    handle_cli_error がユーザー向け 1 行メッセージで exit 1 にする経路に乗せる。
    内部用エラーは ValueError 等のまま上位に伝播させて区別する。
    """


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
# 破損 DB を connect したときに DuckDB が送出しうる例外クラス。
# prepare_db_for_init と check_db_not_broken の except タプルで共有する。
BROKEN_DB_CONNECT_ERRORS = (
    duckdb.IOException,
    duckdb.InternalException,
    duckdb.FatalException,
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

        with open(file_path) as f:
            columns = yaml.safe_load(f)
            if not isinstance(columns, dict):
                raise ValueError(
                    f"Invalid format in {file_path}, expected a dictionary."
                )
            duckdb_columns[target] = columns

    return duckdb_columns


def require_s3_credentials(args):
    if not args.s3_access_key_id or not args.s3_secret_access_key:
        raise CliUsageError(
            "S3 credentials are required: provide --s3_access_key_id and --s3_secret_access_key"
        )


def init(args):
    prepare_db_for_init(args.db)
    if has_s3_objects_table(args.db):
        print(
            f"s3_objects table is already present in DB: {args.db}; init skipped",
            file=sys.stderr,
        )
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
        create_s3_objects_table(con)

        s3_setup(con, args)
        for target in LOG_TARGETS:
            sync_log_for_init(con, client, args, target)


def initialize_log_table(con, client, args, target):
    """対象テーブルを初回作成する。

    list_objects は (last_modified, object_name) の降順で並ぶため、先頭側 initial_maximum_load
    件 (新しい側) のみを取り込み、カーソルは全体最新オブジェクトに進める。対象オブジェクトが
    無ければ何もしない。

    initial_maximum_load を超える古い側は「古すぎるデータを取り込まない」ため意図的に
    取り込まない (insert_log_from_s3 が古い側からバッチ取り込みする方針と非対称なのが正解)。
    """
    log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
    if len(log_objects) == 0:
        print(f"No log found for {target} in {args.s3_bucket}.", file=sys.stderr)
        return

    log_urls = get_target_urls(args.s3_bucket, log_objects[: args.initial_maximum_load])
    create_log_table(con, target, log_urls)
    # 先頭が全体最新
    update_s3_objects_table(con, target, log_objects[0])


def sync_log_for_init(con, client, args, target):
    try:
        initialize_log_table(con, client, args, target)
    except duckdb.InvalidInputException as e:
        # 対象 target で InvalidInputException が出ても残りの LOG_TARGETS を止めないため、
        # stderr に記録して次の target へ進む。 発生要因の例: read_json のスキーマ不一致など。
        print(f"InvalidInputException ({target}): {e}", file=sys.stderr)


def sync_log_for_update(con, client, args, target):
    cursor = select_s3_objects_row(con, target)
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


def is_after_s3_cursor(
    *, obj_last_modified, obj_object_name, cursor_last_modified, cursor_object_name
):
    """
    s3_objects テーブルに保存したカーソルより新しいオブジェクトかを判定する。

    obj_last_modified と cursor_last_modified の両方がタイムゾーン情報を含んでいることを前提とする。
    MinIO SDK の Object.last_modified と DuckDB の TIMESTAMPTZ カラムはどちらも
    タイムゾーン情報を含む datetime を返すため、タイムゾーン情報を含まない datetime が
    渡るのは設計違反として明示的に拒否する。
    """
    if obj_last_modified.tzinfo is None:
        raise ValueError("S3 object has a timezone-naive last_modified timestamp")
    if cursor_last_modified.tzinfo is None:
        raise ValueError("S3 cursor has a timezone-naive last_modified timestamp")
    if obj_last_modified > cursor_last_modified:
        return True
    if obj_last_modified < cursor_last_modified:
        return False
    return obj_object_name > cursor_object_name


def create_s3_objects_table(con):
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
    return any(pattern in message for pattern in BROKEN_DB_ERROR_PATTERNS)


def move_broken_db(db_path):
    """DB ファイルが破損していると判断した場合に .broken.<timestamp> へリネームする。

    タイムスタンプはマイクロ秒まで含めて crash loop による連続退避時の衝突を抑える。
    """
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S%f")
    broken_db_path = f"{db_path}.broken.{timestamp}"
    shutil.move(db_path, broken_db_path)

    wal_path = f"{db_path}.wal"
    if os.path.exists(wal_path):
        shutil.move(wal_path, f"{broken_db_path}.wal")

    return broken_db_path


def _detect_broken_db(db_path):
    """DB ファイルが破損していれば True、 正常または不在なら False を返す。

    破損以外の接続エラー (ロック競合、 権限不足等) は呼び出し元へ伝播させる。
    """
    if not os.path.exists(db_path):
        return False
    try:
        with duckdb.connect(db_path) as con:
            con.execute("SELECT 1")
    except BROKEN_DB_CONNECT_ERRORS as error:
        if is_broken_db_error(error):
            return True
        raise
    return False


def prepare_db_for_init(db_path):
    """DB ファイルが破損していれば .broken.<timestamp> に退避する。

    破損以外の接続エラー (ロック競合、 権限不足等) は呼び出し元へ伝播させる
    (握りつぶすと直後の has_s3_objects_table で同じ例外を再発させ、 ユーザーに二重出力
    させてしまうため)。
    """
    if not _detect_broken_db(db_path):
        return
    broken_db_path = move_broken_db(db_path)
    print(
        f"Detected broken DB file. moved to {broken_db_path}",
        file=sys.stderr,
    )


def has_s3_objects_table(db_path):
    """DB ファイルに s3_objects テーブルが存在するかを判定する。

    init は完了判定に使い、 update / delete は事前チェックに使う。
    s3_objects テーブルの「存在」 のみを見て、 行数や LOG_TARGETS テーブルの有無は見ない。
    中途半端な DB (s3_objects テーブルあり、 LOG_TARGETS 一部欠落) は update 経路の
    sync_log_for_update で復旧する設計。
    """

    if not os.path.exists(db_path):
        return False

    with duckdb.connect(db_path) as con:
        if not table_exists(con, "s3_objects"):
            return False

    return True


def check_db_not_broken(db_path):
    """update / delete の前処理として DB 破損を検出し、 init の再実行を促して終了する。

    init は prepare_db_for_init で自動退避するが、 update / delete では運用者の判断を
    優先するため自動退避しない。 破損以外 (ロック競合、 権限不足等) は呼び出し元へ伝播。
    """
    if not _detect_broken_db(db_path):
        return
    exit_with_stderr(
        f"DB file is broken: {db_path}. Move or remove the file and run 'init' to re-initialize."
    )


UPSERT_S3_OBJECT_SQL = """
MERGE INTO s3_objects AS target
USING (SELECT ? AS type, ? AS object_name, ? AS last_modified) AS source
ON target.type = source.type
WHEN MATCHED THEN
    UPDATE SET object_name = source.object_name, last_modified = source.last_modified
WHEN NOT MATCHED THEN
    INSERT (type, object_name, last_modified) VALUES (source.type, source.object_name, source.last_modified);
"""


def update_s3_objects_table(con, log_type, obj):
    con.execute(UPSERT_S3_OBJECT_SQL, (log_type, obj.object_name, obj.last_modified))


def list_objects(client, bucket, prefix):
    """指定 prefix 配下のオブジェクトを (last_modified, object_name) の降順で返す。

    戻り値の先頭が最新のオブジェクト、最後が最古のオブジェクトになる。
    last_modified が同値の場合は object_name の辞書順降順で並ぶ
    (is_after_s3_cursor のカーソル比較順序と一致させるため)。

    オブジェクトキーが時系列順とは限らない (UUID 等を含むケースがある) ため、
    MinIO の start_after でカーソル以降を絞り込むのは取り逃しのリスクがあり使用しない。
    """
    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    return sorted(
        objects, key=lambda obj: (obj.last_modified, obj.object_name), reverse=True
    )


def get_target_urls(bucket, objects):
    """テーブル作成や insert で DuckDB の read_json に渡すための s3://bucket/key URL リストを生成する。"""
    urls = []
    for obj in objects:
        urls.append(f"s3://{bucket}/{obj.object_name}")

    return urls


def escape_sql_string_literal(path_literal):
    """DuckDB の ATTACH 等で使う SQL 文字列リテラルとして path_literal を安全に埋め込めるよう
    シングルクォートをエスケープする。

    DuckDB はファイルパスをプリペアドステートメントでバインドできないため、 ATTACH 直前に
    呼んでパス文字列を直接埋め込む用途。 制御文字 (0x00 から 0x1F および 0x7F) を含む値は
    DuckDB パーサで予期せぬ挙動を起こす可能性があるため CliUsageError で拒否する。
    ATTACH を通らない `duckdb.connect(args.db)` 等の経路は本関数の対象外で、 そこで NUL
    バイト等が混入したときは Python 側の `embedded null byte` ValueError 等に委ねる
    (args.db は argparse 経由の CLI 引数で外部入力ではないため、 入口での網羅検証は持たない)。
    """
    for i, c in enumerate(path_literal):
        if ord(c) < 0x20 or ord(c) == 0x7F:
            # path_literal[:40]!r はログが肥大化しないように先頭の 40 文字に絞る。 また
            # repr で制御文字を `\xNN` 形式に視覚化し、 ログ表示や grep を壊さないようにする。
            raise CliUsageError(
                "SQL string literal must not contain control characters: "
                f"U+{ord(c):04X} at index {i} in {path_literal[:40]!r}"
            )
    return path_literal.replace("'", "''")


def remove_delete_incomplete_copy_files(copyfile):
    """
    削除処理が失敗した時に残る可能性があるコピー先の .copy ファイルと .copy.wal ファイルを削除する
    """
    for file in (copyfile, f"{copyfile}.wal"):
        try:
            os.remove(file)
        except FileNotFoundError:
            pass


def s3_setup(con, args):
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET s3_url_style='path'")
    con.execute("SET s3_endpoint=?", (args.s3_endpoint,))
    con.execute("SET s3_access_key_id=?", (args.s3_access_key_id,))
    con.execute("SET s3_secret_access_key=?", (args.s3_secret_access_key,))
    con.execute("SET s3_use_ssl=?", (args.s3_use_ssl,))
    con.execute("SET s3_region=?", (args.s3_region,))


def require_known_table(table_name, allowed):
    """許可リストに含まれないテーブル名を ValueError で拒否する。

    create_log_table / insert_log は load_columns() が返す dict を、
    delete_log_by_timestamp は LOG_TARGETS タプルを渡す。 dict も in 演算子で
    キー検査になるため、 呼び出し側で keys() を渡す必要はない。
    SQL に直接埋め込む経路 (delete_log_by_timestamp) と read_json/create 系
    (create_log_table / insert_log) の両方で同じ防御チェックを使う。
    """
    if table_name not in allowed:
        raise ValueError(
            f"Unknown table name: {table_name}. Available tables: {list(allowed)}"
        )


def create_log_table(con, table_name, target_urls):
    """指定された S3 オブジェクト URL から DuckDB テーブルを新規作成する。

    target_urls の JSON 内容を読み込み、DUCKDB_COLUMNS で定義したスキーマでテーブル化する。
    既にテーブルが存在する場合は何もしない。LOG_TARGETS 外のテーブル名は ValueError で弾く。
    """
    duckdb_columns = load_columns()
    require_known_table(table_name, duckdb_columns)

    if table_exists(con, table_name):
        print(f"Table {table_name} already exists.", file=sys.stderr)
        return

    # テーブルを作成する
    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.create(table_name)
    print(f"Created table {table_name} from {len(target_urls)} object(s).")


def update(args):
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}")

    check_db_not_broken(args.db)

    # s3_objects テーブル不在の DB に対しては update を拒否する。init が未実行のまま
    # update を呼ぶと select_s3_objects_row が CatalogException で落ちるため、明示的に弾く。
    if not has_s3_objects_table(args.db):
        raise CliUsageError(
            f"s3_objects table not found in DB: {args.db}. Run 'init' first."
        )

    require_s3_credentials(args)

    client = minio.Minio(
        args.s3_endpoint,
        access_key=args.s3_access_key_id,
        secret_key=args.s3_secret_access_key,
        secure=args.s3_use_ssl,
    )

    with duckdb.connect(args.db) as con:
        s3_setup(con, args)
        for target in LOG_TARGETS:
            sync_log_for_update(con, client, args, target)


def delete(args):
    """retention_period 日より古いログを削除し、 削除後の DB を新しい DB へ COPY して詰め直す。

    DELETE 発行後、 in-memory DuckDB から ATTACH + COPY FROM DATABASE で空き領域を詰めた
    DB を copy_file へ書き出し、 shutil.move で元 DB を置き換える。

    copy_file は args.db と同一ディレクトリに置くため、 shutil.move は内部で os.rename を
    呼び POSIX 上 atomic に振る舞う。 すなわち args.db が「move 部分成功で書き換わって
    壊れる」 状態で残ることはない前提で except 経路を組んでいる。 copy_file を args.db と
    別のファイルシステムに置くと、 shutil.move は os.rename ではなく copy + remove に
    切り替わって atomic でなくなり、 args.db が書きかけのまま残り得るので、 その場合は
    args.db の退避処理を追加すること。

    .wal の残骸は check_db_not_broken の破損検出で吸収する想定で、 明示的な削除は加えない。

    0o660 への chmod は COPY 経路の副次効果なので、 削除 0 件のときは正規化されない
    (元 DB のパーミッションは init / sync の umask で揃える前提)。
    """
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}")

    check_db_not_broken(args.db)

    copy_file = f"{args.db}.copy"

    # 前回 delete が SIGKILL や OOM 等で異常終了して残った .copy と .copy.wal を掃除してから
    # 始める。 残っていると後段の ATTACH '{copy_file}' AS copy が既存ファイルを開いてしまい、
    # COPY FROM DATABASE で古いスキーマと新本体データが混ざる可能性があるため。
    # 同一 delete 内で失敗した場合の掃除は except 内で別途行う。
    remove_delete_incomplete_copy_files(copy_file)

    deleted_rows = 0
    with duckdb.connect(args.db) as con:
        timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
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
        remove_delete_incomplete_copy_files(copy_file)
        # return code を 0 以外にするため例外を呼び出し元に投げる
        raise


def insert_log_from_s3(con, client, table_name, bucket, prefix, update_maximum_load):
    cursor = select_s3_objects_row(con, table_name)
    object_name, object_last_modified = cursor

    log_objects = list_objects(client, bucket, f"{prefix}/{table_name}/")

    target_log_objects = [
        obj
        for obj in log_objects
        if is_after_s3_cursor(
            obj_last_modified=obj.last_modified,
            obj_object_name=obj.object_name,
            cursor_last_modified=object_last_modified,
            cursor_object_name=object_name,
        )
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
        update_s3_objects_table(con, table_name, target_log_objects[0])
        con.commit()
    except Exception:
        con.rollback()
        raise


def insert_log(con, table_name, target_urls):
    duckdb_columns = load_columns()
    require_known_table(table_name, duckdb_columns)

    columns = duckdb_columns[table_name]
    rel = con.read_json(target_urls, union_by_name=True, columns=columns)
    rel.insert_into(table_name)


def select_s3_objects_row(con, log_type):
    return con.execute(
        "SELECT object_name, last_modified FROM s3_objects WHERE type=?",
        (log_type,),
    ).fetchone()


def delete_log_by_timestamp(con, table_name, timestamp):
    # table_name は SQL に直接埋め込むため、許可リストで縛る
    require_known_table(table_name, LOG_TARGETS)

    if not table_exists(con, table_name):
        # テーブルが存在しない場合はスキップする
        # delete サブコマンドはテーブル名を指定して実行ではないため、テーブルが存在しない場合もエラーにはしない
        print(f"Table {table_name} does not exist.", file=sys.stderr)
        return 0

    con.execute(f"DELETE FROM {table_name} WHERE timestamp < ?", (timestamp,))
    deleted_rows = con.fetchone()[0]
    print(f"Deleted {deleted_rows} rows from {table_name}.")
    return deleted_rows


def exit_with_stderr(message):
    """エラーメッセージを stderr に書き出して exit code 1 で終了する。"""
    print(message, file=sys.stderr)
    sys.exit(1)


def should_create_readonly(db_path, initial_mtime, initial_size):
    """initial_mtime / initial_size と現在の値を比較して、 readonly コピーを生成すべきかを返す。

    DB ファイルが書き換わったかを mtime と st_size の両方で判定し、 どちらか異なれば
    .readonly を生成する。 mtime は OS とファイルシステムによっては秒粒度に丸められるため、
    同一秒内で書き込みと比較が完了するケースは st_size の変化で検出する。
    """
    if not os.path.exists(db_path):
        return False
    current = os.stat(db_path)
    return (initial_mtime, initial_size) != (current.st_mtime, current.st_size)


def create_readonly_copy(db_path):
    """書き込み済みの DB ファイルから読み込み専用コピーを生成する。

    DuckDB は書き込み中に他プロセスからアクセスできないため、書き込み終了後に同 FS 内で
    一時ファイルを作成し、rename で .readonly に切り替えることでアトミックな差し替えにする。
    Grafana は .readonly のみを参照する想定。
    参考: https://github.com/motherduckdb/grafana-duckdb-datasource?tab=readme-ov-file#updating-data-in-the-duckdb-file
    """
    tmp_file = f"{db_path}.tmp"
    # 前回の create_readonly_copy が copyfile と move の間で異常終了して .tmp が残っても、
    # 直後の copyfile が上書きするため事前削除は不要。 delete の .copy は ATTACH で開かれ
    # 古いスキーマが混ざる危険があるため掃除するが、 .tmp にはその経路がなく非対称でよい。
    shutil.copyfile(db_path, tmp_file)
    # other の読み込み権限、書き込み権限は不要なので 0o660 に揃える
    os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    readonly_file = f"{db_path}.readonly"
    shutil.move(tmp_file, readonly_file)


def handle_cli_error(error, bucket):
    """main から呼び出された関数の例外を分類して整形する CLI トップレベル例外ハンドラ。

    S3Error / FileNotFoundError / CliUsageError をユーザー向け 1 行メッセージで exit 1 にする。
    それ以外はトレースバック付きで上位に伝播させる。 bucket は NoSuchBucket メッセージ用
    の表示値として受け取る。
    """
    if isinstance(error, S3Error):
        if error.code == "NoSuchBucket":
            exit_with_stderr(f"S3 bucket not found: {bucket}")
        else:
            exit_with_stderr(f"S3 error occurred (code={error.code}): {error.message}")
    elif isinstance(error, (FileNotFoundError, CliUsageError)):
        exit_with_stderr(str(error))
    else:
        raise error


def main():
    # --help にデフォルト値を自動表示するため、 ArgumentDefaultsHelpFormatter を使う。
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
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
        help="Retention period in days",
        type=positive_int,
    )
    parser.add_argument(
        "--initial_maximum_load",
        default=DEFAULT_INITIAL_MAXIMUM_LOAD,
        help="Max S3 objects per init",
        type=positive_int,
    )
    parser.add_argument(
        "--update_maximum_load",
        default=DEFAULT_UPDATE_MAXIMUM_LOAD,
        help="Max S3 objects per update",
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

    # DB ファイルが存在すれば mtime と st_size を、 無ければ両方 None を取る。
    if os.path.exists(args.db):
        stat_result = os.stat(args.db)
        initial_mtime = stat_result.st_mtime
        initial_size = stat_result.st_size
    else:
        initial_mtime = None
        initial_size = None

    try:
        args.func(args)
    except Exception as error:
        handle_cli_error(error, args.s3_bucket)

    if should_create_readonly(args.db, initial_mtime, initial_size):
        create_readonly_copy(args.db)


if __name__ == "__main__":
    main()
