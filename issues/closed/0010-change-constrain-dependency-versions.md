# pyproject.toml の依存が上限なし `>=` 単独指定

- Created: 2026-08-14
- Completed: 2026-08-17
- Branch: feature/change-constrain-dependency-versions
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

ingester/pyproject.toml の本番依存 (dependencies) のうち、 上限なしの `>=` 単独指定になっている minio / pytz / pyyaml が、 shiguredo-python 規約 (「上限なしの `>=` 単独指定は禁止」「依存ライブラリのバージョン指定はマイナーバージョンまでとすること」「依存ライブラリには用途をコメントで明記すること」) に違反している。 バージョン境界を切り、 用途コメントを追加して規約に準拠させる。

## 現状

ingester/pyproject.toml の dependencies は次の構成になっている (現行のロック済みバージョンは uv.lock で minio 7.2.20 / pytz 2026.2 / pyyaml 6.0.3):

```toml
  # Grafana DuckDB Data Source Plugin の DuckDB のバージョンにあわせる
  "duckdb==1.4.2",
  "minio>=7.2",
  # duckdb が TIMESTAMPTZ カラムを Python に変換するときに内部で import するため必須。
  "pytz>=2026.1",
  "pyyaml>=6.0",
```

- minio / pytz / pyyaml の 3 つが上限なしの `>=` 単独指定で、 破壊的変更を吸い込む
- minio (S3 クライアント) と pyyaml (DUCKDB_COLUMNS のカラム定義 YAML 読み込み) に用途コメントがない (duckdb / pytz にはコメントあり)
- duckdb==1.4.2 は Grafana プラグイン追従の理由コメント付きの完全固定で、 本 issue の対象外
- dependency-groups (lint / test) は本 issue の対象外

## 設計方針

- 対象は dependencies (本番依存) の minio / pytz / pyyaml のみとする
- 「マイナー系列で固定する」は、 規約の例 (`>=0.3,<0.4` のようにメジャー・マイナーで境界を切る) に従い、 下限を現在のメジャー.マイナー (例: 7.2) に、 上限を次のマイナー (例: 7.3) に設定することを指す (例: `minio>=7.2,<7.3`、 `pyyaml>=6.0,<6.1`)
- pytz は日付ベースのバージョン体系 (年.リリース番号) で、 マイナー (リリース番号) 系列での固定 (2026.1 系列 = `>=2026.1,<2026.2`) はロック済みの 2026.2 が範囲外になり、 実質的な完全固定になる。 そのため年単位 (メジャー相当) で境界を切る (`pytz>=2026.1,<2027`。 下限はマイナーまで指定しており規約の「マイナーバージョンまでとすること」を満たす)
- minio と pyyaml に用途コメントを追加する
- マイナー系列固定は、 新しいマイナーバージョンのリリース時に境界を手動で引き上げる運用を前提とする
- 変更後は uv lock で uv.lock を更新し、 uv sync --frozen でのビルドが通る状態にする

## 完了条件

- ingester/pyproject.toml の dependencies の minio / pytz / pyyaml にバージョン境界が設定される (duckdb==1.4.2 は理由コメント付き完全固定のため対象外。 dependency-groups は対象外)
- minio / pyyaml に用途コメントが付与される
- uv sync と全テストが引き続き通過する (uv.lock も更新され、 uv sync --frozen でのビルドが通る。 CI の uv sync は --frozen 未指定のため、 この検証はローカルの uv sync --frozen 実行に依存する)
- CHANGES.md の `## develop` セクションに変更履歴が追記される (種別は [CHANGE])

## 解決方法

- `ingester/pyproject.toml` の本番依存を `minio>=7.2,<7.3`、 `pytz>=2026.1,<2027`、 `pyyaml>=6.0,<6.1` に変更した
- minio に S3 クライアントとしての用途コメント、 pyyaml に DUCKDB_COLUMNS の YAML 読み込み用途コメントを追加した。 pytz は年単位で境界を切る理由コメントを追記した
- `uv lock` で `ingester/uv.lock` を更新し、 `uv sync --frozen` と lint / 単体テストが通過することを確認した
- CHANGES.md はリポジトリ管理外のため、 本ブランチのコミットには含めていない
