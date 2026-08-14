# テストで Any を使用

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-remove-any-in-tests
- Polished: {YYYY-MM-DD}

## 目的

テストコードで typing.Any を使用しており、 shiguredo-python 規約 (「Any を使わないこと。 どうしても必要な場合は理由をコメントで明記すること」) に違反している。 具象型または object に置き換える。

## 現状

Any を使用している箇所:

- ingester/tests/test_ingester.py: import (11 行)、 update_timestamp_for_rtc_stats の obj 引数 (86 行)、 get_latest_object の戻り値 (142 行)
- ingester/tests/test_fluent_bit_rustfs_integration.py: import (5 行)、 2 箇所 (21, 150 行)
- ingester/tests/test_grafana_integration.py: import (10 行)、 4 箇所 (48, 53, 130, 140 行)

置き換え可能な例:

- get_latest_object は minio.datatypes.Object を返すため、 戻り値型を Object にできる
- update_timestamp_for_rtc_stats の obj は fetchall の 4 要素行なので、 4 要素タプル型にできる

## 設計方針

- 各箇所で Any を具象型に置き換える (戻り値が特定できるものはその型、 できないものは object)
- どうしても Any が必要な箇所には理由コメントを付ける

## 完了条件

- ingester/tests/ 配下の全テストから Any の使用がなくなる (理由コメント付きのものを除く)
- 全テストが引き続き通過する (ruff / ty 含む)
