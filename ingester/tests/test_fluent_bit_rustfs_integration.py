import json
import shutil
import subprocess
from pathlib import Path

import duckdb
import minio
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from .fluent_bit_helper import create_fluent_bit_config
from .helpers import WaitTimeoutError, wait_until

ACCESS_KEY = "kohakuadmin"
SECRET_KEY = "kohakuadmin"
BUCKET = "kohaku"
PREFIX = "log"
RUSTFS_PORT = 9000
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-alpha.89"
FLUENT_BIT_IMAGE = "fluent/fluent-bit"


def count_objects(client, prefix):
    """
    指定プレフィックス配下のオブジェクト件数を取得する。
    :param client: MinIO 互換クライアント
    :param prefix: 件数集計対象のプレフィックス
    :return: オブジェクト件数を表す整数
    """
    return len(list(client.list_objects(BUCKET, prefix=prefix, recursive=True)))


def decode_logs(logs):
    """
    ログ出力を文字列へ正規化する。
    :param logs: bytes または文字列化可能なログデータ
    :return: UTF-8 で復号した、または文字列化したログ文字列
    """
    if isinstance(logs, bytes):
        return logs.decode("utf-8", errors="replace")
    return str(logs)


def create_test_log_dir(tmp_path, source_log_dir, include_session_webhook=True):
    """
    テスト用ログディレクトリを作成し、入力ログファイルを配置する。
    :param tmp_path: pytest が提供する一時ディレクトリ
    :param source_log_dir: 元となるログファイルを保持するディレクトリ
    :param include_session_webhook: session_webhook の入力ファイルを生成するかどうか
    :return: 生成したログディレクトリの Path オブジェクト
    """
    # fluent-bit 入力用のログディレクトリをテスト毎に作成する
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    shutil.copyfile(source_log_dir / "rtc_stats.jsonl", log_dir / "rtc_stats.jsonl")

    # 対象ログ欠損ケースを作るため、session_webhook は必要なときだけ生成する
    if include_session_webhook:
        session_webhook_source_path = source_log_dir / "session_webhook.jsonl"
        session_webhook_path = log_dir / "session_webhook.jsonl"
        session_webhook_path.write_text(
            session_webhook_source_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return log_dir


def run_fluent_bit_and_wait(network, log_dir, config_path, state_dir, client, expected_prefix_counts):
    """
    fluent-bit コンテナを起動し、期待件数に到達するまで待機する。
    :param network: テスト用 Docker ネットワーク
    :param log_dir: fluent-bit 入力ログのマウント元ディレクトリ
    :param config_path: fluent-bit 設定ファイルの Path
    :param state_dir: fluent-bit 状態ファイルのマウント元ディレクトリ
    :param client: オブジェクトストレージクライアント
    :param expected_prefix_counts: プレフィックスごとの期待最小件数を持つ辞書
    :return: なし。待機がタイムアウトした場合は pytest.fail を呼び出す
    """
    # fluent-bit を起動し、期待オブジェクト数に達するまで待機する
    with (
        DockerContainer(FLUENT_BIT_IMAGE)
        .with_env("AWS_ACCESS_KEY_ID", ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", SECRET_KEY)
        .with_volume_mapping(str(log_dir), "/log", mode="rw")
        .with_volume_mapping(str(config_path), "/fluent-bit/etc/fluent-bit.yml", mode="ro")
        .with_volume_mapping(str(state_dir), "/state", mode="rw")
        .with_network(network)
        .with_command("/fluent-bit/bin/fluent-bit -c /fluent-bit/etc/fluent-bit.yml") as fluent_bit
    ):
        try:
            for prefix, expected_count in expected_prefix_counts.items():
                wait_until(lambda p=prefix, n=expected_count: count_objects(client, p) >= n)
        except WaitTimeoutError as error:
            fluent_bit_logs = decode_logs(fluent_bit.get_logs())
            pytest.fail(
                f"{error}\n\n"
                f"fluent-bit logs:\n{fluent_bit_logs}"
            )


def run_ingester_cli(ingester_dir, db_path, endpoint, command, initial_maximum_load=1000):
    """
    ingester の run.py を CLI として実行する。
    :param ingester_dir: run.py 実行時の作業ディレクトリ
    :param db_path: DuckDB ファイルパス
    :param endpoint: 接続先 S3 エンドポイント
    :param command: 実行するサブコマンド
    :param initial_maximum_load: init 時の初期読み込み上限件数
    :return: subprocess.run が返す CompletedProcess オブジェクト
    """
    # run.py を CLI 経由で実行し、stdout/stderr を呼び出し元で検証できるようにする
    cmd = [
        "uv",
        "run",
        "python",
        "src/run.py",
        "--db",
        str(db_path),
        "--s3_endpoint",
        endpoint,
        "--s3_access_key_id",
        ACCESS_KEY,
        "--s3_secret_access_key",
        SECRET_KEY,
        "--s3_bucket",
        BUCKET,
        "--s3_prefix",
        PREFIX,
        "--initial_maximum_load",
        str(initial_maximum_load),
        command,
    ]
    return subprocess.run(
        cmd,
        cwd=ingester_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def append_rtc_stats_log(log_dir):
    """
    rtc_stats ログに 1 行追加し、新規オブジェクト送信の契機を作る。
    :param log_dir: rtc_stats.jsonl を含むログディレクトリ
    :return: なし
    """
    # 既存ログ 1 行を複製して識別子だけ変え、新規オブジェクト送信を発生させる
    rtc_stats_path = log_dir / "rtc_stats.jsonl"
    first_line = rtc_stats_path.read_text(encoding="utf-8").splitlines()[0]
    data = json.loads(first_line)
    data["id"] = "NKSZER34ZN5J77HTVNQP1FWJ1X"
    data["rtc_id"] = "AP-NEW"
    data["timestamp"] = "2025-07-25T06:08:51.592776Z"
    data["rtc_timestamp"] = 1753423731556.0

    with rtc_stats_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")


def get_s3_cursor(con, log_type):
    """
    指定ログ種別の S3 カーソル情報を取得する。
    :param con: DuckDB 接続オブジェクト
    :param log_type: s3_objects テーブルの type 列に対応するログ種別
    :return: object_name と last_modified のタプル。未登録時は None
    """
    return con.execute(
        "SELECT object_name, last_modified FROM s3_objects WHERE type=?",
        (log_type,),
    ).fetchone()


def test_runpy_init_with_fluent_bit_and_rustfs(tmp_path):
    """fluent-bit 経由で RustFS に保存したログを init で取り込めることを確認する。"""
    repo_root = Path(__file__).resolve().parents[2]
    ingester_dir = repo_root / "ingester"
    source_log_dir = ingester_dir / "tests" / "log"
    log_dir = create_test_log_dir(tmp_path, source_log_dir)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config_path = tmp_path / "fluent-bit.yml"
    create_fluent_bit_config(config_path)
    duckdb_path = tmp_path / "duck.db"

    # RustFS と fluent-bit を同一 Docker network 上で接続する
    with Network() as network:
        with (
            DockerContainer(RUSTFS_IMAGE)
            .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
            .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
            .with_network(network)
            .with_network_aliases("rustfs")
            .with_exposed_ports(RUSTFS_PORT) as rustfs
        ):
            endpoint = f"{rustfs.get_container_host_ip()}:{rustfs.get_exposed_port(RUSTFS_PORT)}"
            client = minio.Minio(
                endpoint,
                access_key=ACCESS_KEY,
                secret_key=SECRET_KEY,
                secure=False,
            )

            wait_until(lambda: client.list_buckets() is not None)
            client.make_bucket(BUCKET)

            run_fluent_bit_and_wait(
                network,
                log_dir,
                config_path,
                state_dir,
                client,
                {
                    f"{PREFIX}/rtc_stats/": 1,
                    f"{PREFIX}/session_webhook/": 1,
                },
            )

            run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "init")
            assert run.returncode == 0, run.stderr
            assert duckdb_path.exists()

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                session_webhook_count = con.execute("SELECT COUNT(*) FROM session_webhook").fetchone()[0]
                s3_objects_count = con.execute("SELECT COUNT(*) FROM s3_objects").fetchone()[0]

            assert rtc_stats_count > 0
            assert session_webhook_count > 0
            # LOG_TARGETS が 2 種類のため、カーソルテーブルも 2 行になる
            assert s3_objects_count == 2


def test_runpy_init_skips_missing_target_without_invalid_input_exception(tmp_path):
    """session_webhook が存在しない場合でも init が成功し、InvalidInputException を出力しないことを確認する。"""
    repo_root = Path(__file__).resolve().parents[2]
    ingester_dir = repo_root / "ingester"
    source_log_dir = ingester_dir / "tests" / "log"
    # rtc_stats のみを投入し、session_webhook は意図的に欠損させる
    log_dir = create_test_log_dir(tmp_path, source_log_dir, include_session_webhook=False)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config_path = tmp_path / "fluent-bit.yml"
    create_fluent_bit_config(config_path)
    duckdb_path = tmp_path / "duck.db"

    with Network() as network:
        with (
            DockerContainer(RUSTFS_IMAGE)
            .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
            .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
            .with_network(network)
            .with_network_aliases("rustfs")
            .with_exposed_ports(RUSTFS_PORT) as rustfs
        ):
            endpoint = f"{rustfs.get_container_host_ip()}:{rustfs.get_exposed_port(RUSTFS_PORT)}"
            client = minio.Minio(
                endpoint,
                access_key=ACCESS_KEY,
                secret_key=SECRET_KEY,
                secure=False,
            )

            wait_until(lambda: client.list_buckets() is not None)
            client.make_bucket(BUCKET)

            run_fluent_bit_and_wait(
                network,
                log_dir,
                config_path,
                state_dir,
                client,
                {
                    # rtc_stats だけが RustFS に保存されることを待機条件にする
                    f"{PREFIX}/rtc_stats/": 1,
                },
            )

            run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "init")
            # 欠損ターゲットがあっても init 全体は成功すること
            assert run.returncode == 0, run.stderr
            # 旧挙動で出ていた InvalidInputException が消えていること
            assert "InvalidInputException" not in run.stdout
            assert "InvalidInputException" not in run.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                session_webhook_table_count = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'"
                ).fetchone()[0]
                s3_objects_count = con.execute("SELECT COUNT(*) FROM s3_objects").fetchone()[0]

            # rtc_stats は通常どおり取り込まれること
            assert rtc_stats_count > 0
            # 欠損している session_webhook テーブルは作成されないこと
            assert session_webhook_table_count == 0
            # rtc_stats のみ取り込まれるため、カーソルテーブルも 1 行になる
            assert s3_objects_count == 1

            # 未作成ターゲット (session_webhook) が欠損していても update 全体が成功すること
            update_run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "update")
            assert update_run.returncode == 0, update_run.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count_after_update = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                session_webhook_table_count_after_update = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'"
                ).fetchone()[0]
                s3_objects_count_after_update = con.execute("SELECT COUNT(*) FROM s3_objects").fetchone()[0]

            assert rtc_stats_count_after_update == rtc_stats_count
            assert session_webhook_table_count_after_update == 0
            assert s3_objects_count_after_update == 1


