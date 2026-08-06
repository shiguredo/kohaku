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

    例外分類の対応表 (handle_cli_error のディスパッチ先):
      - S3Error: 1 行メッセージで exit 1
      - FileNotFoundError (DB 不在): 1 行メッセージで exit 1
      - CliUsageError (本例外): 1 行メッセージで exit 1
      - RuntimeError (デプロイ不備、 例: load_columns の YAML 欠損): トレースバック伝播
      - ValueError (内部 invariant 違反、 例: Unknown table name): トレースバック伝播
      - その他: トレースバック伝播
    """


DEFAULT_DUCKDB_FILE = "duck.db"
# /kohaku/log/rtc_stats/2025/06/01/a.gz のようなパスを想定
DEFAULT_S3_BUCKET_NAME = "kohaku"
DEFAULT_S3_PREFIX = "log"

DEFAULT_S3_REGION = "ap-northeast-1"
DEFAULT_RETENTION_PERIOD = 7
# init 時に読み込むファイル数の上限。古すぎるデータを取り込まないために
# ユーザーが指定する上限であり、超過した古い側オブジェクトは意図的に取り込まれない。
# デフォルト値 1000 は複数 fluent-bit (10 台) 構成で単一運用と同等の約 8 時間
# (500 分) のカバーを実現する値 (カバーしたい分数 500 ÷ upload_timeout 5 分 ×
# 10 台)。20 台構成では約 4 時間。単一運用では初回取り込み量が 10 倍になるが、
# 対象オブジェクトは gzip 圧縮済みの小さな JSON オブジェクトで実害は限定的。
DEFAULT_INITIAL_MAXIMUM_LOAD = 1000
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
# is_db_broken の except タプル (prepare_db_for_init / raise_if_db_broken は is_db_broken
# 経由でこの分類を共有する)。
BROKEN_DB_CONNECT_ERRORS = (
    duckdb.IOException,
    duckdb.InternalException,
    duckdb.FatalException,
)

# Sora のログテーブル名兼 DuckDB のテーブル名
LOG_TARGETS = (
    # 現行のダッシュボードでは connection を使用していないため、使用する場合はコメントアウトを外す
    # "connection",
    "rtc_stats",
    "session_webhook",
)


def positive_int(value):
    int_value = int(value)
    if int_value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return int_value


def load_columns(targets=LOG_TARGETS, columns_dir=COLUMNS_DIR):
    """targets 各テーブルのカラム定義 YAML を columns_dir から読み込んで辞書として返す。

    戻り値は `{target: {"columns": {カラム名: 型}, "primary_key": [PK カラム名...]}}` の形式。
    primary_key が定義されていないテーブルは空リストになる。

    YAML 欠損はデプロイ / イメージビルド側の不備なので RuntimeError で送出し、
    handle_cli_error では拾わずトレースバック付きで上位に伝播させる (「Run 'init'」
    のようなユーザー向け 1 行メッセージにはしない)。

    引数はデフォルトで LOG_TARGETS / COLUMNS_DIR を利用する。
    """
    duckdb_columns = {}
    for target in targets:
        file_path = os.path.join(columns_dir, f"{target}.yml")
        if not os.path.exists(file_path):
            raise RuntimeError(f"Column definition file not found: {file_path}")

        with open(file_path) as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict) or "columns" not in data:
                raise ValueError(
                    f"Invalid format in {file_path}, expected a dictionary with 'columns'."
                )
            columns = data["columns"]
            if not isinstance(columns, dict):
                raise ValueError(
                    f"Invalid format in {file_path}, 'columns' must be a dictionary."
                )
            primary_key = data.get("primary_key", [])
            if not isinstance(primary_key, list):
                raise ValueError(
                    f"Invalid format in {file_path}, 'primary_key' must be a list."
                )
            duckdb_columns[target] = {
                "columns": columns,
                "primary_key": primary_key,
            }

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
        region=args.s3_region,
    )

    with duckdb.connect(args.db) as con:
        # 取得済みの最後のオブジェクト情報を保存するテーブルを作成
        create_s3_objects_table(con)

        # ICU (TIMESTAMPTZ 挙動と Grafana initSql に揃える目的) は s3_setup 内で LOAD する。
        s3_setup(con, args)
        for target in LOG_TARGETS:
            sync_log_for_init(con, client, args, target)


def initialize_log_table(con, client, args, target):
    """対象テーブルを初回作成する。

    list_objects は (last_modified, object_name) の降順で並ぶため、先頭側 initial_maximum_load
    件 (新しい側) のみを取り込み、カーソルは全体最新オブジェクトに進める。対象オブジェクトが
    無ければ何もしない。

    initial_maximum_load を超える古い側は「古すぎるデータを取り込まない」ため意図的に
    取り込まない。 init は最新側だけを取り込み、 update は取りこぼしを避けるため古い側から
    バッチ取り込みする。

    create_log_table (テーブル作成) と update_s3_objects_table (カーソル登録) は con.begin() /
    con.commit() で囲み、 途中失敗時は con.rollback() で「テーブル作成とカーソル登録」を
    まとめて取り消す (insert_log_from_s3 と同じ方針)。 これにより create 直後にプロセスが
    停止しても、 テーブルだけが存在して s3_objects にカーソル行が無い状態は残らない。

    「LOG_TARGETS テーブルは存在するが s3_objects のカーソル行が無い」 状態 (例: 手動
    DELETE FROM s3_objects、 s3_objects テーブル drop 後の init 再実行) で本関数を進めると、
    create_log_table がテーブル既存で早期 return する一方 update_s3_objects_table はカーソル
    を全体最新へ進めてしまい、 過去オブジェクトが取り込まれない状態になる。 これを防ぐ
    ため冒頭で該当状態を CliUsageError で拒否する。 運用者は DB ファイルと .wal ファイル
    を削除してから init を再実行して整合を取り直すこと。 init だけを再実行しても
    has_s3_objects_table が True で早期 return するため復旧しない。 また .wal を残すと、
    新規 DB に古い WAL が再生されるリスクがあるため、 DB ファイルと対で削除する
    (move_broken_db と同じ扱い)。 なおこの操作はローカルの LOG_TARGETS 全データを破棄して
    S3 から再取得し直すことになり、 initial_maximum_load 上限で古いオブジェクトは再取得されない
    点に注意。
    """
    if table_exists(con, target) and get_s3_objects_cursor(con, target) is None:
        raise CliUsageError(
            f"{target} table exists but s3_objects cursor is missing. "
            "Delete DB file and its .wal, then run 'init' again."
        )

    log_objects = list_objects(client, args.s3_bucket, f"{args.s3_prefix}/{target}/")
    if len(log_objects) == 0:
        print(f"No log found for {target} in {args.s3_bucket}.", file=sys.stderr)
        return

    log_urls = get_target_urls(args.s3_bucket, log_objects[: args.initial_maximum_load])
    con.begin()
    try:
        create_log_table(con, target, log_urls)
        # log_objects は降順なので、先頭が全体の最新オブジェクト
        update_s3_objects_table(con, target, log_objects[0])
        con.commit()
    except Exception:
        # rollback が disk full 等で失敗すると、stderr では rollback 起源の例外が目立ち、
        # 根本原因の追跡が難しくなる。rollback 例外は吸収し、事実だけを stderr に残して
        # 元例外を維持する (insert_log_from_s3 と同じ方針)。
        try:
            con.rollback()
        except Exception as rollback_error:
            print(f"Rollback also failed: {rollback_error}", file=sys.stderr)
        raise


def sync_log_for_init(con, client, args, target):
    try:
        initialize_log_table(con, client, args, target)
    except (duckdb.InvalidInputException, duckdb.IOException) as e:
        # 対象 target で読み込みエラー (壊れた gzip、read_json のスキーマ不一致等) が
        # 出ても残りの LOG_TARGETS を止めないため、stderr に記録して次の target へ進む。
        # 発生要因の例: 壊れた gzip (IOException)、read_json のスキーマ不一致
        # (InvalidInputException)。なお IOException は DB 書き込み側 (disk full、
        # 権限剥奪、WAL 書き込み失敗等) でも発生し得るが、メッセージでは区別せず
        # 同じ例外処理で捕捉する。コマンド全体の exit code は失敗を示さないため、
        # target ごとの stderr 出力を運用側で監視すること。
        print(f"{type(e).__name__} ({target}): {e}", file=sys.stderr)


def sync_log_for_update(con, client, args, target):
    cursor_key = get_s3_objects_cursor(con, target)
    try:
        if cursor_key is None:
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
                cursor_key,
            )
    except (duckdb.InvalidInputException, duckdb.IOException) as e:
        # 読み込みエラーは stderr に記録して次の target へ進む。IOException は
        # 読み込み側だけでなく DB 書き込み側でも発生し得るが、メッセージでは区別せず
        # 同じ例外処理で捕捉する。catch しないと単一の壊れたオブジェクトで update 全体が
        # 中断し、次サイクルもカーソル未進行のまま同じオブジェクトで止まり続ける。
        # 該当 target のカーソルは進まないため、壊れたオブジェクトが除去されるまで同じ
        # target で再発するが、他 target の更新は継続できる。
        print(f"{type(e).__name__} ({target}): {e}", file=sys.stderr)


def is_after_s3_cursor(obj_key, cursor_key):
    """
    s3_objects テーブルに保存したカーソルより新しいオブジェクトかを判定する。

    obj_key と cursor_key はどちらも (last_modified, object_name) の 2 タプル。 両方の
    last_modified がタイムゾーン情報を含んでいることを前提とする。 MinIO SDK の
    Object.last_modified と DuckDB の TIMESTAMPTZ カラムはどちらもタイムゾーン情報を
    含む datetime を返すため、 タイムゾーン情報を含まない datetime が渡るのは設計違反
    として明示的に拒否する。 判定はタプルの辞書順比較で行い、 last_modified が同値の
    場合は object_name の辞書順で決まる。
    """
    obj_last_modified, _ = obj_key
    cursor_last_modified, _ = cursor_key
    if obj_last_modified.tzinfo is None:
        raise ValueError("S3 object has a timezone-naive last_modified timestamp")
    if cursor_last_modified.tzinfo is None:
        raise ValueError("S3 cursor has a timezone-naive last_modified timestamp")
    return obj_key > cursor_key


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
    wal → 本体 の順で退避する。 途中で失敗しても本体が元位置に残るため、 次回起動時に
    is_db_broken が破損を再検出して同関数を呼び直せる。 本体は退避先に移ったのに wal
    だけ元位置に取り残される状態を構造的に作らずに済む。
    """
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S%f")
    broken_db_path = f"{db_path}.broken.{timestamp}"

    wal_path = f"{db_path}.wal"
    if os.path.exists(wal_path):
        shutil.move(wal_path, f"{broken_db_path}.wal")

    shutil.move(db_path, broken_db_path)

    return broken_db_path


