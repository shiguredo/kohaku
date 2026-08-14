# Grafana 統合テストがダッシュボードを検証していない

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/add-dashboard-integration-test
- Polished: {YYYY-MM-DD}

## 目的

ingester/tests/test_grafana_integration.py が、 ダッシュボード (grafana/dashboards/kohaku/Kohaku.json と rtc-stats.json) 自体を一切検証していない。 ダッシュボード内の SQL の実行可否・変数参照・データソース解決が CI で検出されないため、 壊れたダッシュボードがマージをすり抜ける構造になっている。

## 現状

- test_grafana_integration.py は datasource の provisioning と SELECT COUNT(*) の実行のみを検証する
- ダッシュボード 2 枚は provisioning でマウントされているが、 ロード成功の確認もパネル SQL の実行もしていない
- テスト用 DB のスキーマ (rtc_stats に timestamp / connection_id / value のみ) が実スキーマ (DUCKDB_COLUMNS 準拠) と乖離しており、 ダッシュボードのパネル SQL (rtc_data の JSON 参照等) は実行できない
- 指摘対応 (datasource uid の固定) で、 ダッシュボードの uid 参照と provisioning の整合を検証するテストは追加済み

## 設計方針

- ダッシュボードの provisioning 後に /api/dashboards/uid/<uid> でロード確認する
- 実スキーマ (DUCKDB_COLUMNS 準拠) の DB を用意し、 ダッシュボードの各パネル SQL (rawSql) を /api/ds/query 経由で実行してエラーがないことを検証する
- テスト用フィクスチャ (ingester/tests/log/*.jsonl) のセッションをダッシュボードの変数連携で使える形に揃える (rtc_stats と session_webhook で同一セッションを使う)

## 完了条件

- provisioning されたダッシュボードのロード成功を検証するテストが追加される
- ダッシュボードの各パネル SQL が実スキーマでエラーなく実行できることを検証するテストが追加される
- 既存の Grafana 統合テストが引き続き通過する
