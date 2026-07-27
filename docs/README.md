# 構築手順

Ubuntu 24.04 上で動作を確認しています。

## 環境

下記の 3 つの構成を想定しています。

各サーバーは役割ごとの構成単位です。複数のサーバーに分けて構築しても、すべてを同一サーバー上に構築してもかまいません。

### Docker を使用しない構成

Fluent Bit、ingester、Grafana を同一サーバー上（あるいは役割ごとに分けたサーバー上）に用意します。

- 主に Amazon S3 を利用する想定です
- Amazon S3 以外にも、独自で構築した S3 互換ストレージや、他の S3 互換サービスを利用できます
- S3 互換ストレージの動作確認用として、Kohaku では RustFS を Docker Compose で構築できるように用意しています

手順:

- [Sora + Fluent Bit サーバーの構築手順](SORA_FLUENT_BIT.md)
- [Ingester + Grafana サーバーの構築手順](INGESTER_GRAFANA.md)
- 動作確認用に RustFS を構築する場合: [RustFS サーバーの構築手順](RUSTFS.md)

### Docker Compose 構成（`compose.yml`）

Fluent Bit、RustFS、ingester、Grafana をすべてコンテナで用意します。

- S3 互換ストレージは Docker Compose で起動する RustFS を利用します
- 動作確認用の構成です。本番運用では Docker を使用しない構成を推奨します

手順:

- [Docker Compose による構築手順](DOCKER.md)

### Docker Compose 構成 + 外部 S3 互換ストレージ（`compose.external-s3.yml`）

Fluent Bit、ingester、Grafana をコンテナで用意し、S3 互換ストレージは別途用意します。

- 主に RustFS 以外の S3 互換ストレージ（Amazon S3、独自に構築した S3 互換ストレージなど）を利用する場合の構成です
- 動作確認用の構成です。本番運用では Docker を使用しない構成を推奨します

手順:

- [Docker Compose による構築手順](DOCKER.md)

## S3 互換ストレージに保存したログデータの保持期間について

Kohaku 本体は、S3 互換ストレージに保存したログデータを削除しません。保持期間を超えたログの削除は、ストレージ側のライフサイクル機能に任せる構成です。

各構成でのライフサイクルルールの扱いは下記のとおりです。

- Docker を使用しない構成で Amazon S3 や独自に構築した S3 互換ストレージなどを利用する場合
  - ライフサイクルルールは自動では登録されません
  - 運用ポリシーに合わせて、利用するストレージ側でライフサイクルルールを設定してください

- 動作確認用の RustFS のみを Docker Compose で利用する場合
  - `mc` コンテナが `.env` の `RETENTION_PERIOD` (日) を保持期間として、起動時にバケットへライフサイクルルールを登録します

- Docker Compose 構成（`compose.yml`）
  - `mc` コンテナが `.env` の `RETENTION_PERIOD` (日) を保持期間として、起動時にバケットへライフサイクルルールを登録します

- Docker Compose 構成 + 外部 S3 互換ストレージ（`compose.external-s3.yml`）
  - `mc` コンテナが、既存のライフサイクルルールを全て削除してから、`.env` の `RETENTION_PERIOD` (日) を保持期間とするルールをバケットへ登録します
  - 既存のライフサイクルルールを保持する必要がある場合は、この構成をそのまま使用しないでください

なお、各ストレージのライフサイクル機能の詳細は下記を参照してください。

- https://docs.rustfs.com/features/lifecycle/
- https://docs.aws.amazon.com/ja_jp/AmazonS3/latest/userguide/object-lifecycle-mgmt.html

### 注意

`RETENTION_PERIOD` は本来、ingester が DuckDB 上で保持するログの期間を制御する設定です。

`mc` コンテナを使用する構成（動作確認用の RustFS のみを Docker Compose で利用する場合、Docker Compose 構成（`compose.yml`）、Docker Compose 構成 + 外部 S3 互換ストレージ（`compose.external-s3.yml`））では、`mc` コンテナが `RETENTION_PERIOD` の値をバケットのライフサイクルルール（保持日数）にも設定します。

Docker を使用しない構成で Amazon S3 や独自に構築した S3 互換ストレージなどを利用する場合は、`RETENTION_PERIOD` はオブジェクトの保持期間には影響しません。オブジェクトの保持期間を制御したい場合は、運用ポリシーに合わせて、利用するストレージ側でライフサイクルルールを設定してください。
