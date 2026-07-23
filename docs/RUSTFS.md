# RustFS サーバーの構築手順

Ubuntu 24.04 上で動作を確認しています。

## 概要

S3 互換ストレージの動作確認用として、Docker Compose で RustFS を起動し、Fluent Bit から送信されてきたログを保存する構成です。

本番運用では、Amazon S3 をはじめとする他の S3 互換ストレージやサービスの利用を推奨します。

## 前提条件

- Docker、Docker Compose がインストールされている
- Fluent Bit サーバーおよび Ingester + Grafana サーバーからネットワーク経由でアクセスできる

## kohaku リポジトリをクローン

任意のディレクトリで kohaku を取得します。

```bash
git clone https://github.com/shiguredo/kohaku.git kohaku
```

## 環境変数の準備

`.env` ファイルに、RustFS の設定を行います。

設定項目のテンプレートは `.env.common.template`、`.env.docker.template` に用意してありますので、これらを利用して設定します。

テンプレート内の設定項目はコメントアウトされています。下記の RustFS の設定に必要な項目のコメントアウトを外したうえで、環境に合わせて値を設定してください。

```bash
cd kohaku
cat .env.common.template .env.docker.template > .env
vim .env
```

RustFS の設定に必要な項目は下記のとおりです。

- `AWS_ACCESS_KEY_ID` - RustFS のアクセスキー
- `AWS_SECRET_ACCESS_KEY` - RustFS のシークレットキー
- `S3_ENDPOINT` - このサーバーのアドレスとポート番号（例: `192.0.2.1:9000`）
- `S3_USE_SSL` - SSL を使用するかどうか（通常は `false`）
- `S3_BUCKET` - バケット名
- `S3_PREFIX` - S3 プレフィックス
- `RETENTION_PERIOD` - ログの保持期間（日）
- `RUSTFS_BASE_DIR` - RustFS のデータ保存ディレクトリ（通常は `./rustfs`）

## データ保存ディレクトリの作成

`.env` で設定した `RUSTFS_BASE_DIR` 以下に `data` と `logs` のディレクトリを作成します。

```bash
set -a && source .env && set +a
mkdir -p "${RUSTFS_BASE_DIR}/data" "${RUSTFS_BASE_DIR}/logs"
```

## RustFS の起動

Docker Compose で RustFS、mc（初期設定用）を起動します。

RustFS のコンテナはホスト上の `./rustfs` ディレクトリに書き込みます。ホストとコンテナで権限を合わせるため、実行ユーザーの UID / GID を `USER_ID` と `GROUP_ID` で渡しています。

```bash
USER_ID=$(id -u) GROUP_ID=$(id -g) docker compose up -d rustfs mc
```

RustFS の起動後、mc コンテナが自動でバケット作成や Lifecycle Management ルールの登録などの初期設定を行います。

mc コンテナは初期設定完了後に終了します。

保持期間を超えたオブジェクトは、RustFS の Lifecycle Management により `.env` の `RETENTION_PERIOD` (日) 後に削除されます。

## ファイアウォールの設定

Fluent Bit サーバーおよび Ingester + Grafana サーバーから、ポート 9000 へのアクセスを許可します。

管理画面（ポート 9001）では、認証後にバケットを自由に操作できるため、外部からアクセスできないように制限することを推奨します。
