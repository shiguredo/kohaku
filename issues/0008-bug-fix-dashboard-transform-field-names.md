# rtc-stats.json の transform が実フィールド名と不一致

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-dashboard-transform-field-names
- Polished: {YYYY-MM-DD}

## 目的

Grafana ダッシュボード (grafana/dashboards/kohaku/rtc-stats.json) の各サブパネルの transform (filterFieldsByName) が、 実際のクエリ結果のフィールド名と一致しない名前を参照しており、 パネルが空になる可能性がある問題を修正する。

## 現状

rtc-stats.json の多数のサブパネル (パネル 41 を例に、 パネル 42〜65、 90〜97、 4〜39、 68〜97 に同パターン) の filterFieldsByName は次の設定になっている:

```json
{
  "id": "filterFieldsByName",
  "options": {
    "include": {
      "names": ["time", "A jitter"],
      "pattern": "^(jitter .+|Time)"
    }
  }
}
```

- `names` の `"A jitter"` は series の表示名であり、 duckdb データソースのクエリ結果のフィールド名はカラム名のまま (例: `jitter`) のため、 フィールドが一致せずパネルが空になる可能性がある
- `pattern` の `^(jitter .+|Time)` も実フィールド名 (`jitter` / `time` 小文字) に一致しない
- クエリ側の refId は A のため、 フィールド名に `A ` プレフィックスは付かない (review-code の実機調査による)

## 設計方針

実機 (Grafana) でダッシュボードを開き、 クエリ結果の実際のフィールド名を確認したうえで、 transform の `names` / `pattern` を実フィールド名に修正する。 全サブパネルの一括修正が必要。

## 完了条件

- rtc-stats.json の全サブパネルの filterFieldsByName が実フィールド名と一致し、 ダッシュボードの各パネルがデータを表示することを実機で確認する
- 既存の Grafana 統合テスト (ingester/tests/test_grafana_integration.py) が引き続き通過する
