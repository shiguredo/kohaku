import base64
import json
import os
import shutil
import subprocess
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

# Grafana API / ダッシュボード JSON はネストが深く具象型が存在しないため Any を使う
type GrafanaJson = dict[str, Any]

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


def as_grafana_json(value: object) -> GrafanaJson:
    """JSON オブジェクトを GrafanaJson として取り出す。キーが str でない場合は失敗する。"""
    assert isinstance(value, dict)
    result: GrafanaJson = {}
    for key, item in value.items():
        assert isinstance(key, str)
        result[key] = item
    return result


def as_grafana_json_list(value: object) -> list[GrafanaJson]:
    """JSON 配列を GrafanaJson のリストとして取り出す。配列でない場合は失敗する。"""
    assert isinstance(value, list)
    return [as_grafana_json(item) for item in value]


def build_auth_header(user: str, password: str) -> dict[str, str]:
    """Grafana の Basic 認証ヘッダーを生成する。"""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def request_json(
    url: str,
    method: str = "GET",
    body: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> GrafanaJson | None:
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
            f"{method} {url} が HTTP {error.code} で失敗しました: {detail}"
        ) from error
    if not payload:
        return None
    parsed: object = json.loads(payload.decode("utf-8"))
    return as_grafana_json(parsed)


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


def collect_datasource_uids(dashboard: GrafanaJson) -> set[str]:
    """ダッシュボード JSON からパネルとテンプレート変数が参照する datasource uid を収集する。

    パネルとテンプレート変数の datasource が uid を持つ辞書の場合にその uid を返す。
    "-- Dashboard --" (ダッシュボードルートの datasource を継承する特殊値) は
    参照先がダッシュボードルートの datasource 設定になるため、ここでは収集しない。
    """
    uids: set[str] = set()

    templating = as_grafana_json(dashboard.get("templating", {}))
    for variable in as_grafana_json_list(templating.get("list", [])):
        datasource = variable.get("datasource")
        if isinstance(datasource, dict) and datasource.get("uid"):
            uids.add(datasource["uid"])

    def visit(panels: list[GrafanaJson]) -> None:
        for panel in panels:
            datasource = panel.get("datasource")
            if (
                isinstance(datasource, dict)
                and datasource.get("uid")
                and datasource["uid"] != "-- Dashboard --"
            ):
                uids.add(datasource["uid"])
            visit(as_grafana_json_list(panel.get("panels", [])))

    visit(as_grafana_json_list(dashboard.get("panels", [])))
    return uids


def collect_dashboard_inheriting_panels(dashboard: GrafanaJson) -> list[str]:
    """ダッシュボードルートの datasource を継承する ("-- Dashboard --") パネルのタイトル一覧を返す。"""
    titles: list[str] = []

    def visit(panels: list[GrafanaJson]) -> None:
        for panel in panels:
            datasource = panel.get("datasource")
            if (
                isinstance(datasource, dict)
                and datasource.get("uid") == "-- Dashboard --"
            ):
                title = panel.get("title", "")
                assert isinstance(title, str)
                titles.append(title)
            visit(as_grafana_json_list(panel.get("panels", [])))

    visit(as_grafana_json_list(dashboard.get("panels", [])))
    return titles


