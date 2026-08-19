# テストで Any を使用

- Created: 2026-08-14
- Completed: 2026-08-19
- Branch: feature/refactor-remove-any-in-tests
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

テストコードで typing.Any を使用しており、 shiguredo-python 規約 (「Any を使わないこと。 どうしても必要な場合は理由をコメントで明記すること」) に違反している。 具象型または理由コメント付き Any に整理する。

## 現状

ingester/tests/ 配下の Any の使用箇所 (全 17 箇所、 3 ファイル):

- ingester/tests/test_ingester.py:
  - import (typing.Any)
  - update_timestamp_for_rtc_stats の obj 引数 (Sequence[Any])
  - get_latest_object の戻り値 (Any)
- ingester/tests/test_fluent_bit_rustfs_integration.py:
  - import (typing.Any)
  - fetch_scalar の戻り値 (Any)
  - get_s3_cursor の戻り値 (tuple[Any, ...] | None)
- ingester/tests/test_grafana_integration.py:
  - import (typing.Any)
  - request_json の body 引数 (Any = None) と戻り値 (Any)
  - collect_datasource_uids の dashboard 引数 (Mapping[str, Any]) と内の visit 関数 (list[Mapping[str, Any]])
  - collect_dashboard_inheriting_panels の dashboard 引数 (Mapping[str, Any]) と内の visit 関数 (list[Mapping[str, Any]])
  - assert_datasource_resolved の datasource 引数 (Any)
  - extract_first_table_value の response 引数 (Mapping[str, Any]) と戻り値 (Any)
  - query_grafana_datasource の戻り値 (Any)

## 設計方針

- 戻り値が特定できるものは具象型に置き換える:
  - get_latest_object → minio.datatypes.Object
  - update_timestamp_for_rtc_stats の obj → tuple[datetime.datetime, str, str, str] (fetchall の 4 要素行)
  - fetch_scalar → int (全呼び出しが SELECT COUNT(*) の結果)
  - get_s3_cursor → tuple[datetime.datetime, str] | None
- JSON レスポンスを扱う request_json の戻り値や、 ダッシュボード JSON の深いネスト構造を扱う collect_datasource_uids / collect_dashboard_inheriting_panels の Mapping[str, Any] は、 具象型が存在しない (object は subscript 不可のため置き換え不能)。 構造を絞った型への置き換え (例: request_json は TypeVar で呼び出し側の期待型に推論させる) か、 理由コメント付きで Any を残す
- 完了条件の「理由コメント付きのものを除く」に該当する箇所は、 コメントで「JSON 由来で具象型が存在しないため」等の理由を明記する
- test_ingester.py は 0009 (run.py の型ヒント欠如) でも変更対象のため、 0009 の完了後に本 issue を実装する (同一ファイルの変更が衝突しないように)

## 完了条件

- ingester/tests/ 配下の全テストから Any の使用がなくなる (理由コメント付きのものを除く)
- Any が残っていないことを確認する (コードレビューで確認する)
- 全テストが引き続き通過する (ruff / ty 含む)
- CHANGES.md の `### misc` セクションに変更履歴が追記される (テストのみのリファクタリングのため)

## 解決方法

- `test_ingester.py` の `update_timestamp_for_rtc_stats` の obj を `tuple[datetime.datetime, str, str, str]` に、 `get_latest_object` の戻り値を `minio.datatypes.Object` にした。 DuckDB の `fetchall` 行は `as_rtc_stats_row` で実行時に narrowing する
- `test_fluent_bit_rustfs_integration.py` の `fetch_scalar` を `int` (`SELECT COUNT(*)`)、 `get_s3_cursor` を `tuple[datetime.datetime, str] | None` にした。 いずれも `isinstance` で実行時に保証する
- `test_grafana_integration.py` は Grafana API / ダッシュボード JSON がネスト深く具象型が無いため、 理由コメント付きの `GrafanaJson = dict[str, Any]` を残した。 `request_json` の body は `Mapping[str, object] | None`、 戻り値は `GrafanaJson | None`。 `assert_datasource_resolved` と `extract_first_table_value` の戻り値は `object` にした
- `as_grafana_json` / `as_grafana_json_list` で JSON オブジェクト・配列を narrowing し、 `collect_*` の panels / templating 走査に使う。 非 dict / 非 list は assert で失敗させる
- `pyproject.toml` から `test_ingester.py` の ANN401 除外を外した。 fluent-bit / Grafana 統合テストはテスト関数の型ヒントが未着手のため ANN 除外を残し、 Grafana 側は JSON の Any (ANN401) も残る旨をコメントした
- CHANGES.md はリポジトリ管理外のため、 本ブランチのコミットには含めていない