def is_db_broken(db_path):
    """DB ファイルが破損していれば True、 正常または不在なら False を返す。

    破損以外の接続エラー (ロック競合、 権限不足等) は呼び出し元へ伝播させる。
    read_only=True で開くことで、 WAL 再生による意図せぬ状態変化 (破損を「復旧」
    したように見せる副作用) を避け、 破損状態をそのまま検出できるようにする。 ただし
    read_only 接続は WAL 再生を行わないため、 「DB 本体は正常だが WAL が破損」 の
    ケースは本関数では検出しない。 WAL 破損は update / delete が実 R/W オープンして
    WAL 再生を試みた段階で IOException 等として顕在化する。
    """
    if not os.path.exists(db_path):
        return False
    try:
        with duckdb.connect(db_path, read_only=True) as con:
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
    if not is_db_broken(db_path):
        return
    broken_db_path = move_broken_db(db_path)
    print(
        f"Detected broken DB file. moved to {broken_db_path}",
        file=sys.stderr,
    )


def has_s3_objects_table(db_path):
    """DB ファイルに s3_objects テーブルが存在するかを判定する。 DB ファイルが存在しない
    場合は False を返す。

    init は完了判定に使い、 update は事前チェックに使う。 s3_objects テーブルの「存在」
    のみを見て、 行数や LOG_TARGETS テーブルの有無は見ない。 s3_objects の作成は init 内
    で LOG_TARGETS 取り込みより先に呼ばれるため、 テーブル存在 = init が create_s3_objects_table
    まで到達した という判定に十分。
    """

    if not os.path.exists(db_path):
        return False

    # read_only=True で開くことで、s3_objects テーブル存在確認だけの目的で mtime や
    # WAL を進めないようにする。これで init が何もせず終了する経路
    # (has_s3_objects_table True で早期 return) が readonly コピー再生成を毎回誘発することを防ぐ。
    with duckdb.connect(db_path, read_only=True) as con:
        if not table_exists(con, "s3_objects"):
            return False

    return True


