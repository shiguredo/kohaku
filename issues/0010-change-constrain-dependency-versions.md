# pyproject.toml の依存が上限なし `>=` 単独指定

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/change-constrain-dependency-versions
- Polished: {YYYY-MM-DD}

## 目的

ingester/pyproject.toml の依存ライブラリのバージョン指定が上限なしの `>=` 単独指定になっており、 shiguredo-python 規約 (「上限なしの `>=` 単独指定は禁止」「依存ライブラリのバージョン指定はマイナーバージョンまでとすること」「依存ライブラリには用途をコメントで明記すること」) に違反している。 バージョン境界を切り、 用途コメントを追加する。

## 現状

ingester/pyproject.toml の dependencies:

```toml
"minio>=7.2",
"pytz>=2026.1",
"pyyaml>=6.0",
```

- 3 つとも上限なしの `>=` 単独指定で、 破壊的変更を吸い込む
- minio (S3 クライアント) と pyyaml (DUCKDB_COLUMNS のカラム定義 YAML 読み込み) に用途コメントがない (duckdb / pytz / urllib3 にはコメントあり)
- duckdb==1.4.2 は Grafana プラグイン追従の理由コメントがあり許容

## 設計方針

- minio / pytz / pyyaml にマイナーで境界を切る (例: `minio>=7.2,<8`、 `pytz>=2026.1,<2027`、 `pyyaml>=6,<7`)
- minio と pyyaml に用途コメントを追加する

## 完了条件

- ingester/pyproject.toml の全依存にマイナー境界が設定される
- 全依存に用途コメントが付与される
- uv sync と全テストが引き続き通過する
