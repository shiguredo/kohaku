"""tests/ 配下で共有する pytest fixture と定数を集約するモジュール。

pytest はテストモジュールと同じディレクトリ階層を遡って conftest.py を自動的に読み込むため、
ここで `@pytest.fixture` を宣言しておけば各 `test_*.py` から import なしで利用できる。
重複している接続情報やコンテナ起動処理を一箇所にまとめ、テストごとの揺れを防ぐ目的で用意している。
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer


# テスト用に作成する S3 バケット名 (test_ingester / test_fluent_bit で共通利用)
BUCKET = "kohaku"
# RustFS コンテナのアクセスキー (テスト専用)
ACCESS_KEY = "kohakuadmin"
# RustFS コンテナのシークレットキー (テスト専用)
SECRET_KEY = "kohakuadmin"
# S3 オブジェクトキー先頭のプレフィックス (例: "log/rtc_stats/...")
PREFIX = "log"
# RustFS コンテナが LISTEN する内部ポート
RUSTFS_PORT = 9000
# 使用する RustFS の Docker イメージタグ
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-beta.2"


@pytest.fixture(scope="session")
def rustfs_container() -> Iterator[DockerContainer]:
    """セッション全体で 1 つだけ起動する RustFS コンテナを提供する。

    test_ingester.py のように単純な S3 互換ストレージとしての RustFS を利用するテスト群が
    共有する。fluent-bit との統合テストのように Docker network を必要とするケースでは
    別途テスト内で個別に DockerContainer を起動するため、この fixture は使わない。
    """
    with (
        DockerContainer(RUSTFS_IMAGE)
        .with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
        .with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
        .with_exposed_ports(RUSTFS_PORT) as rustfs
    ):
        yield rustfs


@pytest.fixture(scope="session")
def rustfs_endpoint(rustfs_container: DockerContainer) -> str:
    """rustfs_container の公開エンドポイント (host:port) を返す。"""
    return f"{rustfs_container.get_container_host_ip()}:{rustfs_container.get_exposed_port(RUSTFS_PORT)}"


@pytest.fixture(scope="session")
def init_grafana_plugin() -> None:
    """pytest セッション中に 1 回だけ Grafana プラグイン取得用の make init を実行する。

    `make init` は repo root の Makefile で plugins/motherduck-duckdb-datasource を取得し、
    test_grafana_integration.py が Grafana コンテナにマウントするときに必要となる。
    Grafana を使わないテスト (test_run_unit.py や test_ingester.py 等) で不要なネット越し
    curl を走らせないよう、autouse にせず Grafana テスト側から明示的に依存させる。
    """
    repo_root = Path(__file__).resolve().parents[2]
    subprocess.run(["make", "init"], cwd=repo_root, check=True)