def raise_if_db_broken(db_path):
    """update / delete の前処理として DB 破損を検出し、 CliUsageError で init の再実行を促す。

    init は prepare_db_for_init で自動退避するが、 update / delete では運用者の判断を
    優先するため自動退避しない。 例外は handle_cli_error で ユーザー向け 1 行メッセージ
    + exit 1 に整形される (他の前処理 FileNotFoundError / CliUsageError と経路を揃える)。
    破損以外 (ロック競合、 権限不足等) は呼び出し元へ伝播。
    """
    if not is_db_broken(db_path):
        return
    raise CliUsageError(
        f"DB file is broken: {db_path}. Move or remove the file and run 'init' to re-initialize."
    )


UPSERT_S3_OBJECTS_SQL = """
MERGE INTO s3_objects AS target
USING (SELECT ? AS type, ? AS object_name, ? AS last_modified) AS source
ON target.type = source.type
WHEN MATCHED THEN
    UPDATE SET object_name = source.object_name, last_modified = source.last_modified
WHEN NOT MATCHED THEN
    INSERT (type, object_name, last_modified) VALUES (source.type, source.object_name, source.last_modified);
"""


def update_s3_objects_table(con, log_type, obj):
    con.execute(UPSERT_S3_OBJECTS_SQL, (log_type, obj.object_name, obj.last_modified))


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


