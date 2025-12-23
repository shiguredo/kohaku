# 構築手順

Ubuntu 24.04 上で動作を確認しています

## 環境

下記の 4 点で Kohaku 環境を構築します

- Fluent Bit
  - Sora のログの RustFS, Amazon S3 への転送

- RustFS または Amazon S3
  - Fluent Bit で送信されてきたログの保存と Kohaku からの問い合わせ

- Kohaku
  - RustFS または Amazon S3 から取得したログを DB で管理

- Grafana
  - ログの視覚化

Fluent Bit は Sora のログを読み込めるサーバ上に構築し、Kohaku は Grafana は同一のサーバ上に構築します

RustFS または Amazon S3 は、構築した Fluent Bit、Kohaku からアクセスできるようにします

## Grafana の設定

Grafana のインストール後にプラグインをインストールして使用する場合の設定手順です

### Grafana のインストール

https://grafana.com/docs/grafana/latest/setup-grafana/installation/ の手順で Grafana をインストールします

### kohaku リポジトリをクローン

任意のディレクトリで kohaku を取得します

```bash
git clone https://github.com/shiguredo/kohaku.git kohaku
```

### 環境変数の準備

.env ファイルに、Sora の log ディレクトリのパスや Amazon S3, RustFS へのアクセスに必要な設定をおこないます

設定項目のテンプレートは .env.template に用意してありますので、これを利用して設定します

```bash
cd kohaku
cp .env.template .env
vim .env
...
```

### Grafana プラグインの準備

Grafana 上で DuckDB を Data Source として使用するための、 [Grafana DuckDB Data Source Plugin](https://github.com/motherduckdb/grafana-duckdb-datasource) を準備します

プラグインの準備には docker コマンドを使用していますので、docker コマンドの実行ユーザを docker グループに追加してから、下記を実行してください

```bash
make build
```

### 準備したプラグインの設置

準備したプラグインを Grafana の指定のディレクトリへコピーします

```bash
sudo cp -r ./plugins /var/lib/grafana/
sudo chown -R grafana:grafana /var/lib/grafana/plugins
```

### 環境変数で Grafana を設定

下記のコマンドでを実行して、 Grafana を設定します

設定内容は .env の内容に従っておこないます

```bash
sudo make setup-grafana
```

### Fluent Bit の準備

https://docs.fluentbit.io/manual/installation/getting-started-with-fluent-bit の手順で Fluent Bit をインストールします

下記のいずれかのコマンドで Fluent Bit の設定をおこないます

- Amazon S3 の場合

```bash
sudo make setup-fluent-bit
```

- RustFS の場合

```bash
sudo make setup-fluent-bit-for-rustfs
```

これらは、Kohaku 設定時の fluent-bit.yml の設定および、systemd の設定を合わせておこないますので、
他の用途で Fluent Bit を使用する場合は、下記のコマンドで作成される fluent-bit.yml を参考にして組み込むようにしてください

- Amazon S3 の場合

```bash
make fluent-bit-yml
```

- RustFS の場合

```bash
make fluent-bit-yml-for-rustfs
```

### Kohaku ユーザの作成

systemd で Kohaku を実行するためのユーザを用意します

```bash
sudo useradd -M -s /sbin/nologin kohaku
```

### duckdb の DB ファイル保存等に使用する kohaku ディレクトリの作成

下記のコマンドで、/var/lib/kohaku にディレクトリを作成します

```bash
sudo make setup-kohaku
```

### uv のインストール

ingester を実行する環境を構築します

ingester の実行環境は uv で管理することを想定しているため、uv をインストールします

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR="/opt/uv/bin" sh
```

### Kohaku 管理用の systemd の unit ファイルの準備

Kohaku 管理用の systemd の unit ファイルを /etc/systemd/system/ 以下にコピーします

本手順以外のユーザやパス等を使用する場合は、適宜 kohaku.service や kohaku.timer を変更して、systemctl で操作できるようにしてください

```bash
sudo cp systemd/kohaku.service /etc/systemd/system/
sudo cp systemd/kohaku.timer /etc/systemd/system/
```

### Kohaku ディレクトリの設置

構築が終わりましたら、/opt/kohaku に Kohaku の実行環境を設置します

```bash
git clone --no-checkout . /tmp/kohaku
cp .env /tmp/kohaku/
pushd /tmp/kohaku
git sparse-checkout init --no-cone
git sparse-checkout set ingester
git checkout develop
popd
sudo mv /tmp/kohaku /opt/kohaku
sudo chown -R kohaku:kohaku /opt/kohaku
```

### Fluent Bit の起動

Fluent Bit を起動します

```bash
sudo systemctl start fluent-bit
```

### テーブル作成および初期データの挿入

DB へのテーブル作成および初期データの挿入は下記の手順でおこないます

Fluent Bit から Amazon S3 または RustFS へログデータが送られてきてから下記を実行します

ログデータの保存状況は mc コマンド等で確認してください

```bash
set -a
source /opt/kohaku/.env
pushd /opt/kohaku/ingester
sudo -E -u kohaku /opt/uv/bin/uv --cache-dir /opt/kohaku/ingester/.cache sync
sudo -E -u kohaku HOME=/opt/kohaku/ingester /opt/uv/bin/uv run python src/run.py \
  --db $DUCKDB_DB_PATH \
  --storage $STORAGE \
  --s3_endpoint $S3_ENDPOINT \
  --s3_access_key_id $AWS_ACCESS_KEY_ID \
  --s3_secret_access_key $AWS_SECRET_ACCESS_KEY \
  --s3_bucket $S3_BUCKET \
  --s3_prefix $S3_PREFIX \
  init
popd
set +a
```

### Grafana, Kohaku の起動

systemctl を使用して起動します

```bash
sudo systemctl start grafana-server
sudo systemctl start kohaku.timer
```

サーバー再起動時などに自動起動させる場合は下記を実行します

```bash
sudo systemctl enable kohaku.timer
```

## Amazon S3, RustFS に保存したログデータについて

Amazon S3, RustFS に保存したログデータは削除しませんので、
各ストレージのライフサイクルルールを設定するなどして、
定期的に削除することを推奨します

- https://docs.aws.amazon.com/ja_jp/AmazonS3/latest/userguide/object-lifecycle-mgmt.html
- https://docs.rustfs.com/features/lifecycle/

## 注意点

- 初回の起動時には、Fluent Bit によるログの読み込みおよび送信処理から、ingester による DB ファイルの作成が完了するまで、グラフは表示されません
- Amazon S3 または、RustFS から読み込んだデータは、duck.db ファイルに保存して、Grafana から読み込むための duck.db.readonly ファイルにコピーしているため、一時的に duck.db.readonly ファイルの読み込みに失敗することがあります