@pytest.mark.usefixtures("init_grafana_plugin")
def test_dashboard_panels_reference_provisioned_datasource_uid(tmp_path):
    """ダッシュボードのパネルが参照する datasource uid が provisioning の uid と一致することを確認する。

    provisioning (grafana/datasources/duckdb.yml) で uid 未指定の datasource は
    Grafana が自動採番する。 ダッシュボード JSON にハードコードされた uid が
    自動採番の uid と一致しない場合、 パネルは datasource not found になる。

    あわせて、 Kohaku.json の type のみ (uid なし) の datasource 参照が、
    Grafana によって datasource name から uid へ解決されることを確認する。
    """
    duckdb_dir = create_duckdb_readonly_copy(tmp_path)

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
            str(GRAFANA_DATASOURCES_DIR),
            "/etc/grafana/provisioning/datasources",
            mode="ro",
        )
        .with_volume_mapping(
            str(GRAFANA_DASHBOARDS_DIR / "kohaku.yml"),
            "/etc/grafana/provisioning/dashboards/kohaku.yml",
            mode="ro",
        )
        .with_volume_mapping(
            str(GRAFANA_DASHBOARDS_DIR / "kohaku"),
            "/var/lib/grafana/dashboards/kohaku",
            mode="ro",
        )
        .with_volume_mapping(
            str(PLUGIN_DIR),
            "/var/lib/grafana/plugins/motherduck-duckdb-datasource",
            mode="ro",
        )
        .with_volume_mapping(str(duckdb_dir.parent), "/var/lib/kohaku", mode="rw")
    )

    try:
        container.start()
    except ContainerStartException as exc:
        pytest.fail(f"Docker Engine へ接続できないためテストを実行できません: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(3000)
        base_url = f"http://{host}:{port}"
        auth_header = build_auth_header(ADMIN_USER, ADMIN_PASSWORD)

        wait_for_grafana(base_url, auth_header)

        datasource = request_json(
            f"{base_url}/api/datasources/name/{DATA_SOURCE_NAME}", headers=auth_header
        )
        assert datasource is not None
        provisioned_uid = datasource["uid"]

        dashboard_path = GRAFANA_DASHBOARDS_DIR / "kohaku" / "rtc-stats.json"
        dashboard = as_grafana_json(
            json.loads(dashboard_path.read_text(encoding="utf-8"))
        )
        referenced_uids = collect_datasource_uids(dashboard)
        inheriting_panels = collect_dashboard_inheriting_panels(dashboard)

        # ハードコードされた uid が provisioning の uid と一致しない場合は、
        # 新規環境でパネルが datasource not found になる
        assert referenced_uids == {provisioned_uid}, (
            f"ダッシュボードが参照する uid {referenced_uids} が "
            f"provisioning の uid {provisioned_uid} と一致しません"
        )
        # "-- Dashboard --" を参照するパネルはダッシュボードルートの datasource を
        # 継承するため、ルートの datasource が設定されている必要がある
        if inheriting_panels:
            assert dashboard.get("datasource") is not None, (
                f"ダッシュボードルートの datasource が未設定ですが、 "
                f"継承するパネルがあります: {inheriting_panels[:5]}"
            )

        # Kohaku.json のパネルは type のみ (uid なし) の datasource 参照で、
        # Grafana が datasource name から uid へ解決する。 provisioning 後に
        # Grafana が保存したダッシュボードのパネル参照が、 provisioned uid に
        # 解決されていることを確認する。
        kohaku_dashboard = request_json(
            f"{base_url}/api/dashboards/uid/ceg4rfpqzngu8d", headers=auth_header
        )
        assert kohaku_dashboard is not None

        def assert_datasource_resolved(datasource: object) -> None:
            if not isinstance(datasource, dict):
                return
            resolved = as_grafana_json(datasource)
            uid = resolved.get("uid")
            if uid:
                assert uid == provisioned_uid, (
                    f"Kohaku.json のパネルが参照する uid {uid} が "
                    f"provisioning の uid {provisioned_uid} と一致しません"
                )

        for panel in kohaku_dashboard["dashboard"]["panels"]:
            assert_datasource_resolved(panel.get("datasource"))
            for target in panel.get("targets", []):
                assert_datasource_resolved(target.get("datasource"))
    finally:
        container.stop()


def extract_first_table_value(
    response: GrafanaJson, ref_id: str = "A", field_name: str = "count"
) -> object:
    """/api/ds/query の結果から最初のテーブル値を取り出す。"""
    frame = response["results"][ref_id]["frames"][0]
    field_names = [field["name"] for field in frame["schema"]["fields"]]
    field_index = field_names.index(field_name)
    return frame["data"]["values"][field_index][0]


def query_grafana_datasource(
    base_url: str, auth_header: Mapping[str, str], datasource_uid: str
) -> GrafanaJson | None:
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

    def health_is_ready() -> bool:
        try:
            payload = request_json(f"{base_url}/api/health", headers=auth_header)
            return payload is not None and payload["database"] == "ok"
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


@pytest.fixture(scope="session")
def init_grafana_plugin() -> None:
    """pytest セッション中に 1 回だけ Grafana プラグイン取得用の make init を実行する。

    `make init` は repo root の Makefile で plugins/motherduck-duckdb-datasource を取得し、
    Grafana コンテナにマウントするときに必要となる。 副作用として repo root に plugins/ と
    rustfs/ を作成するため、 既に PLUGIN_DIR/plugin.json が存在する場合は
    make init をスキップして repo state を汚さないようにする (空ディレクトリだけ残った
    途中失敗状態を検出するため plugin.json まで確認する)。 Grafana テスト専用の session
    スコープ fixture のため、 共有 conftest.py には置かず本モジュール内に置く。 テスト側
    からは @pytest.mark.usefixtures("init_grafana_plugin") で明示的に依存させる。
    """
    if (PLUGIN_DIR / "plugin.json").is_file():
        return
    repo_root = Path(__file__).resolve().parents[2]
    subprocess.run(["make", "init"], cwd=repo_root, check=True)


@pytest.mark.usefixtures("init_grafana_plugin")
def test_grafana_can_query_duckdb_data(tmp_path):
    """Grafana の datasource から DuckDB を実際に参照できることを確認する。"""
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
            str(GRAFANA_DATASOURCES_DIR),
            "/etc/grafana/provisioning/datasources",
            mode="ro",
        )
        .with_volume_mapping(
            str(GRAFANA_DASHBOARDS_DIR / "kohaku.yml"),
            "/etc/grafana/provisioning/dashboards/kohaku.yml",
            mode="ro",
        )
        .with_volume_mapping(
            str(GRAFANA_DASHBOARDS_DIR / "kohaku"),
            "/var/lib/grafana/dashboards/kohaku",
            mode="ro",
        )
        .with_volume_mapping(
            str(PLUGIN_DIR),
            "/var/lib/grafana/plugins/motherduck-duckdb-datasource",
            mode="ro",
        )
        .with_volume_mapping(str(duckdb_dir.parent), "/var/lib/kohaku", mode="rw")
    )

    try:
        container.start()
    except ContainerStartException as exc:
        # Docker が使えない環境はテスト環境不備として失敗させる。start 失敗時は
        # container.stop() を呼ばずに終了し、二次例外で pytest.fail の原因が
        # 上書きされるリスクを避ける。
        pytest.fail(f"Docker Engine へ接続できないためテストを実行できません: {exc}")

    try:
        # 起動後は health と datasource の provision 完了を待ってから API を叩く。
        host = container.get_container_host_ip()
        port = container.get_exposed_port(3000)
        base_url = f"http://{host}:{port}"
        auth_header = build_auth_header(ADMIN_USER, ADMIN_PASSWORD)

        wait_for_grafana(base_url, auth_header)

        # provision された datasource の name / type / uid を確認する
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