def ensure_safe_sql_string_literal(path_literal):
    """DuckDB の ATTACH 等で使う SQL 文字列リテラルとして path_literal を安全に埋め込むため、
    制御文字を含む値を CliUsageError で拒否し、 シングルクォートをエスケープする。

    DuckDB はファイルパスをプリペアドステートメントでバインドできないため、 ATTACH 直前に
    呼んでパス文字列を直接埋め込む用途。 低位制御文字 (0x00 から 0x1F および 0x7F) を含む値は
    DuckDB パーサで予期せぬ挙動を起こす可能性があるため CliUsageError で拒否する。 C1 制御
    (0x80-0x9F) や Unicode 行区切り (U+2028 / U+2029) は DuckDB での実害が観測されていない
    ため対象外とする (追加が必要になれば実例を根拠に拡張する)。
    ATTACH を通らない `duckdb.connect(args.db)` 等の経路は本関数の対象外で、 そこで NUL
    バイト等が混入したときは Python 側の `embedded null byte` ValueError 等に委ねる
    (args.db は argparse 経由の CLI 引数で外部入力ではないため、 入口での網羅検証は持たない)。
    """
    for i, c in enumerate(path_literal):
        if ord(c) < 0x20 or ord(c) == 0x7F:
            # path_literal[:40]!r はログが肥大化しないように先頭の 40 文字に絞る。また
            # repr で制御文字を `\xNN` 形式に視覚化し、ログ表示や検索への影響を避ける。
            raise CliUsageError(
                "SQL string literal must not contain control characters: "
                f"U+{ord(c):04X} at index {i} in {path_literal[:40]!r}"
            )
    return path_literal.replace("'", "''")


