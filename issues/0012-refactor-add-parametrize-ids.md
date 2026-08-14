# parametrize の ids 未指定が多数

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-add-parametrize-ids
- Polished: {YYYY-MM-DD}

## 目的

pytest.mark.parametrize に ids を指定していないテストが多数あり、 shiguredo-python 規約 (「pytest.mark.parametrize の ids を必ず指定し、 失敗時に何のケースか分かるようにすること」) に違反している。 全 parametrize に ids を付与する。

## 現状

ids 未指定の parametrize:

- ingester/tests/test_run_unit.py: positive_int 系 (18, 25 行)、 制御文字系 (328 行)、 関数オブジェクト (609 行)、 その他 (625 行)
- ingester/tests/test_run_sh.py: 必須環境変数系 (87 行)
- scripts/tests/test_mc_init.py: (237, 264, 317 行)
- scripts/tests/test_run_ingester.py: (85, 106 行)

特に制御文字の parametrize (test_run_unit.py の制御文字系) は ids 未指定だと生の制御バイト (NUL / LF 等) がテスト ID に混入し、 失敗出力が読めない。 関数オブジェクトを parametrize している箇所 (test_run_unit.py の 609 行付近) はデフォルト ID にオブジェクトアドレスが入り、 実行ごとに ID が変動する。

なお、 ids を指定済みの parametrize (test_ingester.py の broken-gzip 等) は既に存在する。

## 設計方針

- 全 parametrize に pytest.param(..., id="...") 形式で ids を付与する
- 制御文字系は `id="nul"` / `id="lf"` 等の読みやすい ID にする
- 関数オブジェクト系は `pytest.param(run.update, id="update")` 形式にする

## 完了条件

- ingester/ と scripts/ 配下の全 parametrize に ids が付与される
- 全テストが引き続き通過する (ruff / ty 含む)
