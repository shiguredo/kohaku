import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.exceptions import ContainerStartException

from .helpers import wait_until


GRAFANA_IMAGE = "grafana/grafana:12.4.3-ubuntu"
PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "motherduck-duckdb-datasource"
GRAFANA_DATASOURCES_DIR = Path(__file__).resolve().parents[2] / "grafana" / "datasources"
GRAFANA_DASHBOARDS_DIR = Path(__file__).resolve().parents[2] / "grafana" / "dashboards"
ADMIN_USER = "shiguredo"
ADMIN_PASSWORD = "password"
DATA_SOURCE_NAME = "motherduck-duckdb-datasource"


@pytest.fixture(scope="session", autouse=True)
def init_grafana_plugin():
    """
    Grafana の integration test に必要な plugin を事前に作成する。
    """
    # pytest 開始時に 1 回だけ init を実行し、plugins 配下をテスト前提の状態にする。
    repo_root = Path(__file__).resolve().parents[2]
    subprocess.run(["make", "init"], cwd=repo_root, check=True)


def build_auth_header(user, password):
    """
    Grafana の Basic 認証ヘッダーを生成する。
    :param user: ユーザー名
    :param password: パスワード
    :return: Authorization ヘッダーを持つ辞書
    """
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def request_json(url, method="GET", body=None, headers=None):
    """
    JSON API を呼び出し、レスポンスを辞書として返す。
    :param url: リクエスト先 URL
    :param method: HTTP メソッド
    :param body: JSON で送るリクエストボディ
    :param headers: 追加ヘッダー
    :return: JSON デコード結果、または空レスポンス時は None
    """
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
        raise RuntimeError(f"{method} {url} failed with HTTP {error.code}: {detail}") from error
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def create_duckdb_readonly_copy(base_dir: Path) -> Path:
    """
    Grafana が参照できる DuckDB の読み取り専用コピーを作成する。
    :param base_dir: 一時ディレクトリのルート
    :return: duck.db.readonly を含むディレクトリの Path
    """
    duckdb_dir = base_dir / "duckdb"
    duckdb_dir.mkdir(parents=True, exist_ok=True)

    # Grafana からの読み取り確認に必要な最小限のテーブルだけを作る。
    db_path = duckdb_dir / "duck.db"
    con = duckdb.connect(str(db_path))
    try:
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
    finally:
        con.close()

    readonly_path = duckdb_dir / "duck.db.readonly"
    shutil.copyfile(db_path, readonly_path)
    os.chmod(readonly_path, 0o666)
    return duckdb_dir


def extract_first_table_value(response, ref_id="A", field_name="count"):
    """
    /api/ds/query の結果から最初のテーブル値を取り出す。
    :param response: Grafana の JSON レスポンス
    :param ref_id: 抽出対象の query refId
    :param field_name: 抽出対象の列名
    :return: 指定列の先頭値
    """
    frame = response["results"][ref_id]["frames"][0]
    field_names = [field["name"] for field in frame["schema"]["fields"]]
    field_index = field_names.index(field_name)
    return frame["data"]["values"][field_index][0]


def query_grafana_datasource(base_url, auth_header, datasource_uid):
    """
    Grafana の datasource に対して DuckDB の件数取得クエリを実行する。
    :param base_url: Grafana のベース URL
    :param auth_header: 認証用ヘッダー
    :param datasource_uid: datasource UID
    :return: /api/ds/query の JSON レスポンス
    """
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
    return request_json(f"{base_url}/api/ds/query", method="POST", body=query_body, headers=auth_header)


def wait_for_grafana(base_url, auth_header):
    """
    Grafana の起動と datasource の provision 完了を待つ。
    :param base_url: Grafana のベース URL
    :param auth_header: 認証用ヘッダー
    :return: なし
    """
    # Grafana の起動完了と datasource の provision 完了を別々に待つ。
    def health_is_ready():
        try:
            return request_json(f"{base_url}/api/health", headers=auth_header)["database"] == "ok"
        except Exception:
            return False

    def datasource_is_ready():
        try:
            return request_json(f"{base_url}/api/datasources/name/{DATA_SOURCE_NAME}", headers=auth_header) is not None
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
        .with_volume_mapping(str(grafana_datasources_dir), "/etc/grafana/provisioning/datasources", mode="ro")
        .with_volume_mapping(str(grafana_dashboards_dir / "kohaku.yml"), "/etc/grafana/provisioning/dashboards/kohaku.yml", mode="ro")
        .with_volume_mapping(str(grafana_dashboards_dir / "kohaku"), "/var/lib/grafana/dashboards/kohaku", mode="ro")
        .with_volume_mapping(str(plugin_dir), "/var/lib/grafana/plugins/motherduck-duckdb-datasource", mode="ro")
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
        datasource = request_json(f"{base_url}/api/datasources/name/{DATA_SOURCE_NAME}", headers=auth_header)
        assert datasource is not None
        assert datasource["name"] == DATA_SOURCE_NAME
        assert datasource["type"] == DATA_SOURCE_NAME
        assert "uid" in datasource

        response = query_grafana_datasource(base_url, auth_header, datasource["uid"])
        assert response is not None
        assert extract_first_table_value(response) == 1
    finally:
        container.stop()