def remove_delete_incomplete_copy_files(copy_file):
    """
    削除処理が失敗した時に残る可能性があるコピー先の .copy ファイルと .copy.wal ファイルを削除する
    """
    for path in (copy_file, f"{copy_file}.wal"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def s3_setup(con, args):
    # INSTALL / LOAD icu は init / update の両経路で TIMESTAMPTZ を含むスキーマの read_json
    # をタイムゾーン依存の挙動差から守るための予防的ロードで、Grafana 側 datasource の
    # initSql (INSTALL icu; LOAD icu) と挙動を揃える目的も持つ。INSTALL は idempotent
    # なので複数経路から呼んでも副作用は無い。delete は S3 に触らない別経路 (in-memory
    # DuckDB + ATTACH) のため本関数を通らず、ICU も要求しないので LOAD しない。
    con.execute("INSTALL icu")
    con.execute("LOAD icu")
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
    primary_key が定義されているテーブルは PK 付きで作成し、 insert は ON CONFLICT DO NOTHING
    で重複行を吸収する (rel.create / rel.insert_into は PK と ON CONFLICT を扱えないため、
    生 SQL で組み立てる)。 既にテーブルが存在する場合は何もしない。 LOG_TARGETS 外の
    テーブル名は ValueError で弾く。
    """
    duckdb_columns = load_columns()
    require_known_table(table_name, duckdb_columns)

    if table_exists(con, table_name):
        print(f"Table {table_name} already exists.", file=sys.stderr)
        return

    # テーブルを作成する
    columns = duckdb_columns[table_name]["columns"]
    primary_key = duckdb_columns[table_name]["primary_key"]
    column_defs = ", ".join(f"{name} {col_type}" for name, col_type in columns.items())
    if primary_key:
        column_defs += f", PRIMARY KEY ({', '.join(primary_key)})"
    con.execute(f"CREATE TABLE {table_name} ({column_defs})")
    insert_log(con, table_name, target_urls)
    print(
        f"Created table {table_name} from {len(target_urls)} object(s).",
        file=sys.stderr,
    )


def update(args):
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}. Run 'init' first.")

    raise_if_db_broken(args.db)

    # s3_objects テーブル不在の DB に対しては update を拒否する。init が未実行のまま
    # update を呼ぶと get_s3_objects_cursor が CatalogException で落ちるため、明示的に弾く。
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
        region=args.s3_region,
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

    .wal の残骸は本関数内の duckdb.connect(args.db) (R/W オープン) で DuckDB が自動的に
    再生・チェックポイントするため、 明示的な削除は加えない。 raise_if_db_broken は
    read_only 接続なので WAL 再生には関与しない。

    0o660 への chmod は COPY 経路の副次効果なので、 削除 0 件のときは正規化されない
    (元 DB のパーミッションは init / sync の umask で揃える前提)。

    con.begin() を明示的に呼ばないため DuckDB は auto-commit モードで動き、
    delete_log_by_timestamp が発行する DELETE は SQL 実行のたびに即 commit される
    (init の initialize_log_table や update の insert_log_from_s3 が LOG_TARGETS
    の target ごとに con.begin() / con.commit() で囲むのと非対称)。 このため、
    途中の target で例外が起きると直前までの target の DELETE は反映済みで、
    残りは未反映のまま delete が中断される。 全 target の DELETE を通過した後の
    COPY 段階で失敗した場合は全 DELETE が反映済みで、 元 DB は未圧縮のまま残る。
    いずれもリトライ時は同じ retention_period で境界を計算し直すため、 反映済み分
    は再実行しても同じ結果に収束する前提。
    """
    if not os.path.exists(args.db):
        raise FileNotFoundError(f"DB file not found: {args.db}. Run 'init' first.")

    raise_if_db_broken(args.db)

    copy_file = f"{args.db}.copy"

    # 前回 delete が SIGKILL や OOM 等で異常終了して残った .copy と .copy.wal を掃除してから
    # 始める。残っていると後段の ATTACH '{copy_file}' AS copy が既存ファイルを開いてしまい、
    # COPY FROM DATABASE で古いスキーマと新本体データが混ざる可能性があるため。
    # ここでの掃除失敗は delete 全体の失敗として扱う (残骸を消せない状態で ATTACH に進むと
    # 古いスキーマ混入の危険があるため)。関数側は FileNotFoundError のみ吸収し、
    # PermissionError 等は握らずに呼び出し元へ伝播することで、掃除失敗を隠さず delete 全体
    # を失敗させる。同一delete 内で失敗した場合の掃除は except 内で別途行う (そちらは
    # 元の例外を守るため掃除の例外を握りつぶす経路)。
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
            # DB サイズ削減のため、COPY FROM DATABASE で DB を詰め直す
            con.execute(f"ATTACH '{ensure_safe_sql_string_literal(args.db)}' AS db")
            con.execute(f"ATTACH '{ensure_safe_sql_string_literal(copy_file)}' AS copy")
            con.execute("COPY FROM DATABASE db TO copy")

        # コピーしたファイルを、元の DB ファイルに上書きする
        # other の読み込み権限、書き込み権限は不要なので 0o660 に揃える
        os.chmod(
            copy_file,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP,
        )
        shutil.move(copy_file, args.db)
    except Exception:
        # 削除する前にデバッグ情報 (存在有無 + サイズ) を stderr に残す。ディスクフルや
        # パーミッションエラー等の原因究明の手がかりを消さないため。stat 失敗で元例外を
        # 上書きしないよう、診断出力の失敗は無視する。存在確認とサイズ取得を分けると、
        # その間にファイル状態が変わって FileNotFoundError が発生する可能性がある。
        for path in (copy_file, f"{copy_file}.wal"):
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            print(
                f"Cleaning up incomplete {path} (size={size} bytes)",
                file=sys.stderr,
            )
        # ATTACH / COPY / chmod / move のいずれかが失敗した後、残った copy_file と
        # copy_file.wal を削除する。削除中に PermissionError 等が出ても、元例外
        # (ATTACH 失敗、COPY 失敗、ディスクフル 等) を上書きしないよう、掃除の失敗は
        # stderr に記録するだけに留める。
        try:
            remove_delete_incomplete_copy_files(copy_file)
        except OSError as cleanup_error:
            print(
                f"Failed to cleanup incomplete copy files: {cleanup_error}",
                file=sys.stderr,
            )
        # delete の失敗として呼び出し元へ伝えるため例外を再送出する
        raise


def insert_log_from_s3(
    con, client, table_name, bucket, prefix, update_maximum_load, cursor_key
):
    """s3_objects カーソルより新しい S3 オブジェクトを古い順にバッチで取り込む。

    update 経路の中心関数。 呼び出し側 (sync_log_for_update) は cursor_key is not None
    を保証する前提 (cursor_key は (last_modified, object_name) の 2 タプル)。 list_objects
    (降順) と is_after_s3_cursor でカーソル以降の対象オブジェクトを絞り込み、 末尾側
    update_maximum_load 件 (古い側) を 1 回の update で取り込む。 カーソルはバッチ内最新
    までしか進めないため、 update_maximum_load を超えた新しい側は次回以降の update で
    is_after_s3_cursor が True 判定して順次取得する。

    カーソルと同値の last_modified を持つオブジェクト (カーソル行自身を除く) は、
    バッチ分割の対象外として毎回の update で全件取り込む (カーソル通過後に同値の
    last_modified で現れたオブジェクトを拾うため。 重複行は PK 制約 + ON CONFLICT
    DO NOTHING で吸収する)。

    insert_log とカーソル更新は con.begin() / con.commit() で囲み、 途中失敗時は
    con.rollback() で「行 insert とカーソル更新」 を atomic に保つ (中途半端な状態で
    残さない)。
    """
    log_objects = list_objects(client, bucket, f"{prefix}/{table_name}/")

    cursor_last_modified, cursor_object_name = cursor_key
    target_log_objects = [
        obj
        for obj in log_objects
        if is_after_s3_cursor((obj.last_modified, obj.object_name), cursor_key)
    ]
    same_last_modified_objects = [
        obj
        for obj in log_objects
        if obj.last_modified == cursor_last_modified
        and obj.object_name != cursor_object_name
    ]

    # 長時間停止後に大量ファイルが蓄積したケースに備え、古い方からバッチで取り込む。
    # 降順ソートされているため、末尾側 update_maximum_load 件が古い順のバッチになる。
    # 同値グループはバッチ分割の対象外とする (カーソルと同値のオブジェクトは毎回全件
    # 取り込み、 重複は PK で吸収する)。
    if len(target_log_objects) > update_maximum_load:
        target_log_objects = target_log_objects[-update_maximum_load:]

    if len(target_log_objects) == 0 and len(same_last_modified_objects) == 0:
        return

    target_urls = get_target_urls(bucket, target_log_objects)
    same_urls = get_target_urls(bucket, same_last_modified_objects)
    con.begin()
    try:
        if target_urls:
            insert_log(con, table_name, target_urls)
        if same_urls:
            insert_log(con, table_name, same_urls)
        if target_log_objects:
            # 先頭がこのバッチの最新
            update_s3_objects_table(con, table_name, target_log_objects[0])
        con.commit()
    except Exception:
        # rollback が disk full 等で失敗すると元例外が __context__ に沈み、stderr には
        # rollback 起源の例外だけが出て根本原因の追跡が難しくなる。rollback 例外は吸収し、
        # 事実だけを stderr に残して元例外を維持する。
        try:
            con.rollback()
        except Exception as rollback_error:
            print(f"Rollback also failed: {rollback_error}", file=sys.stderr)
        raise


def insert_log(con, table_name, target_urls):
    """指定 target_urls の JSON を LOG_TARGETS テーブルに追加する。

    create_log_table と同じ許可リスト検査 (require_known_table) を経由して、
    SQL への直接埋め込みを行わない経路でも防御チェックを共有する。
    PK を持つテーブルでは ON CONFLICT DO NOTHING で重複行を吸収する
    (rel.insert_into は ON CONFLICT を扱えないため、 生 SQL で組み立てる)。
    """
    duckdb_columns = load_columns()
    require_known_table(table_name, duckdb_columns)

    columns = duckdb_columns[table_name]["columns"]
    column_names = ", ".join(columns.keys())
    if duckdb_columns[table_name]["primary_key"]:
        conflict_clause = " ON CONFLICT DO NOTHING"
    else:
        conflict_clause = ""
    con.execute(
        f"INSERT INTO {table_name} ({column_names}) "
        f"SELECT {column_names} FROM read_json(?, union_by_name=true, columns=?)"
        f"{conflict_clause}",
        [target_urls, columns],
    )


def get_s3_objects_cursor(con, log_type):
    return con.execute(
        "SELECT last_modified, object_name FROM s3_objects WHERE type=?",
        (log_type,),
    ).fetchone()


def delete_log_by_timestamp(con, table_name, timestamp):
    # table_name は SQL に直接埋め込むため、許可リストで縛る
    require_known_table(table_name, LOG_TARGETS)

    if not table_exists(con, table_name):
        # テーブルが存在しない場合はスキップする
        # delete は全 target を処理するため、存在しないテーブルはエラーにせずスキップする
        print(f"Table {table_name} does not exist.", file=sys.stderr)
        return 0

    con.execute(f"DELETE FROM {table_name} WHERE timestamp < ?", (timestamp,))
    deleted_rows = con.fetchone()[0]
    print(f"Deleted {deleted_rows} rows from {table_name}.", file=sys.stderr)
    return deleted_rows


def exit_with_stderr(message):
    """エラーメッセージを stderr に書き出して exit code 1 で終了する。"""
    print(message, file=sys.stderr)
    sys.exit(1)


def capture_db_stat(db_path):
    """DB ファイルが存在すれば (mtime_ns, size) タプルを、 無ければ None を返す。

    main が args.func 実行前後で readonly コピー生成要否を判定するための初期値取得と、
    should_create_readonly 内の現在値取得を共通化するためのヘルパー。 秒粒度に丸められる
    FS でも「同一秒 + 同一サイズ」 の同値ですり抜けるケースを排除するため、 mtime_ns
    (ナノ秒精度整数) と size のペアを返す。

    os.path.exists で先に弾かず try/except FileNotFoundError で受けているのは、 exists
    と stat の 2 コール間で削除された場合に FileNotFoundError が乗る TOCTOU を避けるため。
    捕捉するのは FileNotFoundError のみで、 PermissionError (SELinux / 親ディレクトリ
    権限不足 等) は握らず伝播する。
    """
    try:
        stat_result = os.stat(db_path)
    except FileNotFoundError:
        return None
    return (stat_result.st_mtime_ns, stat_result.st_size)


def should_create_readonly(db_path, initial_stat):
    """initial_stat (mtime_ns, size) と現在の値を比較して、 readonly コピーを生成すべきかを返す。

    capture_db_stat が返すタプルを initial_stat と比較し、 どちらか異なれば .readonly を
    生成する (タプル構造の根拠は capture_db_stat の docstring 参照)。 args.db が args.func
    実行後に消失したケース (initial_stat が None または現在ファイル不在) は readonly も
    更新しない方針とし、 現在ファイルが無ければ False を返す。

    update / delete では実データ変更が無くても DuckDB の R/W オープン副作用で mtime が
    進むため、 readonly が毎回再生成される (コストは shutil.copyfile 1 回分で許容する
    方針)。 no-op init だけは has_s3_objects_table を read_only=True にして再生成を
    防いでいる。
    """
    current_stat = capture_db_stat(db_path)
    if current_stat is None:
        return False
    return initial_stat != current_stat


def create_readonly_copy(db_path):
    """書き込み済みの DB ファイルから読み込み専用コピーを生成する。

    DuckDB は書き込み中に他プロセスからアクセスできないため、書き込み終了後に同 FS 内で
    一時ファイルを作成し、rename で .readonly に切り替えることで atomic な差し替えにする。
    Grafana は .readonly のみを参照する想定。
    参考: https://github.com/motherduckdb/grafana-duckdb-datasource?tab=readme-ov-file#updating-data-in-the-duckdb-file
    """
    tmp_file = f"{db_path}.tmp"
    # 前回の create_readonly_copy が copyfile と move の間で異常終了して .tmp が残っても、
    # 直後の copyfile が上書きするため事前削除は不要。delete の .copy は ATTACH で開かれ
    # 古いスキーマが混ざる危険があるため掃除するが、.tmp にはその経路がなく非対称でよい。
    shutil.copyfile(db_path, tmp_file)
    # other の読み込み権限、書き込み権限は不要なので 0o660 に揃える
    os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    readonly_file = f"{db_path}.readonly"
    shutil.move(tmp_file, readonly_file)


def handle_cli_error(error, bucket):
    """main から呼び出された関数の例外を分類して整形する CLI トップレベル例外ハンドラ。

    S3Error / FileNotFoundError (DB 不在) / CliUsageError をユーザー向け 1 行メッセージで
    exit 1 にし、 それ以外はトレースバック付きで上位に伝播させる (詳細な分類は
    CliUsageError の対応表を参照)。 bucket は NoSuchBucket メッセージ用の表示値として
    受け取る。
    """
    if isinstance(error, S3Error):
        if error.code == "NoSuchBucket":
            exit_with_stderr(f"S3 bucket not found: {bucket}")
        else:
            exit_with_stderr(f"S3 error occurred (code={error.code}): {error.message}")
    elif isinstance(error, (FileNotFoundError, CliUsageError)):
        exit_with_stderr(str(error))
    else:
        # ハンドル対象外の例外 (ValueError 等の内部バグ) は意図的にトレースバック付きで
        # 上位に伝播させる。tb には handle_cli_error のフレームが 1 段乗るが、原因究明時は
        # 元例外の chain を辿る前提で受け入れる。
        raise error


def main():
    # --help にデフォルト値を自動表示するため、ArgumentDefaultsHelpFormatter を使う。
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

    initial_stat = capture_db_stat(args.db)

    try:
        args.func(args)
    except Exception as error:
        handle_cli_error(error, args.s3_bucket)

    # args.func が例外を投げると handle_cli_error 経由で sys.exit するか、未処理例外として
    # 上位へ再送出されるため、以下は成功時のみ実行される。結果として .readonly は前回
    # 成功時点のまま保持される。
    # readonly コピーは Grafana が参照する派生物で、args.func 本体の DB 更新が終わってから
    # 生成する。shutil.copyfile / os.chmod / shutil.move で PermissionError (SELinux / 親
    # ディレクトリ権限不足 等) や OSError (disk full 等) が上がっても、args.func は既に成功
    # して DB 本体への変更は反映済み、かつ .readonly は前回成功時点のファイルがそのまま残る
    # ため Grafana の参照経路は壊れない。したがって readonly 生成失敗は 1 回分の反映遅延に
    # 留まる副作用と捉え、process としては exit code 0 を維持して stderr に 1 行残すだけに
    # する (次回の update / delete で改めて更新される)。args.func 失敗と同じ経路で
    # handle_cli_error に乗せて exit 1 にすると「DB 更新は成功したのに exit 1」 という
    # 誤解を招くため、意図的に別経路にしている。
    try:
        if should_create_readonly(args.db, initial_stat):
            create_readonly_copy(args.db)
    except Exception as error:
        print(
            f"Failed to update .readonly copy: {type(error).__name__}: {error}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
