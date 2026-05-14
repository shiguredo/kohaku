# Ingester + Grafana サーバーの構築手順

Ubuntu 24.04 上で動作を確認しています

## 概要

このサーバーでは、RustFS または Amazon S3 からログを取得して DuckDB で管理し、Grafana でログを視覚化します

## 前提条件

- RustFS または Amazon S3 へネットワーク経由でアクセスできる

## Grafana のインストール

https://grafana.com/docs/grafana/latest/setup-grafana/installation/ の手順で Grafana をインストールします

Grafana は 12.4.x 系をインストールしてください

下記のコマンドでは、現時点の 12.4.x 系の最新版である 12.4.3 をインストールしています

```bash
sudo apt-get install grafana=12.4.3
```

## kohaku リポジトリをクローン

任意のディレクトリで kohaku を取得します

```bash
git clone https://github.com/shiguredo/kohaku.git kohaku
```

## 環境変数の準備

.env ファイルに、Amazon S3 または RustFS へのアクセスに必要な設定をおこないます

設定項目のテンプレートは .env.common.template に用意してありますので、これを利用して設定します

テンプレート内の設定項目はコメントアウトされています。下記の Ingester + Grafana の設定に必要な項目のコメントアウトを外した上で、環境に合わせて値を設定してください

Grafana の待受ポートは `.env` の `GRAFANA_HTTP_PORT` で設定します

```bash
cd kohaku
cp .env.common.template .env
vim .env
```

Ingester + Grafana の設定に必要な項目は下記のとおりです

### 共通の項目

- `AWS_ACCESS_KEY_ID` - S3 互換ストレージのアクセスキー
- `AWS_SECRET_ACCESS_KEY` - S3 互換ストレージのシークレットキー
- `S3_BUCKET` - バケット名
- `S3_PREFIX` - S3 プレフィックス
- `S3_USE_SSL` - S3 互換ストレージへの接続に SSL を使用するかどうか（Amazon S3 の場合は `true`）
- `DUCKDB_DB_PATH` - DuckDB の DB ファイルのパス
- `RETENTION_PERIOD` - ingester が DuckDB 上で保持するログの期間（日）
- `INITIAL_MAXIMUM_LOAD` - init / update 実行時に読み込む既存ログファイル数の上限
- `GRAFANA_HTTP_PORT` - Grafana の待受ポート番号
- `UV_PYTHON_INSTALL_DIR` - uv が管理する Python のインストール先ディレクトリ

### Amazon S3 以外の S3 互換ストレージを利用する場合

- `S3_ENDPOINT` - S3 互換ストレージのエンドポイント（例: `192.0.2.1:9000`）

### 利用する S3 互換ストレージでリージョン指定が必要な場合（Amazon S3 など）

- `S3_REGION` - S3 互換ストレージのリージョン（例: `ap-northeast-1`）

## Grafana プラグインの準備

Grafana 上で DuckDB を Data Source として使用するための、 [Grafana DuckDB Data Source Plugin](https://github.com/motherduckdb/grafana-duckdb-datasource) を準備します

プラグインの準備には docker コマンドを使用していますので、docker コマンドの実行ユーザを docker グループに追加してから、下記を実行してください

```bash
make build
```

## 準備したプラグインの設置

準備したプラグインを Grafana の指定のディレクトリへコピーします

```bash
sudo cp -r ./plugins /var/lib/grafana/
sudo chown -R grafana:grafana /var/lib/grafana/plugins
```

## 環境変数で Grafana を設定

下記のコマンドを実行して、Grafana を設定します

設定内容は .env の内容に従っておこないます

`GRAFANA_HTTP_PORT` を変更した場合は、その値が Grafana の待受ポートとして設定されます

```bash
sudo make setup-grafana
```

## Kohaku ユーザの作成

systemd で Kohaku を実行するためのユーザを用意します

```bash
sudo useradd -M -s /sbin/nologin kohaku
```

## duckdb の DB ファイル保存等に使用する kohaku ディレクトリの作成

下記のコマンドで、/var/lib/kohaku にディレクトリを作成します

```bash
sudo make setup-kohaku
```

## uv のインストール

ingester を実行する環境を構築します

ingester の実行環境は uv で管理することを想定しているため、uv をインストールします

下記のコマンドは Astral 社が提供する公式インストールスクリプトを sudo で実行します。実行前にスクリプトの内容を確認することを推奨します

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR="/opt/uv/bin" sh
```

## Kohaku 管理用の systemd の unit ファイルの準備

Kohaku 管理用の systemd の unit ファイルを /etc/systemd/system/ 以下にコピーします

本手順以外のユーザやパス等を使用する場合は、適宜 kohaku.service や kohaku.timer を変更して、systemctl で操作できるようにしてください

```bash
sudo cp systemd/kohaku.service /etc/systemd/system/
sudo cp systemd/kohaku.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## Kohaku ディレクトリの設置

ingester を systemd 経由で実行するための環境を /opt/kohaku に設置します

ingester の実行に必要な `ingester` および `scripts` ディレクトリと、これまでの手順で編集した `.env` ファイルのみを配置します

```bash
# 作業用に clone 済みのリポジトリをローカルから clone する
git clone --no-checkout . /tmp/kohaku

# 編集済みの .env をコピーする
cp .env /tmp/kohaku/

pushd /tmp/kohaku
# 必要なディレクトリのみを取り出す sparse-checkout を設定
git sparse-checkout init --no-cone
git sparse-checkout set ingester scripts

# VERSION ファイルに記載されたバージョンに固定する
git checkout `git show origin/develop:VERSION`
popd

sudo mv /tmp/kohaku /opt/kohaku
sudo chown -R kohaku:kohaku /opt/kohaku
```

## テーブル作成および初期データの挿入

DB へのテーブル作成および初期データの挿入は下記の手順でおこないます

Fluent Bit から RustFS または Amazon S3 へログデータが送られてきてから下記を実行します

ログデータの保存状況は mc コマンド等で確認してください

```bash
set -a
source /opt/kohaku/.env
set +a
pushd /opt/kohaku/ingester
sudo -E -u kohaku /opt/uv/bin/uv --cache-dir /opt/kohaku/ingester/.cache sync
sudo -E -u kohaku HOME=/opt/kohaku /bin/sh /opt/kohaku/scripts/run-ingester.sh init
popd
```

## Grafana, Kohaku の起動

systemctl を使用して起動します

```bash
sudo systemctl start grafana-server
sudo systemctl start kohaku.timer
```

サーバー再起動時などに自動起動させる場合は下記を実行します

```bash
sudo systemctl enable grafana-server
sudo systemctl enable kohaku.timer
```

## 注意点

- 初回の起動時には、Fluent Bit によるログの読み込みおよび送信処理から、ingester による DB ファイルの作成が完了するまで、グラフは表示されません
- RustFS または Amazon S3 から読み込んだデータは、duck.db ファイルに保存して、Grafana から読み込むための duck.db.readonly ファイルにコピーしているため、一時的に duck.db.readonly ファイルの読み込みに失敗することがあります
