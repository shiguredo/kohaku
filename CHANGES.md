# 変更履歴

- CHANGE
  - 下位互換のない変更
- UPDATE
  - 下位互換がある変更
- ADD
  - 下位互換がある追加
- FIX
  - バグ修正

## develop

- [ADD] rtc-stats ダッシュボードに data-channel のグラフを追加する
  - @tnamao
- [FIX] `kohaku` ダッシュボードの from - to のタイムスタンプのミリ秒の扱いを変更する
  - ミリ秒以下を from は切り捨て、to は切り上げする
  - @tnamao

## 2023.1.1

- [FIX] HTTP/2 Rapid Reset 対策として Go 1.21.3 以上でリリースバイナリを作成するよう修正する
  - https://groups.google.com/g/golang-announce/c/iNNxDTCjZvo
  - @voluntas

## 2023.1.0

- [ADD] Grafana ダッシュボードの追加
  - @tnamao
- [ADD] バージョンを -V と --version で表示できるようにする
  - バージョンファイル VERSION を利用するようにする
  - @voluntas
- [CHANGE] 設定ファイル読み込みの引数を -C と --config に変更する
  - @voluntas
- [CHANGE] デフォルト設定ファイル名を `config.ini` にする
  - @voluntas
- [CHANGE] /.ok を POST から GET に変更する
  - @voluntas
- [CHANGE] h2c を利用可能にする設定 `https` を追加する
  - @voluntas
- [UPDATE] go.mod の go のバージョンを 1.21 にする
  - @voluntas
- [UPDATE] sqlc 1.20.0 にする
  - @voluntas
- [UPDATE] lumberjack を公式に戻す
  - @voluntas
- [CHANGE] sqlc の emit_pointers_for_null_types を true にする
  - @voluntas
- [CHANGE] RTCStats のデータはすでにレコードがある場合 timestamp 以外が変更されていない限り追加しない
  - @voluntas
- [CHANGE] TimescaleDB の設定項目を変更する
  - timescale_rootcert_file は postgres_ca_cert_file へ
  - timescale_url は postgres_uri へ
  - timescale_sslmode は廃止し postgres_uri で指定可能に
    - `kohaku?sslmode=require` のように指定
  - @voluntas
- [CHANGE] schema の全面書き換えし JSONB 形式で保存するように変更
  - @voluntas
- [CHANGE] 設定 `http2_fullchain_file` を `tls_fullchain_file` に変更
  - @voluntas
- [CHANGE] 設定 `http2_privkey_file` を `tls_privkey_file` に変更
  - @voluntas
- [CHANGE] 設定 `http2_verify_cacert_path` を `tls_verify_cacert_path` に変更
  - @voluntas
- [CHANGE] デフォルト設定ファイル名を config.ini に変更する
  - @voluntas
- [CHANGE] OpenMetrics 用の Exporter を追加する
  - exporter_https
    - 証明書は `tls\_\*` を利用します
  - exporter_listen_addr
  - exporter_listen_port
  - @voluntas
- [CHANGE] 設定ファイル形式を YAML から INI に変更する
  - @voluntas
- [CHANGE] デバッグが有効な場合は stdout に出すログは可読性の高いフォーマットにする
  - @voluntas
- [CHANGE] ログ出力を JSON 形式に変更する
  - @voluntas
- [CHANGE] 設定例のログ出力ファイル名の拡張子を `jsonl` にする
  - @voluntas
- [ADD] ログローテーション用の設定を追加
  - log_rotate_max_size
    - メガバイト
  - log_rotate_max_backups
  - log_rotate_max_age
    - 日
- [ADD] ライブリロード用に Air を追加
  - @voluntas
- [ADD] TimescaleDB と Grafana 検証用の compose.yaml を追加
  - grafana は 3333 ポート待ち受け
  - @voluntas
- [UPDATE] go.mod, Github Actions で使用する Go のバージョンを go.mod を見るようにする
  - @Hexa
- [UPDATE] Github Actions で使用する staticcheck のバージョンを 2023.1.2 にあげる
  - @Hexa

## 2022.1.0

- [CHANGE] kohaku の設定ファイルのパス指定のデフォルトを ../config.yaml から ./config.yaml に変更する
  - @Hexa
- [CHANGE] query.sql と schema.sql を db/ 以下へ移動
  - @voluntas
- [CHANGE] sqlc のコード生成を gen/sqlc 以下へ移動
  - @voluntas
- [CHANGE] echo 化
  - @Hexa @voluntas
- [CHANGE] Erlang VM 関連の統計を削除する
  - [Sora exporter](https://github.com/shiguredo/sora_exporter) で対応したため不要になった
  - @voluntas
- [UPDATE] テスト用 TimescaleDB を latest:pg14 に変更する
  - @Hexa
- [UPDATE] Github Actions go のバージョンを v3 にあげる
  - バージョンを `^1.18` にする
  - @Hexa
- [UPDATE] Github Actions chekcout のバージョンを v3 にあげる
  - @Hexa

## 2021.1.0

**祝リリース**
