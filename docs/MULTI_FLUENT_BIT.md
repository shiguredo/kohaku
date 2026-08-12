# 複数 Fluent Bit ノードの運用ガイド

本ガイドは、複数の Fluent Bit ノードから同一の S3 バケットへログを put する構成を運用する際の注意点とパラメータ調整をまとめたものです。

Fluent Bit ノードを [Sora + Fluent Bit サーバーの構築手順](SORA_FLUENT_BIT.md)、ingester を [Ingester + Grafana サーバーの構築手順](INGESTER_GRAFANA.md) の手順で構築済みであることを前提とします。

## 概要

この構成では、複数の Fluent Bit ノードがそれぞれ Sora のログを収集し、同一の S3 バケットの同一 prefix 配下へ put します。ingester は 1 台で、S3 バケット内のオブジェクトをまとめて取り込みます。

```text
Sora + Fluent Bit ─┐
Sora + Fluent Bit ─┼─> S3 バケット ─> ingester ─> DuckDB
Sora + Fluent Bit ─┘
```

- 各 Fluent Bit ノードは `SORA_LOG_PATH` に指定したディレクトリ配下の `rtc_stats.jsonl` と `session_webhook.jsonl` を収集します
- ノードごとにログの取り込み範囲が分担されるのではなく、全ノードが同一の S3 バケットへ put し、ingester が重複を吸収しながら取り込みます

## 設定管理

### fluent-bit.yml の生成元を全ノードで一致させる

`fluent-bit.yml` は `make fluent-bit-yml`（Amazon S3 向け）または `make fluent-bit-yml-for-rustfs`（RustFS 向け）で、`.env` の設定値と `fluent-bit/fluent-bit.yml.s3` または `fluent-bit/fluent-bit.yml.rustfs` から生成されます。

複数ノードで運用する場合は、この生成元（`.env` の設定値と Fluent Bit のテンプレート）を全ノードで一致させてください。設定を同期する方法は Ansible や Chef などの構成管理ツール、または手動同期のいずれでもかまいません。

`fluent-bit.yml` に埋め込まれる項目のうち、ノード個別に値が異なってもよいのは `SORA_LOG_PATH`（各ノードのログディレクトリ）のみです。それ以外の項目（`S3_BUCKET`、`S3_PREFIX`、`S3_REGION`、`S3_ENDPOINT` など）は全ノードで同じ値を設定してください。

