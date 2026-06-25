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


def _run_script_observe_args(
    subcommand: str, env: dict[str, str]
) -> tuple[int, list[str]]:
    """偽 uv を /opt/uv/bin/uv に置いて run-ingester.sh を実行し、 uv に渡された引数列を返す。

    本物の uv は引数列を python src/run.py ... の形で受け取って実行するが、 ここでは
    引数列をそのまま stdout に出すだけのスタブ shell に差し替えて、 シェル側で組み立てた
    引数が想定通りであることを Python 側で検証する。
    """
    bootstrap = (
        "mkdir -p /opt/uv/bin\n"
        "cat > /opt/uv/bin/uv <<'EOF'\n"
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\"\n"
        "EOF\n"
        "chmod +x /opt/uv/bin/uv\n"
        f"exec /scripts/run-ingester.sh {subcommand}\n"
    )
    container = (
        DockerContainer(BASH_IMAGE, command=["-ceu", bootstrap])
        .with_kwargs(entrypoint="/bin/sh")
        .with_volume_mapping(str(SCRIPT_PATH), "/scripts/run-ingester.sh", "ro")
    )
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
        stdout = wrapped.logs(stdout=True, stderr=False).decode(
            "utf-8", errors="replace"
        )
        return status_code, stdout.splitlines()
    finally:
        container.stop()


@pytest.mark.parametrize(
    ("subcommand", "extra_env", "must_contain", "must_not_contain"),
    [
        # init: S3_USE_SSL 未設定で --s3_use_ssl は付かない
        (
            "init",
            {},
            ["init", "--db", "--s3_endpoint"],
            ["--s3_use_ssl", "--update_maximum_load"],
        ),
        # init: S3_USE_SSL=true で --s3_use_ssl が付く
        (
            "init",
            {"S3_USE_SSL": "true"},
            ["init", "--s3_use_ssl"],
            ["--update_maximum_load"],
        ),
        # update: UPDATE_MAXIMUM_LOAD 指定で --update_maximum_load が付く
        (
            "update",
            {"UPDATE_MAXIMUM_LOAD": "50"},
            ["update", "--update_maximum_load", "50"],
            [],
        ),
        # init: UPDATE_MAXIMUM_LOAD 指定でも init では --update_maximum_load が付かない
        (
            "init",
            {"UPDATE_MAXIMUM_LOAD": "50"},
            ["init"],
            ["--update_maximum_load"],
        ),
        # init: INITIAL_MAXIMUM_LOAD 指定で --initial_maximum_load が付く
        (
            "init",
            {"INITIAL_MAXIMUM_LOAD": "200"},
            ["init", "--initial_maximum_load", "200"],
            [],
        ),
    ],
)
def test_run_ingester_init_or_update_builds_expected_args(
    subcommand: str,
    extra_env: dict[str, str],
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    """init / update で uv に渡される引数列が env に応じて想定通り組み立てられることを確認する。"""
    env = _full_s3_env()
    env.update(extra_env)

    status_code, args = _run_script_observe_args(subcommand, env)

    assert status_code == 0, (
        f"引数組み立てフェーズが失敗しました (subcommand={subcommand}): {args}"
    )
    for token in must_contain:
        assert token in args, f"引数に {token!r} が含まれていません: {args}"
    for token in must_not_contain:
        assert token not in args, f"引数に {token!r} が含まれています: {args}"


def test_run_ingester_delete_builds_minimal_args() -> None:
    """delete では --db と --retention_period のみ渡され、 S3 関連の引数は付かないことを確認する。"""
    env = _full_delete_env()

    status_code, args = _run_script_observe_args("delete", env)

    assert status_code == 0, f"引数組み立てフェーズが失敗しました: {args}"
    for token in ("delete", "--db", "--retention_period"):
        assert token in args, f"引数に {token!r} が含まれていません: {args}"
    for token in ("--s3_endpoint", "--s3_access_key_id", "--s3_use_ssl"):
        assert token not in args, f"引数に {token!r} が含まれています: {args}"
