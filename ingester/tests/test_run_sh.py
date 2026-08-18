"""ingester/run.sh の必須環境変数バリデーションをテストする。

Docker Compose 経由で ingester コンテナの ENTRYPOINT として使われる本スクリプトの
必須環境変数チェック層 (`:?...`) が想定通り動作することを bash イメージで検証する。
実 uv 起動 / trap 挙動 / umask などの複雑な統合的挙動はここではカバーしない
(コスト対効果で見送り、 必要になれば別途対応する)。

systemd 用 scripts/run-ingester.sh と対称構造でテストを組む
(scripts/tests/test_run_ingester.py 参照)。 全必須環境変数を埋めた場合は最終 exec の
uv 起動で失敗するが、 本テストでは env バリデーションの挙動だけを検証する。
"""

from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException

BASH_IMAGE = "bash:5"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "run.sh"

# ingester/run.sh が要求する必須環境変数 (スクリプト冒頭のチェック順)
REQUIRED_VARS = (
    "DUCKDB_DB_PATH",
    "S3_ENDPOINT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "S3_PREFIX",
    "RETENTION_PERIOD",
    "UPDATE_INTERVAL",
)


def _full_env() -> dict[str, str]:
    """ingester/run.sh 用に全必須環境変数をダミー値で埋めた dict を返す。

    S3_ENDPOINT は RFC 6761 で予約された .invalid TLD を使い、 万一テストが S3 接続まで
    到達しても DNS 解決段階で必ず失敗させる (scripts/tests/test_run_ingester.py と同じ方針)。
    UPDATE_INTERVAL は数値である必要はなく、 バリデーション層通過後の sleep で解釈される
    のでダミー値で問題ない。
    """
    return {
        "DUCKDB_DB_PATH": "/tmp/duck.db",
        "S3_ENDPOINT": "s3.invalid",
        "AWS_ACCESS_KEY_ID": "dummy-key",
        "AWS_SECRET_ACCESS_KEY": "dummy-secret",
        "S3_BUCKET": "kohaku",
        "S3_PREFIX": "log",
        "RETENTION_PERIOD": "7",
        "UPDATE_INTERVAL": "300",
    }


def _run_script(env: dict[str, str]) -> tuple[int, str]:
    """bash イメージで ingester/run.sh を実行し、 (exit_code, stderr) を返す。

    ingester/run.sh の shebang は `#!/bin/bash` だが bash:5 イメージ (Alpine ベース) には
    `/bin/bash` がない (bash 本体は `/usr/local/bin/bash`)。 shebang による直接実行は
    `bad interpreter` で失敗するため、 明示的に bash コマンドで起動する。 本番の Ubuntu
    24.04 ベースコンテナでは `/bin/bash` があるため shebang 経由でも動くが、 テストでは
    Alpine 系の軽量 bash イメージを使う都合上こちらの経路にする。
    """
    container = DockerContainer(
        BASH_IMAGE,
        command="bash /scripts/run.sh",
    ).with_volume_mapping(str(SCRIPT_PATH), "/scripts/run.sh", "ro")
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


@pytest.mark.parametrize("missing_var", REQUIRED_VARS, ids=REQUIRED_VARS)
def test_run_sh_rejects_missing_required_var(missing_var: str) -> None:
    """必須環境変数を 1 つでも欠くと exit 非 0 で is required を出すことを確認する。"""
    env = _full_env()
    env.pop(missing_var)

    status_code, stderr = _run_script(env)

    assert status_code != 0, (
        f"必須 env を抜いたのに exit 0 になりました (missing={missing_var}): {stderr}"
    )
    assert f"{missing_var} is required" in stderr, (
        f"stderr に '{missing_var} is required' が含まれていません: {stderr}"
    )
