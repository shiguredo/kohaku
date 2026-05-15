# Sora + Fluent Bit サーバーの構築手順

Ubuntu 24.04 上で動作を確認しています

## 概要

このサーバーでは、Sora のログを Fluent Bit を使って RustFS または Amazon S3 へ転送します

## 前提条件

- Sora が動作しており、ログが出力されている
- S3 互換ストレージへネットワーク経由でアクセスできる

## Fluent Bit のインストール

https://docs.fluentbit.io/manual/installation/getting-started-with-fluent-bit の手順で Fluent Bit の最新の安定版をインストールします

## kohaku リポジトリをクローン

任意のディレクトリで kohaku を取得します

```bash
git clone https://github.com/shiguredo/kohaku.git kohaku
```

## 環境変数の準備

.env ファイルに、Sora のログディレクトリのパスや RustFS または Amazon S3 へのアクセスに必要な設定をおこないます

設定項目のテンプレートは .env.common.template に用意してありますので、これを利用して設定します

テンプレート内の設定項目はコメントアウトされています。下記の Fluent Bit の設定に必要な項目のコメントアウトを外した上で、環境に合わせて値を設定してください

```bash
cd kohaku
cp .env.common.template .env
vim .env
```

Fluent Bit の設定に必要な項目は下記のとおりです

### 共通の項目

- `SORA_LOG_PATH` - Sora のログディレクトリのパス
- `AWS_ACCESS_KEY_ID` - S3 互換ストレージのアクセスキー
- `AWS_SECRET_ACCESS_KEY` - S3 互換ストレージのシークレットキー
- `S3_BUCKET` - バケット名
- `S3_PREFIX` - S3 プレフィックス

### Amazon S3 以外の S3 互換ストレージを利用する場合

- `S3_ENDPOINT` - S3 互換ストレージのエンドポイント（例: `192.0.2.1:9000`）

### 利用する S3 互換ストレージでリージョン指定が必要な場合（Amazon S3 など）

- `S3_REGION` - S3 互換ストレージのリージョン（例: `ap-northeast-1`）

## Fluent Bit の設定

下記のいずれかのコマンドで fluent-bit.yml を生成して、systemd の設定をおこないます

これらのコマンドは、Fluent Bit の systemd 起動時に使用する認証情報を `/etc/fluent-bit/kohaku.env` に保存します

- Amazon S3 の場合

```bash
sudo make setup-fluent-bit
```

- RustFS の場合

```bash
sudo make setup-fluent-bit-for-rustfs
```

### 既に Fluent Bit を利用している場合

他の用途で Fluent Bit を利用している場合は、下記のコマンドで生成される fluent-bit.yml を参考にして、適宜既存の設定に追加または変更してください

- Amazon S3 の場合

```bash
make fluent-bit-yml
```

- RustFS の場合

```bash
make fluent-bit-yml-for-rustfs
```

## Fluent Bit による収集対象のログファイル

Kohaku は、.env ファイルの `SORA_LOG_PATH` に指定したディレクトリ以下のログファイルを対象にログを収集します

- rtc_stats.jsonl
- session_webhook.jsonl

ログ収集時の Fluent Bit の設定は、上記の make で生成された fluent-bit.yml でご確認ください

## Fluent Bit の起動

Fluent Bit を起動します

```bash
sudo systemctl start fluent-bit
```

サーバー再起動時などに自動起動させる場合は下記を実行します

```bash
sudo systemctl enable fluent-bit
```
