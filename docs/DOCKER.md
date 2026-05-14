# Docker Compose による構築手順

Fluent Bit, RustFS, Grafana を Docker コンテナ上に用意する手順です

Sora の log ディレクトリを Fluent Bit の Docker コンテナ上にマウントして、
Sora のログを Fluent Bit が RustFS の Docker コンテナへ送信します

RustFS の Docker コンテナ上のログは Grafana の Docker コンテナを通して、グラフなどで確認します

## この構成の位置付け

この構成は動作確認用です

本番運用では、Docker 起動時のサーバー設定などの調整が必要になるため、Docker を使用しない構成での運用を推奨します（[Docker を使用しない構成](README.md#docker-を使用しない構成) を参照）

## 環境

下記の 5 点で構築します

- Fluent Bit
  - Sora のログを Storage へ転送

- RustFS（`compose.yml` の場合）
  - Fluent Bit で送信されたログの保存と Kohaku からの問い合わせ

- mc
  - バケット作成や Lifecycle Management 設定などの初期設定

- Kohaku
  - Storage から取得したログを DB で管理

- Grafana
  - ログの視覚化

これらは全て、Docker Compose で構築します

外部 S3 互換ストレージ を利用する `compose.external-s3.yml` の場合、`RustFS` は起動しません

## 設定

.env ファイルで、Sora の log ディレクトリのパスや RustFS の設定をおこないます

設定項目は .env.common.template, .env.docker.template に用意してありますので、これを利用して設定します

テンプレート内の設定項目はコメントアウトされています。Docker Compose の設定に必要な項目のコメントアウトを外した上で、環境に合わせて値を設定してください

Grafana の公開ポートは `.env` の `GRAFANA_HTTP_PORT` で設定します

```bash
cat .env.common.template .env.docker.template > .env
```

### 初期設定

各コンテナにマウントするディレクトリの作成と、Grafana にインストールする [Grafana DuckDB Data Source Plugin](https://github.com/motherduckdb/grafana-duckdb-datasource) の準備をおこないます

```bash
make init
```

fluent-bit.yml を作成します

```bash
DOCKER=true make fluent-bit-yml-for-rustfs
```

### 構築

make up で、Docker Compose が実行され、Fluent Bit, RustFS, mc, Grafana, ingester の Docker コンテナが立ち上がります

```bash
make up
```

`mc` サービスが、`.env` の `RETENTION_PERIOD`（日）を保持期間として、バケットに Lifecycle Management ルールを登録します

### 外部 S3 互換ストレージ を利用する場合

外部 S3 互換ストレージ を利用する場合は `compose.external-s3.yml` を使用します

```bash
make up-external-s3
```

停止は下記です

```bash
make down-external-s3
```

`make down-external-s3` は `compose.external-s3.yml` のサービス（Fluent Bit, mc, Grafana, ingester）の Docker コンテナを削除します

外部 S3 互換ストレージ は Lifecycle Management に対応している必要があります
保持期間は `.env` の `RETENTION_PERIOD`（日）を使用します

### Grafana の設定

- ログイン
  - ブラウザから make up で構築された Grafana (`http://192.0.2.1:${GRAFANA_HTTP_PORT}/`) にアクセスします
    - アクセスするブラウザと Docker ホストが同じ端末上の場合は `http://localhost:${GRAFANA_HTTP_PORT}/` でアクセスできます

  - .env の GF_SECURITY_ADMIN_USER、GF_SECURITY_ADMIN_PASSWORD に設定したアカウントでログインします

    - パスワードは適宜変更してください
  - kohaku ダッシュボードにアクセスします

    - ログが読み込まれて、DB に反映されるまで少し時間がかかります

### 停止

`make down` は `compose.yml` のサービス（Fluent Bit, RustFS, mc, Grafana, ingester）の Docker コンテナを削除します

make down 時には、make up 時に作成した Grafana 用の Docker イメージも削除します

```bash
make down
```

make init 時に作成したディレクトリ等を削除します

```bash
make clean
```

## Docker コンテナ上に作成される DB について

- Docker Compose で作成した volume は、Docker ホスト上のパスをマウントしていません
- `make down` では Docker コンテナとローカルビルドイメージを削除しますが、volume は削除しないため、ログを保存した DB は保持されます
- `make clean` では volume も削除されるため、ログを保存した DB は削除されます
- Docker Compose 停止後も DB を Docker ホスト上の任意パスで保持したい場合には、`compose.yml` で Docker ホスト上のパスをマウントしてください

### 注意点

- Docker Compose で起動した Grafana は `.env` の `GRAFANA_HTTP_PORT` で指定したポート番号、RustFS はポート番号 9000 と 9001 が公開されますので、外部に公開されるサーバ上で起動させる場合には、適宜 Firewall などで、アクセスを制限するようにしてください
