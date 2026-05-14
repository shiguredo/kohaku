# 構築手順

Ubuntu 24.04 上で動作を確認しています

## 環境

下記の組み合わせの 3 サーバーで Kohaku 環境を構築します

各サーバーは役割ごとの構成単位です。3 台のサーバーに分けて構築しても、すべてを同一サーバー上に構築してもかまいません

- [Sora + Fluent Bit] サーバー
  - Sora が動作するサーバー上で Fluent Bit を動かし、ログを RustFS または Amazon S3 へ転送します

- [RustFS] サーバー（Amazon S3 を使用する場合は不要）
  - Fluent Bit から送信されてきたログを保存します
  - Fluent Bit サーバーおよび Ingester + Grafana サーバーからアクセスできる必要があります

- [Ingester + Grafana] サーバー
  - RustFS または Amazon S3 からログを取得して DuckDB で管理します
  - Grafana でログを視覚化します

各サーバーの構築手順は下記を参照してください

- [Sora + Fluent Bit サーバーの構築手順](SORA-FLUENT-BIT.md)
- [RustFS サーバーの構築手順](RUSTFS.md)
- [Ingester + Grafana サーバーの構築手順](INGESTER-GRAFANA.md)

また、Docker を利用して Kohaku 環境を構築する場合は、下記を参照してください

- [Docker Compose による構築手順](DOCKER.md)

## Amazon S3, RustFS に保存したログデータの保持期間について

Kohaku 本体は Amazon S3, RustFS に保存したログデータを削除しません。
保持期間を超えたログの削除は、ストレージ側のライフサイクル機能に任せる構成です

- Docker Compose で構築した場合（`compose.yml`, `compose.external-s3.yml`）
  - `mc` コンテナが `.env` の `RETENTION_PERIOD`（日）を保持期間として、起動時にバケットへライフサイクルルールを登録します

- RustFS サーバーを単独で構築した場合（[RustFS サーバーの構築手順](RUSTFS.md)）
  - 上記同様、`mc` コンテナがライフサイクルルールを登録します

- Amazon S3 を直接利用する場合
  - ライフサイクルルールは自動では登録されません
  - 運用ポリシーに合わせて Amazon S3 側でライフサイクルルールを設定してください

なお、各ストレージのライフサイクル機能の詳細は下記を参照してください

- https://docs.rustfs.com/features/lifecycle/
- https://docs.aws.amazon.com/ja_jp/AmazonS3/latest/userguide/object-lifecycle-mgmt.html

### 注意

`RETENTION_PERIOD` は本来、ingester が DuckDB 上で保持するログの期間を制御する設定です

Docker Compose で構築した場合および RustFS サーバーを単独で構築した場合に限り、`mc` コンテナが `RETENTION_PERIOD` の値を RustFS のオブジェクトのライフサイクルルール（保持日数）にも設定します

Amazon S3 を直接利用する場合は、`RETENTION_PERIOD` はオブジェクトの保持期間には影響しません。オブジェクトの保持期間を制御したい場合は、運用ポリシーに合わせて Amazon S3 側でライフサイクルルールを設定してください
