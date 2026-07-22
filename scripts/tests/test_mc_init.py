import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException
from testcontainers.core.network import Network


MC_IMAGE = "minio/mc:RELEASE.2025-07-21T05-28-08Z"
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-beta.2"
BASH_IMAGE = "bash:5"
AWS_ACCESS_KEY_ID = "kohaku-access-key"
AWS_SECRET_ACCESS_KEY = "kohaku-secret-key"
S3_ENDPOINT = "rustfs:9000"
S3_BUCKET = "kohaku-ci-bucket"
RETENTION_PERIOD = "7"
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MC_INIT_SCRIPT_PATH = SCRIPTS_DIR / "mc-init.sh"

# mc-init.sh が要求する必須環境変数 (シェルの必須変数チェック順)
MC_INIT_REQUIRED_VARS = (
    "S3_ENDPOINT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "RETENTION_PERIOD",
)


def run_and_assert_success(container: DockerContainer) -> None:
    # 一時コンテナを 1 回実行し、終了コードが 0 であることを検証する。
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
def rustfs_env() -> Iterator[dict[str, object]]:
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


def _full_mc_init_env() -> dict[str, str]:
    """mc-init.sh 用に全必須環境変数をダミー値で埋めた dict を返す。

    必須環境変数を 1 つ抜いたケースでは mc へ接続する前に :? で終了するため、
    エンドポイントや認証情報の値そのものは検証に影響しない。
    """
    return {
        "S3_ENDPOINT": "s3.invalid",
        "AWS_ACCESS_KEY_ID": "dummy-key",
        "AWS_SECRET_ACCESS_KEY": "dummy-secret",
        "S3_BUCKET": "kohaku",
        "RETENTION_PERIOD": "7",
    }


def _run_mc_init(env: dict[str, str]) -> tuple[int, str]:
    """bash イメージで mc-init.sh を実行し、(exit_code, stderr) を返す。"""
    container = DockerContainer(
        BASH_IMAGE,
        command="/scripts/mc-init.sh",
    ).with_volume_mapping(str(MC_INIT_SCRIPT_PATH), "/scripts/mc-init.sh", "ro")
    for k, v in env.items():
        container = container.with_env(k, v)

    try:
        container.start()
    except ContainerStartException as exc:
        pytest.fail(f"Docker Engine へ接続できないためテストを実行できません: {exc}")

    try:
        wrapped = container.get_wrapped_container()
        result = wrapped.wait()
        status_code = result.get("StatusCode", -1)
        stderr_bytes = wrapped.logs(stdout=False, stderr=True)
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return status_code, stderr
    finally:
        container.stop()


@pytest.mark.parametrize("missing_var", MC_INIT_REQUIRED_VARS)
def test_mc_init_rejects_missing_required_var(missing_var: str) -> None:
    """いずれかの必須環境変数が欠けると exit 非 0 で is required を出すことを確認する。"""
    env = _full_mc_init_env()
    env.pop(missing_var)

    status_code, stderr = _run_mc_init(env)

    assert status_code != 0, (
        f"必須 env を抜いたのに exit 0 になりました (missing={missing_var}): {stderr}"
    )
    assert f"{missing_var} is required" in stderr, (
        f"stderr に '{missing_var} is required' が含まれていません: {stderr}"
    )


@pytest.mark.parametrize(
    "invalid_value",
    [
        "abc",  # 非数値
        "0",  # 0 は明示的に弾かれる
        "-1",  # 負数 (case の `*[!0-9]*` でマッチして弾かれる)
    ],
)
def test_mc_init_rejects_invalid_max_retries(invalid_value: str) -> None:
    """MC_INIT_MAX_RETRIES が正の整数でない場合に、エラー終了することを確認する。

    空文字は ``${MC_INIT_MAX_RETRIES:-5}`` のフォールバックで 5 に置換されてバリデーション
    層に到達しないため、 本テストの対象から外している。
    """
    env = _full_mc_init_env()
    env["MC_INIT_MAX_RETRIES"] = invalid_value

    status_code, stderr = _run_mc_init(env)

    assert status_code != 0, (
        f"不正な MC_INIT_MAX_RETRIES なのに exit 0 になりました (value={invalid_value!r}): {stderr}"
    )
    assert "MC_INIT_MAX_RETRIES must be a positive integer" in stderr, (
        f"stderr に 'MC_INIT_MAX_RETRIES must be a positive integer' が含まれていません (value={invalid_value!r}): {stderr}"
    )


@pytest.mark.parametrize(
    "invalid_value",
    [
        "abc",  # 非数値
        "-1",  # 負数 (case の `*[!0-9]*` でマッチして弾かれる)
    ],
)
def test_mc_init_rejects_invalid_retry_interval(invalid_value: str) -> None:
    """MC_INIT_RETRY_INTERVAL が非負整数でない場合に、エラー終了することを確認する。

    空文字は ``${MC_INIT_RETRY_INTERVAL:-2}`` のフォールバックで 2 に置換されてバリデーション
    層に到達しないため、 本テストの対象から外している。
    """
    env = _full_mc_init_env()
    env["MC_INIT_RETRY_INTERVAL"] = invalid_value

    status_code, stderr = _run_mc_init(env)

    assert status_code != 0, (
        f"不正な MC_INIT_RETRY_INTERVAL なのに exit 0 になりました (value={invalid_value!r}): {stderr}"
    )
    assert "MC_INIT_RETRY_INTERVAL must be a non-negative integer" in stderr, (
        f"stderr に 'MC_INIT_RETRY_INTERVAL must be a non-negative integer' が含まれていません (value={invalid_value!r}): {stderr}"
    )


def test_mc_init_accepts_zero_retry_interval() -> None:
    """MC_INIT_RETRY_INTERVAL=0 を有効な値として扱うことを確認する。

    bash イメージには mc コマンドが無いため接続ループは必ず失敗するが、 その失敗は
    'must be a non-negative integer' ではなく後段の Timed out 経路で起きる。 これにより
    「0 はバリデーションを通過する」 ことを間接的に検証する。
    """
    env = _full_mc_init_env()
    env["MC_INIT_RETRY_INTERVAL"] = "0"
    # テスト時間短縮のため最小リトライ回数に絞る
    env["MC_INIT_MAX_RETRIES"] = "2"

    status_code, stderr = _run_mc_init(env)

    assert "MC_INIT_RETRY_INTERVAL must be a non-negative integer" not in stderr, (
        f"MC_INIT_RETRY_INTERVAL=0 がバリデーションで弾かれました: {stderr}"
    )
    # バリデーション層を通過してリトライループへ進んだ証拠
    assert "Timed out waiting for storage endpoint" in stderr, (
        f"バリデーション通過後にリトライループへ進んだ形跡がありません: {stderr}"
    )
    # mc コマンドが見つからず接続失敗 → MAX_RETRIES 到達で exit 1 になる
    assert status_code != 0, (
        f"mc コマンド未インストール環境で正常終了してはいけません: {stderr}"
    )
