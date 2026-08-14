# run.py の型ヒント欠如

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-add-type-hints
- Polished: {YYYY-MM-DD}

## 目的

ingester/src/run.py のほぼ全関数に型ヒントがなく、 shiguredo-python 規約 (「型ヒントを必ず付けること」「from __future__ import annotations をデフォルトで有効にすること」) に違反している。 型ヒントを付与して規約に準拠させる。

## 現状

- 型ヒントがあるのは iter_objects / keep_latest_objects / collect_update_targets の 3 関数のみ
- positive_int / load_columns / init / initialize_log_table / is_after_s3_cursor / delete / handle_cli_error / main など 30 以上の関数に引数・戻り値の型ヒントがない
- `from __future__ import annotations` も未使用
- args は SimpleNamespace で受け渡しており、 型が不定形

## 設計方針

- 全関数にシグネチャの型ヒントを付与する
- `from __future__ import annotations` を追加する
- args の SimpleNamespace は、 規模によっては dataclass への置き換えを検討する (置き換える場合は本 issue のスコープで判断する)
- 定数 (DEFAULT_* 等) にも型ヒントを付ける

## 完了条件

- ingester/src/run.py の全関数・定数に型ヒントが付与される
- `from __future__ import annotations` が追加される
- ty check / ruff / 全テストが引き続き通過する
