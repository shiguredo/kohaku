# Kohaku の使い方

## ビルド

```
$ make
```

## 設定ファイル

config_example.ini をコピーして、接続先の DB の URL（postgres_uri） や各証明書等を設定します。

```
$ cp config_example.ini config.ini
```

### 開発環境での利用

HTTPS 設定無効にすることで HTTP/2 over TCP (h2c) での通信が利用できます。
この場合は証明書の設定は不要です。

```ini
https = false
```

## 起動

上記で用意した config.ini を指定して kohaku サーバを立ち上げます。

```
$ ./bin/kohaku -c config.ini
```

## TimescaleDB のスキーマ

db ディレクトリ以下に `schema.sql` がありますのでこちらをご利用ください。
