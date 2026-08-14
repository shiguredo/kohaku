# PBT (hypothesis) が存在しない

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/add-property-based-tests
- Polished: {YYYY-MM-DD}

## 目的

ingester のテストに PBT (hypothesis) が 1 件も存在せず、 shiguredo-python 規約 (「PBT (Property-Based Testing) は hypothesis を使うこと」「PBT でカバーできるものを単体テストで書かないこと」) に違反している。 純関数のプロパティを PBT で検証する。

## 現状

- hypothesis は ingester/pyproject.toml のテスト依存に存在しない
- tests/prop_*.py も存在しない
- 純関数 (keep_latest_objects / collect_update_targets / is_after_s3_cursor / get_target_urls) の性質が手書き列挙テスト (ingester/tests/test_ingester.py の keep_latest_objects 系・collect_update_targets 系、 ingester/tests/test_run_unit.py の is_after_s3_cursor 系) で代替されている

## 設計方針

- hypothesis をテスト依存に追加する
- tests/prop_run.py を新設し、 次のプロパティを検証する:
  - keep_latest_objects: 参照実装 (全件ソート + 先頭 N 件) と結果が一致すること、 降順であること、 件数が上限以下であること
  - collect_update_targets: 対象集合の昇順・上限・カーソルより新しい集合の prefix になっていること (ナイーブ参照実装とのラウンドトリップ)
  - is_after_s3_cursor: タプル辞書順比較と一致すること
  - get_target_urls: s3://bucket/key 形式が生成されること

## 完了条件

- tests/prop_run.py が追加され、 上記のプロパティが hypothesis で検証される
- 既存の単体テストが引き続き通過する (PBT で代替できる手書き列挙テストは削除または整理する)
- ruff / ty が引き続き通過する
