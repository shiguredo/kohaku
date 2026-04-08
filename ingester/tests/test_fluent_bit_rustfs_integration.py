import subprocess
import time
from pathlib import Path

import duckdb
import minio
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

ACCESS_KEY = "kohakuadmin"
SECRET_KEY = "kohakuadmin"
BUCKET = "kohaku"
PREFIX = "log"
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-alpha.89"
FLUENT_BIT_IMAGE = "fluent/fluent-bit"


class WaitTimeoutError(Exception):
    pass


def wait_until(condition, timeout_sec=120, interval_sec=1):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if condition():
            return
        time.sleep(interval_sec)
    raise WaitTimeoutError(f"condition was not met within {timeout_sec} seconds")


def count_objects(client, prefix):
    return len(list(client.list_objects(BUCKET, prefix=prefix, recursive=True)))


def decode_logs(logs):
    if isinstance(logs, bytes):
        return logs.decode("utf-8", errors="replace")
    return str(logs)


@pytest.fixture
def fluent_bit_config_file(tmp_path):
    config_path = tmp_path / "fluent-bit.yml"
    config_path.write_text(
        """
service:
  flush:        1
  daemon:       Off
  log_level:    info
  http_server: Off

pipeline:
  inputs:
    - name: tail
      path: /log/rtc_stats.jsonl
      parser: json
      read_from_head: true
      db: /tmp/rtc_stats.db
      tag: rtc_stats
  outputs:
    - name: s3
      match: 'rtc_stats'
      bucket: kohaku
      endpoint: http://rustfs:9000
      compression: gzip
      s3_key_format: /log/$TAG/%Y/%m/%d/$UUID.gz
      upload_timeout: 10s
      json_date_key: off

parsers:
  - name: json
    format: json
    time_key: timestamp
    time_format: '%Y-%m-%dT%H:%M:%S.%L%z'
    time_keep: on
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


def test_runpy_init_with_fluent_bit_and_rustfs(tmp_path, fluent_bit_config_file):
    repo_root = Path(__file__).resolve().parents[2]
    ingester_dir = repo_root / "ingester"
    # fluent-bit が読み取るテスト用ログ
    log_dir = ingester_dir / "tests" / "log"
    duckdb_path = tmp_path / "duck.db"

    # RustFS と fluent-bit を同一 Docker network 上で接続する
    with Network() as network:
        with (
            DockerContainer(RUSTFS_IMAGE)
            .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
            .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
            .with_network(network)
            .with_network_aliases("rustfs")
            .with_exposed_ports(9000) as rustfs
        ):
            endpoint = f"{rustfs.get_container_host_ip()}:{rustfs.get_exposed_port(9000)}"
            client = minio.Minio(
                endpoint,
                access_key=ACCESS_KEY,
                secret_key=SECRET_KEY,
                secure=False,
            )

            wait_until(lambda: client.list_buckets() is not None)
            client.make_bucket(BUCKET)

            # fluent-bit でローカルログを RustFS に転送する
            with (
                DockerContainer(FLUENT_BIT_IMAGE)
                .with_env("AWS_ACCESS_KEY_ID", ACCESS_KEY)
                .with_env("AWS_SECRET_ACCESS_KEY", SECRET_KEY)
                .with_volume_mapping(str(log_dir), "/log", mode="ro")
                .with_volume_mapping(str(fluent_bit_config_file), "/fluent-bit/etc/fluent-bit.yml", mode="ro")
                .with_network(network)
                .with_command("/fluent-bit/bin/fluent-bit -c /fluent-bit/etc/fluent-bit.yml") as fluent_bit
            ):
                try:
                    wait_until(lambda: count_objects(client, f"{PREFIX}/rtc_stats/") > 0)
                except WaitTimeoutError as error:
                    fluent_bit_logs = decode_logs(fluent_bit.get_logs())
                    rustfs_logs = decode_logs(rustfs.get_logs())
                    pytest.fail(
                        f"{error}\n\n"
                        f"fluent-bit logs:\n{fluent_bit_logs}\n\n"
                        f"rustfs logs:\n{rustfs_logs}"
                    )

            # run.py の CLI を実行して、RustFS 上のログを DuckDB に取り込む
            cmd = [
                "uv",
                "run",
                "python",
                "src/run.py",
                "--db",
                str(duckdb_path),
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
                "1000",
                "init",
            ]
            run = subprocess.run(
                cmd,
                cwd=ingester_dir,
                capture_output=True,
                text=True,
                check=False,
            )

            assert run.returncode == 0, run.stderr
            assert duckdb_path.exists()

            # DuckDB への取り込み結果を検証する
            con = duckdb.connect(str(duckdb_path))
            try:
                rtc_stats_count = con.execute("SELECT COUNT(*) FROM rtc_stats").fetchone()[0]
                s3_objects_count = con.execute("SELECT COUNT(*) FROM s3_objects").fetchone()[0]
            finally:
                con.close()

            assert rtc_stats_count > 0
            assert s3_objects_count == 1
