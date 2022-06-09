## Kohaku の使い方

### ビルド

```
$ make
```


### 設定

config.example.yaml をコピーして、接続先の DB の URL（timescale_url） や各証明書等を設定します。

```
$ cp config.example.yaml config.yaml
```


### 起動

上記で用意した config.yaml を指定して kohaku サーバを立ち上げます。

```
$ ./bin/kohaku -c config.yaml
```

## TimescaleDB のスキーマ

db ディレクトリ以下に `schema.sql` がありますのでこちらをご利用ください。

## mTLS で使用するクライアント証明書の作成と設定

mTLS で使用するクライアント証明書と、クライアント証明書を発行する CA 証明書の作成例を記載します。

### CA 証明書の作成

まずは、クライアント証明書を発行するために、CA 証明書を作成します。

#### 秘密鍵の作成

```
$ openssl ecparam -out ca.key -name prime256v1 -genkey
```

#### 証明書の作成

days や subj は適宜変更してください。

```
$ openssl req -new \
              -x509 \
              -sha256 \
              -days 365 \
              -subj "/C=JP/ST=Tokyo/O=Shiguredo Inc./CN=WebRTC CA" \
              -key ca.key \
              -out ca.pem
```


### ディレクトリ及びファイルの作成

クライアント証明書発行時に使用するディレクトリとファイルを作成します。

```
$ mkdir -p demoCA/newcerts
$ touch demoCA/index.txt
$ echo 00 > demoCA/serial
```

### クライアント証明書の発行

次の手順でクライアント証明書を発行します。

#### 秘密鍵の作成

```
$ openssl ecparam -out client.key -name prime256v1 -genkey
```


#### CSR 及び証明書の作成

subj は適宜変更してください。

```
$ openssl req -new \
              -sha256 \
              -key client.key \
              -outform PEM \
              -keyform PEM \
              -out req.pem \
              -subj "/C=JP/ST=Tokyo/O=Shiguredo Inc./CN=kohaku user1"
```

- openssl.cnf の設定

openssl.cnf を /etc/ssl/openssl.cnf 等からコピーします。

```
$ cp /etc/ssl/openssl.cnf .
```

openssl.cnf の [ usr_cert ] セクションの extendedKeyUsage のコメントアウトを外して、下記の値を設定します。

```
extendedKeyUsage = clientAuth
```

#### クライアント証明書の作成

下記のコマンドでクライアント証明書を発行します。

```
$ openssl ca -config openssl.cnf \
             -in req.pem \
             -keyfile ca.key \
             -cert ca.pem \
             -extensions usr_cert \
             -out client.pem
```


### kohaku 側への設定

kohaku の設定ファイルで、 h2 を有効化と、mTLS で使用するクライアント認証用の CA 証明書ファイルを指定します。

- http2_h2c

    - false に設定して h2 を有効にします

- http2_verify_cacert_path

    - 上記で作成したクライアント証明書を発行する際に使用した CA 証明書（ca.pem）へのパスを指定します

### kohaku クライアントへの設定

kohaku に接続するクライアント（Sora）には、上記で作成したクライアント証明書（client.pem）とその秘密鍵（client.key）のファイルをクライアント認証時に使用するように設定します。
