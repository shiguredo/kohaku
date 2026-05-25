import os
from pathlib import Path
from uuid import uuid4

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException
from testcontainers.core.network import Network


MC_IMAGE = "minio/mc:RELEASE.2025-07-21T05-28-08Z"
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-beta.2"
AWS_ACCESS_KEY_ID = "kohaku-access-key"
AWS_SECRET_ACCESS_KEY = "kohaku-secret-key"
S3_ENDPOINT = "rustfs:9000"
S3_BUCKET = "kohaku-ci-bucket"
RETENTION_PERIOD = "7"
SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def run_and_assert_success(container: DockerContainer) -> None:
    # 一時コンテナを 1 回実行し、終了コードが 0 であることを検証する。
    wrapped = None
    try:
        container.start()
        wrapped = container.get_wrapped_container()
        result = wrapped.wait()
        status_code = result.get("StatusCode")
        if status_code != 0:
            logs = wrapped.logs(stdout=True, stderr=True).decode(
                "utf-8", errors="replace"
            )
            raise AssertionError(
                f"コンテナ実行に失敗しました (status={status_code}):\n{logs}"
            )
    finally:
        container.stop()


@pytest.fixture
def rustfs_env() -> dict[str, object]:
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT", "0")
    suffix = uuid4().hex[:8]
    rustfs_container = f"kohaku-ci-rustfs-{run_id}-{run_attempt}-{suffix}"

    # テスト専用ネットワークを毎回作成し、他テストとの衝突を防ぐ。
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
        pytest.fail(f"Docker Engine へ接続できないためテストを実行できません: {exc}")

    try:
        # yield でテスト本体へ実行コンテキストを渡す。
        yield {"network": network_obj, "rustfs_obj": rustfs_obj}
    finally:
        # テスト成否にかかわらず後始末を必ず実行する。
        rustfs_obj.stop()
        network_obj.remove()


def test_mc_init_creates_bucket(rustfs_env: dict[str, object]) -> None:
    """mc-init.sh を実行し、S3 バケットの作成と保持期間設定ができることを検証する。"""
    script_path = SCRIPTS_DIR / "mc-init.sh"

    # 本番同等の起動方法で mc-init.sh を実行し、バケット作成と ILM ルール登録まで進むことを確認する。
    mc_init = (
        DockerContainer(MC_IMAGE, command="/scripts/mc-init.sh")
        .with_network(rustfs_env["network"])
        .with_kwargs(entrypoint="/bin/sh")
        .with_volume_mapping(str(script_path), "/scripts/mc-init.sh", "ro")
        .with_env("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        .with_env("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        .with_env("S3_ENDPOINT", S3_ENDPOINT)
        .with_env("S3_BUCKET", S3_BUCKET)
        .with_env("S3_USE_SSL", "false")
        .with_env("RETENTION_PERIOD", RETENTION_PERIOD)
        .with_env("MC_INIT_MAX_RETRIES", "30")
        .with_env("MC_INIT_RETRY_INTERVAL", "1")
    )
    run_and_assert_success(mc_init)

    # 作成されたバケットへアクセスできること、および ILM ルールが登録されていることを確認する。
    verify_bucket = (
        DockerContainer(
            MC_IMAGE,
            command=[
                "-ceu",
                'mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" '
                '"${AWS_SECRET_ACCESS_KEY}" >/dev/null\n'
                'mc ls "storage/${S3_BUCKET}" >/dev/null\n'
                'mc ilm rule ls "storage/${S3_BUCKET}" >/dev/null',
            ],
        )
        .with_network(rustfs_env["network"])
        .with_kwargs(entrypoint="/bin/sh")
        .with_env("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        .with_env("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        .with_env("S3_ENDPOINT", S3_ENDPOINT)
        .with_env("S3_BUCKET", S3_BUCKET)
    )
    run_and_assert_success(verify_bucket)
