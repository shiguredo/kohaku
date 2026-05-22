import base64
import json
import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.docker_client import DockerClient
from testcontainers.core.exceptions import ContainerStartException

from .helpers import wait_until


# 使用する Grafana の Docker イメージタグ
GRAFANA_IMAGE = "grafana/grafana:12.4.3-ubuntu"
# 一時ファイルの group を gid=0 に揃えるための軽量イメージ
ALPINE_IMAGE = "alpine:3.20"
# Grafana にマウントする DuckDB プラグインのソースディレクトリ
PLUGIN_DIR = (
    Path(__file__).resolve().parents[2] / "plugins" / "motherduck-duckdb-datasource"
)
# Grafana に provisioning する datasource 設定の格納ディレクトリ
GRAFANA_DATASOURCES_DIR = (
    Path(__file__).resolve().parents[2] / "grafana" / "datasources"
)
# Grafana に provisioning するダッシュボード定義の格納ディレクトリ
GRAFANA_DASHBOARDS_DIR = Path(__file__).resolve().parents[2] / "grafana" / "dashboards"
# Grafana コンテナの管理者ユーザー名 (テスト専用)
ADMIN_USER = "shiguredo"
# Grafana コンテナの管理者パスワード (テスト専用)
ADMIN_PASSWORD = "password"
# Grafana に provisioning する datasource 名 兼 plugin id
DATA_SOURCE_NAME = "motherduck-duckdb-datasource"


