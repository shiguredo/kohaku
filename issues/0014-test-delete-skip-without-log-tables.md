# delete の LOG_TARGETS テーブル不在スキップ経路が未テスト

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/add-delete-skip-test
- Polished: {YYYY-MM-DD}

## 目的

ingester/src/run.py の delete_log_by_timestamp が LOG_TARGETS テーブル不在時にスキップする経路がテストされていない。 本番フローでは空バケットで init した DB (s3_objects のみ存在、 LOG_TARGETS テーブルなし) に対して delete が毎サイクル実行されるため、 この経路の例外が本番 delete ループを壊す可能性がある。

## 現状

- delete_log_by_timestamp は `if not table_exists(...): return 0` でテーブル不在をスキップする (ingester/src/run.py の delete_log_by_timestamp)
- 既存の delete テスト (ingester/tests/test_run_unit.py の delete 系) はすべて rtc_stats / session_webhook を事前作成した DB を前提にしており、 テーブル不在スキップ経路が実行されない
- test_init_and_update_on_empty_bucket (ingester/tests/test_ingester.py) は delete まで到達していない

## 設計方針

- 空バケット init 相当の DB (s3_objects のみ存在) に対して delete を実行し、 完走することと .copy が作られないことを検証するテストを追加する
- あわせて delete が全体として完走し、 exit が成功することを確認する

## 完了条件

- LOG_TARGETS テーブルが存在しない DB に対して delete が例外なく完走することを検証するテストが追加され、 通過する
- .copy ファイルが作られないことを検証する
- 既存の delete テストが引き続き通過する
