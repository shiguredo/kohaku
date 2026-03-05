# 構築手順

## docker compose による構築手順

Fluent Bit, RustFS, Grafana を Docker コンテナ上に用意する手順です

Sora の log ディレクトリを Fluent Bit の Docker コンテナ上にマウントして、
Sora のログを Fluent Bit が RustFS の Docker コンテナへ送信します

RustFS の Docker コンテナ上のログは Grafana の Docker コンテナを通して、グラフなどで確認します

## 環境

下記の 4 点で構築します

- Fluent Bit
  - Sora のログの RustFS への転送

- RustFS
  - Fluent Bit で送信されてきたログの保存と Kohaku からの問い合わせ

- Kohaku
  - RustFS から取得したログを DB で管理

- Grafana
  - ログの視覚化

これらは全て、Docker Compose で構築します

## 設定

.env ファイルで、Sora の log ディレクトリのパスや RustFS の設定をおこないます

設定項目は .env.common.template, .env.docker.template に用意してありますので、これを利用して設定します

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

make up で、docker compose が実行され、Fluent Bit, RustFS, Grafana の Docker コンテナが立ち上がります

```bash
make up
```

### Grafana の設定

- ログイン
  - ブラウザから make up で構築された Grafana (http://192.0.2.0:3000/) にアクセスします
    - アクセスするブラウザと docker ホストが同じ端末上の場合は http://localhost:3000/ でアクセスできます

  - .env の GF_SECURITY_ADMIN_USER、GF_SECURITY_ADMIN_PASSWORD に設定したアカウントでログインします

    - パスワードは適宜変更してください
  - kohaku ダッシュボードにアクセスします

    - ログが読み込まれて、DB に反映されるまで少し時間がかかります

### 停止

Fluent Bit, RustFS, Grafana の Docker コンテナを削除します

make down 時には、make up 時に作成した Grafana 用の Docker イメージも削除します

```bash
make down
```

make init 時に作成したディレクトリ等を削除します

```bash
make clean
```

## Docker コンテナ上に作成される DB について

- Docker Compose で作成した volume は、Docker ホスト上のパスをマウントしていないため、Docker Compose を停止すると、ログを保存した DB は削除されます
  - Docker Compose 停止後も DB を保持したい場合には、compose.yml で Docker ホスト上のパスをマウントしてください

### 注意点

- Docker Compose で起動した Grafana はポート番号 3000, RustFS はポート番号 9000 と 9001 が公開されますので、外部に公開されるサーバ上で起動させる場合には、適宜 Firewall などで、アクセスを制限するようにしてください
