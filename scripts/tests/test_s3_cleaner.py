import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException
from testcontainers.core.network import Network


MC_IMAGE = "minio/mc:RELEASE.2025-07-21T05-28-08Z"
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-alpha.89"
AWS_ACCESS_KEY_ID = "kohaku-access-key"
AWS_SECRET_ACCESS_KEY = "kohaku-secret-key"
S3_ENDPOINT = "rustfs:9000"
S3_BUCKET = "kohaku-ci-bucket"
S3_PREFIX = "cleanup-target"
SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def run_container_and_get_status(container: DockerContainer) -> int:
    # 一時コンテナを 1 回実行し、終了コードのみを返す。
    wrapped = None
    try:
        container.start()
        wrapped = container.get_wrapped_container()
        result = wrapped.wait()
        return int(result.get("StatusCode", 1))
    finally:
        container.stop()


def run_and_assert_success(container: DockerContainer) -> None:
    # 正常終了が前提のコマンド実行で利用するヘルパー。
    status_code = run_container_and_get_status(container)
    if status_code != 0:
        raise AssertionError(f"コンテナ実行に失敗しました (status={status_code})")


@pytest.fixture
def rustfs_env() -> dict[str, object]:
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "0")
    suffix = uuid4().hex[:8]
    rustfs_container = f"kohaku-ci-rustfs-cleaner-{run_id}-{run_attempt}-{suffix}"
    cleaner_container = f"kohaku-ci-s3-cleaner-runner-{run_id}-{run_attempt}-{suffix}"

    # テスト専用ネットワークを作成し、rustfs を分離して起動する。
    network_obj = Network().create()
    rustfs_obj = (
        DockerContainer(RUSTFS_IMAGE)
        .with_name(rustfs_container)
        .with_network(network_obj)
        .with_network_aliases("rustfs")
        .with_env("RUSTFS_ACCESS_KEY", AWS_ACCESS_KEY_ID)
        .with_env("RUSTFS_SECRET_KEY", AWS_SECRET_ACCESS_KEY)
    )

    try:
        # テスト開始前に rustfs を起動して、S3 互換エンドポイントを用意する。
        rustfs_obj.start()
    except ContainerStartException as exc:
        network_obj.remove()
        pytest.skip(f"Docker Engine へ接続できないためスキップします: {exc}")

    try:
        # yield でテスト本体へ実行コンテキストを渡す。
        yield {
            "network": network_obj,
            "cleaner_container": cleaner_container,
            "rustfs_obj": rustfs_obj,
        }
    finally:
        # テスト成否にかかわらず後始末を必ず実行する。
        rustfs_obj.stop()
        network_obj.remove()


def test_s3_cleaner_removes_old_object(rustfs_env: dict[str, object]) -> None:
    """s3-cleaner.sh を実行し、保持期間超過オブジェクトを削除できることを検証する。"""
    script_path = SCRIPTS_DIR / "s3-cleaner.sh"

    # 削除対象オブジェクトを事前に投入する。
    setup_object = (
        DockerContainer(
            MC_IMAGE,
            command=[
                "-ceu",
                'mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" '
                '"${AWS_SECRET_ACCESS_KEY}" >/dev/null\n'
                'mc mb --ignore-existing "storage/${S3_BUCKET}" >/dev/null\n'
                'echo "cleanup test object" | mc pipe "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null\n'
                'mc stat "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null',
            ],
        )
        .with_network(rustfs_env["network"])
        .with_kwargs(entrypoint="/bin/sh")
        .with_env("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        .with_env("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        .with_env("S3_ENDPOINT", S3_ENDPOINT)
        .with_env("S3_BUCKET", S3_BUCKET)
        .with_env("S3_PREFIX", S3_PREFIX)
    )
    run_and_assert_success(setup_object)

    time.sleep(2)

    # cleaner を短時間だけ起動し、1 回以上の削除処理を走らせる。
    cleaner = (
        DockerContainer(MC_IMAGE, command="/scripts/s3-cleaner.sh")
        .with_name(rustfs_env["cleaner_container"])
        .with_network(rustfs_env["network"])
        .with_kwargs(entrypoint="/bin/sh")
        .with_volume_mapping(str(script_path), "/scripts/s3-cleaner.sh", "ro")
        .with_env("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        .with_env("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        .with_env("S3_ENDPOINT", S3_ENDPOINT)
        .with_env("S3_BUCKET", S3_BUCKET)
        .with_env("S3_PREFIX", S3_PREFIX)
        .with_env("S3_USE_SSL", "false")
        .with_env("RETENTION_PERIOD", "0")
        .with_env("CLEANUP_INTERVAL", "1")
    )
    cleaner.start()
    time.sleep(4)
    cleaner.stop()

    # 対象オブジェクトが消えていることを終了コードで検証する。
    probe_deleted = (
        DockerContainer(
            MC_IMAGE,
            command=[
                "-ceu",
                'mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" '
                '"${AWS_SECRET_ACCESS_KEY}" >/dev/null\n'
                'mc stat "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null 2>&1',
            ],
        )
        .with_network(rustfs_env["network"])
        .with_kwargs(entrypoint="/bin/sh")
        .with_env("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        .with_env("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        .with_env("S3_ENDPOINT", S3_ENDPOINT)
        .with_env("S3_BUCKET", S3_BUCKET)
        .with_env("S3_PREFIX", S3_PREFIX)
    )
    status_code = run_container_and_get_status(probe_deleted)
    assert status_code != 0, "s3-cleaner が対象オブジェクトを削除できていません"
