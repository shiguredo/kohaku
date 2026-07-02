"""scripts/run-ingester.sh の必須環境変数バリデーションとサブコマンド分岐をテストする。

systemd EnvironmentFile の編集忘れによる unbound variable を検出する `:?` の防御層が
init / update / delete のサブコマンドごとに正しく動作することを bash イメージで検証する。
合わせて未知サブコマンドが Usage 表示で拒否されることも確認する。

全必須環境変数を埋めた場合は最終 exec の uv 起動で失敗するが、 本テストでは env
バリデーションとサブコマンド分岐の挙動だけを検証する。
"""

from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException

BASH_IMAGE = "bash:5"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "run-ingester.sh"

# init / update が要求する必須環境変数 (run-ingester.sh の :? 順)
S3_REQUIRED_VARS = (
    "DUCKDB_DB_PATH",
    "S3_ENDPOINT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "S3_PREFIX",
)
# delete が要求する必須環境変数
DELETE_REQUIRED_VARS = (
    "DUCKDB_DB_PATH",
    "RETENTION_PERIOD",
)


def _full_s3_env() -> dict[str, str]:
    """init / update 用に全必須環境変数をダミー値で埋めた dict を返す。

    S3_ENDPOINT は RFC 6761 で予約された .invalid TLD を使い、 万一テストが S3 接続まで
    到達しても DNS 解決段階で必ず失敗させる。
    """
    return {
        "DUCKDB_DB_PATH": "/tmp/duck.db",
        "S3_ENDPOINT": "s3.invalid",
        "AWS_ACCESS_KEY_ID": "dummy-key",
        "AWS_SECRET_ACCESS_KEY": "dummy-secret",
        "S3_BUCKET": "kohaku",
        "S3_PREFIX": "log",
    }


def _full_delete_env() -> dict[str, str]:
    """delete 用に全必須環境変数をダミー値で埋めた dict を返す。"""
    return {
        "DUCKDB_DB_PATH": "/tmp/duck.db",
        "RETENTION_PERIOD": "7",
    }


def _run_script(subcommand: str, env: dict[str, str]) -> tuple[int, str]:
    """bash イメージで run-ingester.sh を実行し、(exit_code, stderr) を返す。"""
    container = DockerContainer(
        BASH_IMAGE,
        command=f"/scripts/run-ingester.sh {subcommand}",
    ).with_volume_mapping(str(SCRIPT_PATH), "/scripts/run-ingester.sh", "ro")
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


@pytest.mark.parametrize("missing_var", S3_REQUIRED_VARS)
@pytest.mark.parametrize("subcommand", ["init", "update"])
def test_run_ingester_init_or_update_rejects_missing_required_var(
    subcommand: str, missing_var: str
) -> None:
    """init / update でいずれかの必須環境変数が欠けると exit 非 0 で is required を出すことを確認する。"""
    env = _full_s3_env()
    env.pop(missing_var)

    status_code, stderr = _run_script(subcommand, env)

    assert status_code != 0, (
        f"必須 env を抜いたのに exit 0 になりました"
        f" (subcommand={subcommand}, missing={missing_var}): {stderr}"
    )
    assert f"{missing_var} is required" in stderr, (
        f"stderr に '{missing_var} is required' が含まれていません"
        f" (subcommand={subcommand}): {stderr}"
    )


@pytest.mark.parametrize("missing_var", DELETE_REQUIRED_VARS)
def test_run_ingester_delete_rejects_missing_required_var(missing_var: str) -> None:
    """delete でいずれかの必須環境変数が欠けると exit 非 0 で is required を出すことを確認する。"""
    env = _full_delete_env()
    env.pop(missing_var)

    status_code, stderr = _run_script("delete", env)

    assert status_code != 0, (
        f"必須 env を抜いたのに exit 0 になりました (missing={missing_var}): {stderr}"
    )
    assert f"{missing_var} is required" in stderr, (
        f"stderr に '{missing_var} is required' が含まれていません: {stderr}"
    )


def test_run_ingester_rejects_unknown_subcommand() -> None:
    """未知のサブコマンドを指定した場合に Usage を出して exit 非 0 で終了することを確認する。"""
    status_code, stderr = _run_script("unknown", _full_s3_env())

    assert status_code != 0, f"未知サブコマンドで exit 0 になりました: {stderr}"
    assert "Usage:" in stderr, f"stderr に Usage 表示がありません: {stderr}"
