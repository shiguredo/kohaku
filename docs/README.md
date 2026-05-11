# 構築手順

Ubuntu 24.04 上で動作を確認しています

## 環境

下記の組み合わせの 3 サーバーで Kohaku 環境を構築します

各サーバーは役割ごとの構成単位です。3 台のサーバーに分けて構築しても、すべてを同一サーバー上に構築してもかまいません。

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

## Amazon S3, RustFS に保存したログデータについて

Kohaku は、Amazon S3, RustFS に保存したログデータは削除しませんので、
各ストレージのライフサイクルルールを設定するなどして、
定期的に削除することを推奨します

- https://docs.aws.amazon.com/ja_jp/AmazonS3/latest/userguide/object-lifecycle-mgmt.html
- https://docs.rustfs.com/features/lifecycle/