def build_auth_header(user: str, password: str) -> dict[str, str]:
    """Grafana の Basic 認証ヘッダーを生成する。"""
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def request_json(
    url: str,
    method: str = "GET",
    body: Any = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """JSON API を呼び出してレスポンスを辞書として返す。空レスポンスは None。"""
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    data = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {url} failed with HTTP {error.code}: {detail}"
        ) from error
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def create_duckdb_readonly_copy(base_dir: Path) -> Path:
    """Grafana が参照できる DuckDB の読み取り専用コピーを作成する。"""
    duckdb_dir = base_dir / "duckdb"
    duckdb_dir.mkdir(parents=True, exist_ok=True)

    # Grafana からの読み取り確認に必要な最小限のテーブルだけを作る。
    db_path = duckdb_dir / "duck.db"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE rtc_stats (
                timestamp TIMESTAMP,
                connection_id VARCHAR,
                value INTEGER
            )
            """
        )
        con.execute(
            "INSERT INTO rtc_stats VALUES (?, ?, ?)",
            ("2025-07-25 06:06:51", "connection-1", 1),
        )

    readonly_path = duckdb_dir / "duck.db.readonly"
    shutil.copyfile(db_path, readonly_path)
    # 本番では grafana ユーザーを kohaku グループに追加して 0o660 のファイルへアクセスする運用のため、
    # テストでも同じ 0o660 にする。grafana コンテナ内の grafana ユーザーは primary gid=0 で動くため、
    # ホスト側ファイルの group を gid=0 にすることで本番と同じパーミッションで読み書きできる状態にする。
    os.chmod(readonly_path, 0o660)
    # pytest が tmp_path を 0o700 で作成するため、コンテナユーザーがディレクトリを辿れるよう 0o755 にする。
    os.chmod(duckdb_dir, 0o755)
    os.chmod(base_dir, 0o755)
    assign_root_group(base_dir)
    return duckdb_dir


def assign_root_group(path: Path) -> None:
    """ホスト側パス配下のファイルとディレクトリの group を gid=0 に変更する。

    grafana コンテナの grafana ユーザーは primary gid=0 で動くため、
    group を 0 に揃えることで本番と同じ 0o660 のままアクセスできる。
    chown はホスト側ユーザーの権限では実行できないことが多いため、
    root で動く使い捨ての alpine コンテナを経由して chown を実行する。
    """
    DockerClient().run(
        ALPINE_IMAGE,
        command=["chown", "-R", ":0", "/data"],
        volumes={str(path): {"bind": "/data", "mode": "rw"}},
        remove=True,
    )


def extract_first_table_value(
    response: Mapping[str, Any], ref_id: str = "A", field_name: str = "count"
) -> Any:
    """/api/ds/query の結果から最初のテーブル値を取り出す。"""
    frame = response["results"][ref_id]["frames"][0]
    field_names = [field["name"] for field in frame["schema"]["fields"]]
    field_index = field_names.index(field_name)
    return frame["data"]["values"][field_index][0]


def query_grafana_datasource(
    base_url: str, auth_header: Mapping[str, str], datasource_uid: str
) -> Any:
    """Grafana の datasource に対して DuckDB の件数取得クエリを実行する。"""
    query_body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {
                    "uid": datasource_uid,
                    "type": DATA_SOURCE_NAME,
                },
                "rawSql": "SELECT COUNT(*) AS count FROM rtc_stats",
                "format": 1,
                "rawQuery": True,
                "editorMode": "code",
                "maxDataPoints": 1000,
                "intervalMs": 1000,
                "sql": {
                    "columns": [
                        {
                            "parameters": [],
                            "type": "function",
                        }
                    ],
                    "groupBy": [
                        {
                            "property": {
                                "type": "string",
                            },
                            "type": "groupBy",
                        }
                    ],
                    "limit": 50,
                },
            }
        ],
        "from": "now-5m",
        "to": "now",
    }
    return request_json(
        f"{base_url}/api/ds/query", method="POST", body=query_body, headers=auth_header
    )


def wait_for_grafana(base_url: str, auth_header: Mapping[str, str]) -> None:
    """Grafana の起動と datasource の provision 完了を待つ。"""

    # Grafana の起動完了と datasource の provision 完了を別々に待つ。
    def health_is_ready() -> bool:
        try:
            return (
                request_json(f"{base_url}/api/health", headers=auth_header)["database"]
                == "ok"
            )
        except Exception:
            return False

    def datasource_is_ready() -> bool:
        try:
            return (
                request_json(
                    f"{base_url}/api/datasources/name/{DATA_SOURCE_NAME}",
                    headers=auth_header,
                )
                is not None
            )
        except Exception:
            return False

    wait_until(health_is_ready)
    wait_until(datasource_is_ready)


def test_grafana_can_query_duckdb_data(tmp_path):
    """Grafana の datasource から DuckDB を実際に参照できることを確認する。"""
    repo_root = Path(__file__).resolve().parents[2]
    grafana_datasources_dir = repo_root / "grafana" / "datasources"
    grafana_dashboards_dir = repo_root / "grafana" / "dashboards"
    plugin_dir = PLUGIN_DIR
    duckdb_dir = create_duckdb_readonly_copy(tmp_path)

    # Grafana コンテナに、設定ファイルと plugin ディレクトリをそのままマウントする。
    container = (
        DockerContainer(GRAFANA_IMAGE)
        .with_exposed_ports(3000)
        .with_env("GF_LOG_MODE", "console")
        .with_env("GF_PATHS_DATA", "/var/lib/grafana")
        .with_env("GF_SECURITY_ADMIN_USER", ADMIN_USER)
        .with_env("GF_SECURITY_ADMIN_PASSWORD", ADMIN_PASSWORD)
        .with_env("GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS", DATA_SOURCE_NAME)
        .with_env("GF_PATHS_HOME", "/usr/share/grafana")
        .with_env("GF_PATHS_CONFIG", "/etc/grafana/grafana.ini")
        .with_env("GF_PATHS_PLUGINS", "/var/lib/grafana/plugins")
        .with_env("GF_PATHS_PROVISIONING", "/etc/grafana/provisioning")
        .with_env("GF_PLUGINS_FORWARD_HOST_ENV_VARS", DATA_SOURCE_NAME)
        .with_volume_mapping(
            str(grafana_datasources_dir),
            "/etc/grafana/provisioning/datasources",
            mode="ro",
        )
        .with_volume_mapping(
            str(grafana_dashboards_dir / "kohaku.yml"),
            "/etc/grafana/provisioning/dashboards/kohaku.yml",
            mode="ro",
        )
        .with_volume_mapping(
            str(grafana_dashboards_dir / "kohaku"),
            "/var/lib/grafana/dashboards/kohaku",
            mode="ro",
        )
        .with_volume_mapping(
            str(plugin_dir),
            "/var/lib/grafana/plugins/motherduck-duckdb-datasource",
            mode="ro",
        )
        .with_volume_mapping(str(duckdb_dir.parent), "/var/lib/kohaku", mode="rw")
    )

    try:
        container.start()
    except ContainerStartException as exc:
        # Docker が使えない環境はテスト環境不備として失敗させる。
        pytest.fail(f"Docker Engine へ接続できないためテストを実行できません: {exc}")

    try:
        # 起動後は health と datasource の provision 完了を待ってから API を叩く。
        host = container.get_container_host_ip()
        port = container.get_exposed_port(3000)
        base_url = f"http://{host}:{port}"
        auth_header = build_auth_header(ADMIN_USER, ADMIN_PASSWORD)

        wait_for_grafana(base_url, auth_header)

        # データソースの確認
        datasource = request_json(
            f"{base_url}/api/datasources/name/{DATA_SOURCE_NAME}", headers=auth_header
        )
        assert datasource is not None
        assert datasource["name"] == DATA_SOURCE_NAME
        assert datasource["type"] == DATA_SOURCE_NAME
        assert "uid" in datasource

        response = query_grafana_datasource(base_url, auth_header, datasource["uid"])
        assert response is not None
        assert extract_first_table_value(response) == 1
    finally:
        container.stop()
