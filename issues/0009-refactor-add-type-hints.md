# run.py の型ヒント欠如

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-add-type-hints
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

ingester/src/run.py のほぼ全関数に型ヒントがなく、 shiguredo-python 規約 (「型ヒントを必ず付けること」「from __future__ import annotations をデフォルトで有効にすること」「Any を使わないこと」) に違反している。 型ヒントを付与して規約に準拠させる。

## 現状

- 型ヒントがあるのは iter_objects / keep_latest_objects / collect_update_targets の 3 関数のみ (計 39 関数中)
- positive_int / load_columns / init / initialize_log_table / is_after_s3_cursor / delete / handle_cli_error / main など 36 関数に引数・戻り値の型ヒントがない
- `from __future__ import annotations` は未導入
- args は build_parser の parse_args が返す argparse.Namespace で受け渡しており、 属性の定義が run.py の build_parser と tests/helpers.py の full_args の 2 箇所に重複し、 具象型が存在しないため型チェッカが追えない (テスト側のみ types.SimpleNamespace で構築している)

## 設計方針

- `from __future__ import annotations` を追加する
- args を dataclass (Args) に置き換え、 parse_args(namespace=...) で注入する。 argparse.Namespace のままでは属性アクセスが Any になり規約違反が残るため、 dataclass 化は必須とする
- Args のフィールドデフォルトは build_parser のデフォルト値と一致させる (parse_args(namespace=...) では dataclass 側のデフォルトが優先され、 --help 表示と乖離するため)。 テスト側は Args のデフォルトに依存せず、 必要なフィールドを明示的に上書きする (例: RustFS 接続のため s3_use_ssl=False、 早期 return をすり抜けた場合の安全装置のため s3_endpoint に .invalid TLD)。 テスト側の意図的な差異 (s3_endpoint の s3.invalid、 s3_use_ssl の False) は本番デフォルトに統一しない
- func 属性は Callable 型とし、 set_defaults(func=...) で注入する関係上、 @dataclass(frozen=True) は使用しない (frozen にすると set_defaults の代入が FrozenInstanceError になる)
- 全関数にシグネチャの型ヒントを付与する (args は Args 型)
- 全てのモジュール定数 (DEFAULT_* 群、 COLUMNS_DIR、 BROKEN_DB_ERROR_PATTERNS、 BROKEN_DB_CONNECT_ERRORS、 LOG_TARGETS、 UPSERT_S3_OBJECTS_SQL 等) に型ヒントを付ける
- load_columns の戻り値は YAML 由来で、 columns の値・primary_key の要素は実行時検証されておらず任意の型になりうるため、 型を明示できない箇所は理由をコメントで明記する
- テスト側も Args に合わせて変更する: tests/helpers.py の full_args (Unknown override keys の TypeError ガードは維持する)、 tests/test_run_unit.py と tests/test_ingester.py の SimpleNamespace 構築 (make_args_for_s3 / make_args_for_delete を含む)、 部分構築 (require_s3_credentials テストの SimpleNamespace)。 既存の Any 使用 (test_ingester.py の Sequence[Any] 等) は 0013 (テストで Any を使用) の対象のため、 本 issue では Args 化に伴う変更のみを対象とする
- 型ヒント付与は動作変更を伴わない (リファクタリング)。 ty check で新規エラーが顕在化した場合は、 ヒント側を実挙動に合わせるか挙動不変の範囲で実コードを修正して解消する (例: fetchone() の戻り値 (tuple | None) の Optional 化、 テストが None を渡す箇所の Optional 化)
- 作業順序は「Args dataclass の導入 → parse_args(namespace=...) への切り替え → テスト側の変更 → 全関数への型ヒント付与」の順とする (先に型ヒントを付けると二度手間になる)
- `__all__` の追加と CHANGES.md の更新は本 issue の対象外とする

## 完了条件

- ingester/src/run.py の全関数・定数に型ヒントが付与され、 args が dataclass で受け渡される
- テストコード (tests/helpers.py、 tests/test_run_unit.py、 tests/test_ingester.py) が Args に合わせて変更され、 型ヒントが付与される
- `from __future__ import annotations` が追加される
- Args と build_parser のデフォルト値の一致を検証するテストが追加され、 通過する (乖離の再発防止)
- ty check / ruff / 全テストが引き続き通過する (ty check は型ヒント付与後に初めて関数本体を検証するため、 新規エラーを全て解消した状態を指す。 完了条件 1 の網羅性は、 Ruff の flake8-annotations (ANN) ルール群を extend-select に追加して機械検証する。 ty には disallow_untyped_defs に相当する設定が存在しないため、 ty の公式 FAQ が代替として明記する Ruff の ANN ルール群を採用する)
