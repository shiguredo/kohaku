# Grafana 統合テストがダッシュボードを検証していない

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/add-dashboard-integration-test
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

ingester/tests/test_grafana_integration.py が、 ダッシュボード (grafana/dashboards/kohaku/Kohaku.json と rtc-stats.json) 自体を一切検証していない。 ダッシュボード内の SQL の実行可否・変数参照・データソース解決が CI で検出されないため、 壊れたダッシュボードがマージをすり抜ける構造になっている。

## 現状

- test_grafana_integration.py は datasource の provisioning と SELECT COUNT(*) の実行のみを検証する
- ダッシュボード 2 枚は provisioning でマウントされているが、 Kohaku.json のロード確認 (uid: ceg4rfpqzngu8d) と datasource uid の解決確認のみ実施済みで、 rtc-stats.json のロード確認 (uid: feg4t1lbp3b40b) とパネル SQL の実行はしていない
- テスト用 DB のスキーマ (rtc_stats に timestamp / connection_id / value のみ) が実スキーマ (DUCKDB_COLUMNS 準拠。 rtc_stats は 19 カラム + rtc_data JSON、 session_webhook は id / timestamp / req JSON) と乖離しており、 ダッシュボードのパネル SQL (rtc_data の JSON 参照等) は実行できない
- 指摘対応 (datasource uid の固定) で、 ダッシュボードの uid 参照と provisioning の整合を検証するテストは追加済み (test_dashboard_panels_reference_provisioned_datasource_uid)

## 設計方針

- ダッシュボードの provisioning 後に /api/dashboards/uid/feg4t1lbp3b40b (rtc-stats.json) でロード確認する (Kohaku.json は既存テストで確認済みのため対象外)
- 実スキーマ (DUCKDB_COLUMNS 準拠) の DB を用意し、 ダッシュボードのパネル SQL (rawSql) を /api/ds/query 経由で実行してエラーがないことを検証する。 対象は rawSql を持つパネルのみ (rtc-stats.json の Session Panel / connections / Audio・Video Source Panel 系、 Kohaku.json の Latest connections。 `-- Dashboard --` 参照の stat パネルは rawSql を持たないため対象外)
- パネル SQL の実行にあたり、 次の変数を解決する:
  - テンプレート変数 (${connection_id} / $session_id / ${limit} / ${offset} 等) はテスト側で具体的な値に置換する (/api/ds/query はフロントエンドの変数展開を経由しないため)
  - query 型のテンプレート変数 (connection_id / inbound_rtp_audio_rtc_id 等) は、 その変数クエリを先に /api/ds/query で実行して得た値を置換する (変数クエリの実行可否もダッシュボードの検証対象になる)
  - Grafana のマクロ ($__timeFilter / $__interval / $__interval_ms) は /api/ds/query の timeRange / intervalMs パラメータで置換する
- テスト用フィクスチャ (ingester/tests/log/*.jsonl) のセッションをダッシュボードの変数連携で使える形に揃える (rtc_stats と session_webhook で同一セッションを使う)。 フィクスチャは既存テスト (test_ingester.py / test_fluent_bit_rustfs_integration.py) でも使用しているため、 変更時は既存テストへの影響を確認し、 必要なテストを更新する

## 完了条件

- provisioning された rtc-stats.json のロード成功を検証するテストが追加される
- ダッシュボードの各パネル SQL (rawSql) が実スキーマでエラーなく実行できることを検証するテストが追加される
- query 型テンプレート変数のクエリがエラーなく実行できることを検証する
- 全テストが引き続き通過する (ruff / ty 含む)
- CHANGES.md の `### misc` セクションに変更履歴が追記される (テストのみの追加のため)
