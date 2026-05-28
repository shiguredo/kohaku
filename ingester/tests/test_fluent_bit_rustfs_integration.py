import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import minio
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from .conftest import ACCESS_KEY, BUCKET, PREFIX, RUSTFS_IMAGE, RUSTFS_PORT, SECRET_KEY
from .fluent_bit_helper import create_fluent_bit_config
from .helpers import WaitTimeoutError, wait_until

# 使用する fluent-bit の Docker イメージ (タグ未指定で latest 相当)
FLUENT_BIT_IMAGE = "fluent/fluent-bit"


def fetch_scalar(con: duckdb.DuckDBPyConnection, query: str) -> Any:
    """SELECT で 1 行 1 列を返すクエリの最初のカラム値を取得する。"""
    row = con.execute(query).fetchone()
    assert row is not None
    return row[0]


def count_objects(client: minio.Minio, prefix: str) -> int:
    """指定プレフィックス配下のオブジェクト件数を取得する。"""
    return len(list(client.list_objects(BUCKET, prefix=prefix, recursive=True)))


def decode_logs(logs: Any) -> str:
    """ログ出力を文字列へ正規化する。bytes は UTF-8 で復号する。"""
    if isinstance(logs, bytes):
        return logs.decode("utf-8", errors="replace")
    return str(logs)


def create_test_log_dir(
    tmp_path: Path, source_log_dir: Path, include_session_webhook: bool = True
) -> Path:
    """テスト用ログディレクトリを作成し、入力ログファイルを配置する。"""
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


