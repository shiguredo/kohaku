# Kohaku の使い方

## ビルド

```console
$ make
```

## 設定ファイル

config_example.ini をコピーして、接続先の DB の URL（postgres_uri） などを設定してください。

```console
$ cp config_example.ini config.ini
```

## 開発環境での利用

HTTPS 設定無効にすることで HTTP/2 over TCP (h2c) での通信が利用できます。
この場合は証明書の設定は不要です。

### Kohaku 側の設定

```ini
https = false
```

### Sora 側の設定

HTTP にすることで HTTP/2 over TCP (h2c) を利用して接続しに行きます。

```ini
stats_collector_url = http://192.0.2.10:5890/collector
```

## 起動

上記で用意した `config.ini` を指定して kohaku サーバを立ち上げます。

```console
$ ./bin/kohaku -C config.ini
```

## TimescaleDB のスキーマ

db ディレクトリ以下に `schema.sql` がありますのでこちらをご利用ください。
