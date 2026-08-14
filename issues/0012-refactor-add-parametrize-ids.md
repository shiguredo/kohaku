# parametrize の ids 未指定が多数

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-add-parametrize-ids
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

pytest.mark.parametrize に ids を指定していないテストが多数あり、 shiguredo-python 規約 (「pytest.mark.parametrize の ids を必ず指定し、 失敗時に何のケースか分かるようにすること」) に違反している。 全 parametrize に ids を付与する。

## 現状

ids 未指定の parametrize (テスト関数名で示す):

- ingester/tests/test_run_unit.py:
  - test_positive_int_rejects_non_positive (0 以下 3 値)
  - test_positive_int_accepts_positive (正の値 3 組)
  - test_ensure_safe_sql_string_literal_rejects_control_characters (制御文字 6 種)
  - test_update_or_delete_raises_file_not_found_when_db_missing (run.update / run.delete)
  - test_is_broken_db_error_detects_known_patterns (破損パターン 3 件)
- ingester/tests/test_run_sh.py: test_run_sh_rejects_missing_required_var (必須環境変数 8 件)
- scripts/tests/test_mc_init.py: test_mc_init_rejects_missing_required_var、 test_mc_init_rejects_invalid_max_retries、 test_mc_init_rejects_invalid_retry_interval、 test_mc_init_rejects_json_unsafe_credential
- scripts/tests/test_run_ingester.py: test_run_ingester_init_or_update_rejects_missing_required_var (必須環境変数 × サブコマンド 2 種の 2 つの parametrize)、 test_run_ingester_delete_rejects_missing_required_var

pytest 9.0.3 では、 制御文字の parametrize はデフォルトで ASCII エスケープされた ID (例: [\x00]) になり、 関数オブジェクトは __name__ が ID になる (例: [update])。 生バイトの混入や ID の変動は発生しないが、 エスケープ表記 (例: [\x1f]) はどの制御文字か直感しにくく、 規約の「失敗時に何のケースか分かる」を十分に満たさない。

なお、 ids を指定済みの parametrize (test_ingester.py の broken-gzip / malformed-json / missing-primary-key 等、 test_run_unit.py の file-not-found / cli-usage-error 等) は既に存在する。

## 設計方針

- 全 parametrize に ids を付与する (stacked parametrize は各デコレータに個別に付与する。 例: サブコマンド側は ids=["init", "update"])
- 必須環境変数系 (REQUIRED_VARS 等の定数を parametrize に渡す系統) は、 値と id を同一ソースに保つため `ids=定数名` 形式を使う (例: `ids=REQUIRED_VARS`)
- 値がそのまま分かる系統 (positive_int 系、 message 系) は、 値をそのまま id にする (例: id="0"、 id="-1")
- タプル値の parametrize (test_positive_int_accepts_positive の (入力, 期待値)、 test_mc_init_rejects_json_unsafe_credential の (キー, 値)) は、 id を入力値のみにする (例: id="1")
- 制御文字系は読みやすい ID を付ける (例: id="nul"、 id="lf"、 id="cr"、 id="tab"、 id="unit-separator"、 id="del")
- 関数オブジェクト系は pytest.param(run.update, id="update") 形式にする
- test_run_unit.py は 0009 (run.py の型ヒント欠如) でも変更対象のため、 0009 の完了後に本 issue を実装する (同一ファイルの変更が衝突しないように)

## 完了条件

- ingester/ と scripts/ 配下の全 parametrize に ids が付与される
- ids 未指定の parametrize が残っていないことを確認する (ruff に parametrize の ids を要求するルールがないため、 コードレビューで確認する)
- 全テストが引き続き通過する (ruff / ty 含む)
- CHANGES.md の `### misc` セクションに変更履歴が追記される (テストのみのリファクタリングのため)
