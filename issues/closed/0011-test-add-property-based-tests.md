# PBT (hypothesis) が存在しない

- Created: 2026-08-14
- Completed: 2026-08-18
- Branch: feature/add-property-based-tests
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

ingester のテストに PBT (hypothesis) が 1 件も存在せず、 shiguredo-python 規約 (「PBT (Property-Based Testing) は hypothesis を使うこと」「PBT でカバーできるものを単体テストで書かないこと」) に違反している。 純関数のプロパティを PBT で検証する。 あわせて、 PBT 対象外とする get_target_urls の単体テストを追加する (直接のテストが存在しないため)。

## 現状

- hypothesis は ingester/pyproject.toml のテスト依存に存在しない
- tests/prop_*.py も存在しない
- pytest の収集設定 (ingester/pyproject.toml の [tool.pytest.ini_options]) に python_files がなく、 デフォルトの収集パターン (test_*.py / *_test.py) では prop_*.py が実行されない
- keep_latest_objects / collect_update_targets / is_after_s3_cursor の性質が手書き列挙テスト (ingester/tests/test_ingester.py の keep_latest_objects 系・collect_update_targets 系、 ingester/tests/test_run_unit.py の is_after_s3_cursor 系) で検証されている
- get_target_urls は直接のテストが存在しない (単体テストの追加が新規カバレッジになる)

## 設計方針

- hypothesis をテスト依存に追加する (バージョン境界と用途コメントを付ける)
- ingester/pyproject.toml の pytest 設定に python_files を追加し、 prop_*.py を収集対象にする (テスト関数名は test_ プレフィックスにする)
- tests/prop_run.py を新設し、 次のプロパティを検証する:
  - keep_latest_objects: ナイーブ参照実装 (全件を (last_modified, object_name) で降順ソート + 先頭 max_objects 件) と結果が一致すること。 入力は (last_modified, object_name) が一意になるように生成する (同値キーの扱いは実装詳細に依存するため、 一意化して実装詳細への依存を避ける)
  - collect_update_targets: ナイーブ参照実装 (全件を (last_modified, object_name) でソート・フィルタするオフライン実装) と結果が一致すること。 参照実装は、 target_log_objects を「カーソルより新しいオブジェクトのうち古い側 update_maximum_load 件 (昇順)」、 same_last_modified_objects を「カーソルと同値の last_modified を持つオブジェクト (カーソル行自身を除く) の全件 (入力順)」として計算する。 カーソルは生成したオブジェクトから選択する。 このプロパティはオンラインアルゴリズム (bisect.insort + pop) とオフライン実装の一致を検証する
- 入力の生成戦略: minio Object は make_object 相当のヘルパーで生成し、 last_modified は tz-aware の datetime (st.datetimes(timezones=...)) にする。 object_name は一意にする。 max_objects / update_maximum_load は 1 以上にする。 同値 last_modified のグループを意図的に含める (少数の datetime 候補から選択する) ことで、 same_last_modified_objects の非空ケースを検証する
- 空入力 (objects が空) は境界値のため PBT 対象外とし、 既存の単体テスト (test_collect_update_targets_no_candidate) でカバーする
- is_after_s3_cursor は PBT 対象としない (実装がタプル比較の 1 行で、 参照実装が実装の複製になるため)。 既存の単体テスト (比較 5 件、 tz-naive エラーパス 2 件) を残す
- get_target_urls は PBT 対象とせず、 単体テストを新規追加する (実装が f-string の 1 行で、 PBT の参照実装が実装の複製になるため)
- エラーパス (tz-naive の ValueError) とメモリ検証 (tracemalloc) は単体テストの役割のため、 PBT で代替しない

## 完了条件

- tests/prop_run.py が追加され、 上記のプロパティが hypothesis で検証される (pytest が prop_*.py を収集し、 実行する)
- get_target_urls の単体テストが追加される
- PBT で代替できる手書き列挙テスト (test_keep_latest_objects_limits_memory / test_collect_update_targets_limits_memory) を削除する
- 同値キーを検証する test_keep_latest_objects_same_last_modified、 空入力の境界値を検証する test_collect_update_targets_no_candidate、 is_after_s3_cursor 比較系は PBT で代替しないため残す
- PBT で代替できないテスト (test_keep_latest_objects_does_not_expand_all_objects / test_collect_update_targets_does_not_expand_all_objects の tracemalloc メモリ検証、 test_is_after_s3_cursor_rejects_tz_naive_* のエラーパス) は残す
- 既存の単体テストが引き続き通過する
- ruff / ty が引き続き通過する

## 解決方法

- `ingester/pyproject.toml` のテスト依存に `hypothesis>=6.165,<6.166` を追加し、 pytest の `python_files` に `prop_*.py` を加えて収集対象にした
- `ingester/tests/prop_run.py` を新設し、 `keep_latest_objects` と `collect_update_targets` がナイーブな参照実装と一致することを hypothesis で検証した。 入力は object_name を一意にし、 last_modified は少数の tz-aware datetime 候補から選んで同値グループを含める
- `get_target_urls` の単体テストを `ingester/tests/test_run_unit.py` に追加した
- PBT で代替できる `test_keep_latest_objects_limits_memory` と `test_collect_update_targets_limits_memory` を削除した。 同値キー・空入力・tracemalloc・tz-naive エラーパスの既存単体テストは残した