なお、認証情報（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`）は `fluent-bit.yml` には含まれず、systemd 構成では `/etc/fluent-bit/kohaku.env`、コンテナ構成では compose ファイルの環境変数で Fluent Bit に渡されます。そのため、これらの認証情報はノード単位で別々の値を設定してもかまいません。いずれの場合でも、バケットへのアクセス権限は各ノードに必要です。

### 設定不一致による影響

Fluent Bit の設定がノード間で一致していない場合、下記の問題が発生します。

- `compression` の不一致
  - 一部のノードだけ compression が無効だと、そのノードが put したオブジェクトを ingester が gzip として復号できず、取り込みエラーになります
  - 取り込みエラーが発生すると該当 target（ログの種類。`rtc_stats` / `session_webhook`）の取り込みが停止し、壊れたオブジェクトを S3 から除去するまで、update のたびに失敗が繰り返されます
- `parser` の不一致
  - 行をパースできないと Fluent Bit 側で行が落ちて取り漏れになります
- `s3_key_format` の不一致
  - ingester は `S3_PREFIX` 配下を list して取り込むため、`s3_key_format` の不一致でリスト対象の prefix の外にオブジェクトが put されると取り漏れになります

### リトライと重複の扱い

Fluent Bit の出力プラグインは、S3 への put に失敗すると `retry_limit` の既定回数だけリトライします。リトライのタイミングによっては、同一のログが別の `$UUID` のオブジェクトとして重複 put されることがあります。

重複して put されたオブジェクトは、ingester 側のテーブルの natural key による PK 制約と `INSERT ... ON CONFLICT DO NOTHING` で吸収されるため、DuckDB に重複行は挿入されません。

`retry_limit` などの Fluent Bit 側の出力プラグイン設定を調整する場合は、[Fluent Bit の S3 output ドキュメント](https://docs.fluentbit.io/manual/pipeline/outputs/s3) を参照してください。

### ホスト名の一意性

`fluent-bit.yml` の `s3_key_format` には `${HOSTNAME}` が含まれており、put されたオブジェクトのキーに Fluent Bit が動作するノードのホスト名が記録されます（[S3 オブジェクトキーの形式](SORA_FLUENT_BIT.md#s3-オブジェクトキーの形式) を参照）。

systemd 構成では 1 ホストに 1 Fluent Bit を配置し、ホスト名を一意にしてください。ホスト名が重複すると、S3 オブジェクトキーからのノードの追跡ができなくなります。コンテナ構成ではホスト名がコンテナ ID になるため、この制約はありません。

## tail DB の永続化

Fluent Bit の tail input は、読み取り済みのファイルの inode とオフセットを `db` で指定した SQLite DB に保存します。

- `rtc_stats.jsonl` 用: `/var/lib/fluent-bit/rtc_stats.db`
- `session_webhook.jsonl` 用: `/var/lib/fluent-bit/session_webhook.db`

この DB が無い状態で Fluent Bit を再起動すると読み取り位置が失われるため、永続化が必須です。

### systemd 構成の場合

`make setup-fluent-bit` または `make setup-fluent-bit-for-rustfs` が `/var/lib/fluent-bit` ディレクトリを作成します。このディレクトリを削除すると読み取り位置が失われます。

### Docker Compose 構成（コンテナ構成）の場合

Docker Compose 構成では、`fluent-bit-state` volume が `/var/lib/fluent-bit` にマウントされます。

`docker compose down` では volume は削除されないため、オフセットは保持されます。`docker compose down -v` や `make clean` を実行した場合は volume が削除され、読み取り位置が失われます。

### tail DB が破損または消失したときのリスク

tail DB が破損または消失した場合、Fluent Bit は読み取り位置を失います。`read_from_head: false` のため、DB 消失後は対象ファイルの末尾からの読み取りを開始し、停止期間中に出力されたログは取り込まれません。この取り漏れは再送されません。

また、Fluent Bit がクラッシュした場合などにオフセットが巻き戻ると、同じログが at-least-once で再送されます。再送されたログは ingester 側の PK 制約で重複吸収されるため、DuckDB に重複行は挿入されません。

tail DB が破損または消失した場合は、DB ファイルを削除して Fluent Bit を再起動してください。以降に出力されるログから取り込みが再開されます。

## パラメータ調整

### upload_timeout と更新間隔の関係

Fluent Bit の `upload_timeout` は、バッファしたログを S3 へ put するまでの最大時間です。`fluent-bit.yml` では 5 分に設定されています。ログが Sora で出力されてから ingester で取り込まれるまでの遅延の上界は、次のとおりです。

```text
upload_timeout + ingester の更新間隔
```

ingester の更新間隔は、構成によって次のとおりです。

- systemd 構成: `systemd/kohaku.timer` の 5 分間隔
- コンテナ構成: `.env` の `UPDATE_INTERVAL`（秒）

### initial_maximum_load

`initial_maximum_load` は、init 時、および update 時にテーブル未作成だった場合の初回テーブル作成における読み込みファイル数（S3 のオブジェクト）の上限です。

デフォルト値は 1000 です。

初期取り込みで切り捨てられたオブジェクトは、ingester がカーソルを最新のオブジェクトまで進めるため、init 後の update でも取り込まれません。

ノード数を増やした場合は、実ログレートとカバーしたい時間に応じて値を見直してください。

### update_maximum_load

`update_maximum_load` は、update 時に 1 回で取り込むファイル数の上限です。長時間停止後の復帰時に大量蓄積したログをバッチ分割するために使います。

この上限は target（rtc_stats / session_webhook）ごとに適用され、Fluent Bit の台数に依存しないため、デフォルト値の 100 のままで問題ありません。

### パラメータの上書き方法

`initial_maximum_load` と `update_maximum_load` をデフォルト値から変更する場合の上書き方法は、構成によって次のとおりです。

- systemd 構成
  - `systemd/kohaku.service` の `Environment=` と `/opt/kohaku/.env` の両方で設定できます
  - `EnvironmentFile`（`.env`）は `Environment=` より後で評価されるため、両方に設定した場合は `.env` 側の値が優先されます
- コンテナ構成
  - `.env` で設定します

### 時刻ベースの初期取り込みオプション

件数ベースの `initial_maximum_load` では、初期取り込みのカバー時間が Fluent Bit の台数に依存して変わります。時刻ベースで「過去 N 時間分を取り込む」ことを指定できるオプションは対応検討中です。

## 障害切り分け

どの Fluent Bit ノードが停止しているかを特定するには、S3 バケット内のオブジェクトキーを確認します。`s3_key_format` にホスト名が含まれているため、オブジェクトキーのホスト名でノード単位の put 状況を追跡できます。

- systemd 構成では、ホスト名は各ノードの OS のホスト名になるため、停止ノードを特定できます
- コンテナ構成では、ホスト名はコンテナ ID になるため、コンテナを再作成すると変化します。長期の追跡には向きません

あるホスト名のオブジェクトが現れない場合は、そのノードの Fluent Bit が停止している可能性があります。Sora が動作していれば、ログ量が少ないノードでも `upload_timeout`（5 分）以内にオブジェクトが生成されるため、その数倍以上新しいオブジェクトが現れない場合は停止を疑ってください。S3 オブジェクトの確認には、`mc` コマンドなどでオブジェクトキーを絞り込んでください。