def run_fluent_bit_and_wait(
    network: Network,
    log_dir: Path,
    config_path: Path,
    state_dir: Path,
    client: minio.Minio,
    expected_prefix_counts: dict[str, int],
) -> None:
    """fluent-bit コンテナを起動し、期待件数に到達するまで待機する。タイムアウトすると pytest.fail。"""
    # fluent-bit を起動し、期待オブジェクト数に達するまで待機する
    with (
        DockerContainer(FLUENT_BIT_IMAGE)
        .with_env("AWS_ACCESS_KEY_ID", ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", SECRET_KEY)
        .with_volume_mapping(str(log_dir), "/log", mode="rw")
        .with_volume_mapping(
            str(config_path), "/fluent-bit/etc/fluent-bit.yml", mode="ro"
        )
        .with_volume_mapping(str(state_dir), "/state", mode="rw")
        .with_network(network)
        .with_command(
            "/fluent-bit/bin/fluent-bit -c /fluent-bit/etc/fluent-bit.yml"
        ) as fluent_bit
    ):
        try:
            for prefix, expected_count in expected_prefix_counts.items():
                wait_until(
                    lambda p=prefix, n=expected_count: count_objects(client, p) >= n
                )
        except WaitTimeoutError as error:
            fluent_bit_logs = decode_logs(fluent_bit.get_logs())
            pytest.fail(
                f"fluent-bit の待機に失敗しました: {error}\n\nfluent-bit のログ:\n{fluent_bit_logs}"
            )


def run_ingester_cli(
    ingester_dir: Path,
    db_path: Path,
    endpoint: str,
    command: str,
    initial_maximum_load: int = 1000,
) -> subprocess.CompletedProcess[str]:
    """ingester の run.py を CLI として実行し、stdout/stderr を呼び出し元で検証できるようにする。"""
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


def append_rtc_stats_log(log_dir: Path) -> None:
    """rtc_stats ログに 1 行追加し、新規オブジェクト送信の契機を作る。"""
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


def get_s3_cursor(
    con: duckdb.DuckDBPyConnection, log_type: str
) -> tuple[Any, ...] | None:
    """指定ログ種別の S3 カーソル (object_name, last_modified) を返す。未登録時は None。"""
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
            assert run.returncode == 0, f"init が失敗しました: {run.stderr}"
            assert duckdb_path.exists()

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count = fetch_scalar(con, "SELECT COUNT(*) FROM rtc_stats")
                session_webhook_count = fetch_scalar(
                    con, "SELECT COUNT(*) FROM session_webhook"
                )
                s3_objects_count = fetch_scalar(con, "SELECT COUNT(*) FROM s3_objects")

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
    log_dir = create_test_log_dir(
        tmp_path, source_log_dir, include_session_webhook=False
    )

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
            assert run.returncode == 0, f"init が失敗しました: {run.stderr}"
            # 旧挙動で出ていた InvalidInputException が消えていること
            assert "InvalidInputException" not in run.stdout
            assert "InvalidInputException" not in run.stderr

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count = fetch_scalar(con, "SELECT COUNT(*) FROM rtc_stats")
                session_webhook_table_count = fetch_scalar(
                    con,
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'",
                )
                s3_objects_count = fetch_scalar(con, "SELECT COUNT(*) FROM s3_objects")

            # rtc_stats は通常どおり取り込まれること
            assert rtc_stats_count > 0
            # 欠損している session_webhook テーブルは作成されないこと
            assert session_webhook_table_count == 0
            # rtc_stats のみ取り込まれるため、カーソルテーブルも 1 行になる
            assert s3_objects_count == 1

            # 未作成ターゲット (session_webhook) が欠損していても update 全体が成功すること
            update_run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "update")
            assert update_run.returncode == 0, (
                f"update が失敗しました: {update_run.stderr}"
            )

            with duckdb.connect(str(duckdb_path)) as con:
                rtc_stats_count_after_update = fetch_scalar(
                    con, "SELECT COUNT(*) FROM rtc_stats"
                )
                session_webhook_table_count_after_update = fetch_scalar(
                    con,
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='session_webhook'",
                )
                s3_objects_count_after_update = fetch_scalar(
                    con, "SELECT COUNT(*) FROM s3_objects"
                )

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
            assert init_run.returncode == 0, f"init が失敗しました: {init_run.stderr}"

            with duckdb.connect(str(duckdb_path)) as con:
                before_count = fetch_scalar(con, "SELECT COUNT(*) FROM rtc_stats")
                before_cursor = get_s3_cursor(con, "rtc_stats")

            append_rtc_stats_log(log_dir)
            rtc_stats_object_count_before = count_objects(
                client, f"{PREFIX}/rtc_stats/"
            )
            run_fluent_bit_and_wait(
                network,
                log_dir,
                config_path,
                state_dir,
                client,
                {f"{PREFIX}/rtc_stats/": rtc_stats_object_count_before + 1},
            )

            update_run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "update")
            assert update_run.returncode == 0, (
                f"update が失敗しました: {update_run.stderr}"
            )

            with duckdb.connect(str(duckdb_path)) as con:
                after_count = fetch_scalar(con, "SELECT COUNT(*) FROM rtc_stats")
                after_cursor = get_s3_cursor(con, "rtc_stats")

            assert after_count > before_count
            assert after_cursor is not None
            assert before_cursor is not None
            # 新規オブジェクト取り込み後は cursor が進む
            assert after_cursor != before_cursor

            # 新規ログなしの update では重複取り込みしないことを確認する
            update_run_again = run_ingester_cli(
                ingester_dir, duckdb_path, endpoint, "update"
            )
            assert update_run_again.returncode == 0, (
                f"update (再実行) が失敗しました: {update_run_again.stderr}"
            )

            with duckdb.connect(str(duckdb_path)) as con:
                final_count = fetch_scalar(con, "SELECT COUNT(*) FROM rtc_stats")
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
                lambda: (
                    minio.Minio(
                        endpoint,
                        access_key=ACCESS_KEY,
                        secret_key=SECRET_KEY,
                        secure=False,
                    ).list_buckets()
                    is not None
                )
            )

            run = run_ingester_cli(ingester_dir, duckdb_path, endpoint, "init")
            assert run.returncode != 0
            assert "S3 bucket not found" in run.stderr
