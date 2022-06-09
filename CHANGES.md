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

## 2021.2.0

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
