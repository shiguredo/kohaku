"""tests/ 配下で共有する pytest fixture と定数を集約するモジュール。

pytest はテストモジュールと同じディレクトリ階層を遡って conftest.py を自動的に読み込むため、
ここで `@pytest.fixture` を宣言しておけば各 `test_*.py` から import なしで利用できる。
重複している接続情報やコンテナ起動処理を一箇所にまとめ、テストごとの揺れを防ぐ目的で用意している。
"""

import os
import uuid
from collections.abc import Iterator

import minio
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from .helpers import wait_until

# テスト用に作成する S3 バケット名 (test_ingester / test_fluent_bit で共通利用)。
# pytest-xdist で並列実行したとき worker (= 別プロセス) ごとに別バケットを使うよう
# PID + uuid で一意化する。同一プロセス内では全テストが同じ BUCKET を共有する
# (session スコープの RustFS コンテナ上で reset_bucket / make_bucket を回す現行設計)。
BUCKET = f"kohaku-{os.getpid()}-{uuid.uuid4().hex[:8]}"
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


@pytest.fixture
def rustfs_network_stack() -> Iterator[tuple[Network, str]]:
    """テスト毎に新しい Docker network 上で RustFS コンテナを起動して (network, endpoint) を返す。

    fluent-bit と RustFS を同一 Docker network 上で連携させる統合テスト用。
    make_bucket は行わないため、 bucket 未作成状態を検証したいテストで使う。
    """
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
            yield network, endpoint


@pytest.fixture
def rustfs_network_ready(
    rustfs_network_stack: tuple[Network, str],
) -> tuple[Network, str, minio.Minio]:
    """rustfs_network_stack に加えて minio クライアント生成と make_bucket まで実行し、
    (network, endpoint, client) を返す。 bucket 作成済みを前提とする統合テストで使う。
    """
    network, endpoint = rustfs_network_stack
    client = minio.Minio(
        endpoint,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )
    wait_until(lambda: client.list_buckets() is not None)
    client.make_bucket(BUCKET)
    return network, endpoint, client