def test_runpy_update_only_imports_new_objects_and_updates_cursor(tmp_path):
    """update が差分のみを取り込み、カーソル更新後の再実行で重複しないことを確認する。"""
    repo_root = Path(__file__).resolve().parents[2]
    ingester_dir = repo_root / "ingester"
    source_log_dir = ingester_dir / "tests" / "log"
    log_dir = create_test_log_dir(tmp_path, source_log_dir)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    config_path = tmp_path / "fluent-bit.yml"
    create_fluent_bit_config(config_path)
    duckdb_path = tmp_path / "duck.db"

    with Network() as network:
        with (
            DockerContainer(RUSTFS_IMAGE)
            .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
            .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
            .with_network(network)
            .with_network_aliases("rustfs")
            .with_exposed_ports(RUSTFS_PORT) as rustfs
        ):
            endpoint = f"{rustfs.get_container_host_ip()}:{rustfs.get_exposed_port(RUSTFS_PORT)}"
            client = minio.Minio(
                endpoint,
                access_key=ACCESS_KEY,
                secret_key=SECRET_KEY,
                secure=False,
            )
            wait_until(lambda: client.list_buckets() is not None)
            client.make_bucket(BUCKET)

            run_fluent_bit_and_wait(
                network,
                log_dir,
                config_path,
                state_dir,
                client,
                {
                    f"{PREFIX}/rtc_stats/": 1,
                    f"{PREFIX}/session_webhook/": 1,
                },
            )

            init_run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "init")
            assert init_run.returncode == 0, init_run.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                before_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                before_cursor = get_s3_cursor(con, "rtc_stats")

            append_rtc_stats_log(log_dir)
            rtc_stats_object_count_before = count_objects(client, f"{PREFIX}/rtc_stats/")
            run_fluent_bit_and_wait(
                network,
                log_dir,
                config_path,
                state_dir,
                client,
                {f"{PREFIX}/rtc_stats/": rtc_stats_object_count_before + 1},
            )

            update_run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "update")
            assert update_run.returncode == 0, update_run.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                after_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                after_cursor = get_s3_cursor(con, "rtc_stats")

            assert after_count > before_count
            assert after_cursor is not None
            assert before_cursor is not None
            # 新規オブジェクト取り込み後は cursor が進む
            assert after_cursor != before_cursor

            # 新規ログなしの update では重複取り込みしないことを確認する
            update_run_again = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "update")
            assert update_run_again.returncode == 0, update_run_again.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                final_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                final_cursor = get_s3_cursor(con, "rtc_stats")
            assert final_count == after_count
            # 新規ログがない update では cursor も進まない
            assert final_cursor == after_cursor


def test_runpy_init_fails_when_bucket_not_found(tmp_path):
    """S3 バケット未作成時に init が失敗し、エラーメッセージを返すことを確認する。"""
    repo_root = Path(__file__).resolve().parents[2]
    ingester_dir = repo_root / "ingester"
    duckdb_path = tmp_path / "duck.db"

    with Network() as network:
        with (
            DockerContainer(RUSTFS_IMAGE)
            .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
            .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
            .with_network(network)
            .with_network_aliases("rustfs")
            .with_exposed_ports(RUSTFS_PORT) as rustfs
        ):
            endpoint = f"{rustfs.get_container_host_ip()}:{rustfs.get_exposed_port(RUSTFS_PORT)}"

            wait_until(
                lambda: minio.Minio(
                    endpoint,
                    access_key=ACCESS_KEY,
                    secret_key=SECRET_KEY,
                    secure=False,
                ).list_buckets()
                is not None
            )

            run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "init")
            assert run.returncode != 0
            assert "S3 bucket not found" in run.stderr
