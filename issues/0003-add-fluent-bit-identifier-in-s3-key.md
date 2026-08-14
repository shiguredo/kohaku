# fluent-bit 識別子を S3 オブジェクトキーに含める

- Created: 2026-08-03
- Branch: feature/add-fluent-bit-identifier-in-s3-key
- Polished: 2026-08-05
- Priority: High

## 目的

複数 fluent-bit 環境で、 S3 に put されたオブジェクトがどの fluent-bit ノード由来か判別できるように、 `s3_key_format` にホスト名を含める設定を追加する。

## 現状

`fluent-bit/fluent-bit.yml.rustfs` と `fluent-bit/fluent-bit.yml.s3` の `s3_key_format` は以下:

```
s3_key_format: /${S3_PREFIX}/$TAG/%Y/%m/%d/$UUID.gz
```

`$UUID` は fluent-bit の s3 出力プラグインが生成するランダム 8 文字の英数字で、 fluent-bit ノード識別子を含まない。 結果:

- 特定 fluent-bit ノード由来のオブジェクトを S3 上で識別できない (「ノード A の rtc_stats のみが取り込まれていない」 等の障害調査で、 どのオブジェクトが該当ノード由来か特定困難)
- fluent-bit ノード単位の運用可視性 (put 頻度・失敗率) が S3 側から追えない

Sora のログには `node_name` フィールドがあり (`DUCKDB_COLUMNS/rtc_stats.yml`)、 DuckDB 上での Sora ノード識別は可能。 ただし、 fluent-bit ノード = Sora ノードとは限らない (fluent-bit が別ホストで動く構成もあり得る)。

## 設計方針

fluent-bit の `s3_key_format` にホスト名を含める:

```
s3_key_format: /${S3_PREFIX}/$TAG/%Y/%m/%d/${HOSTNAME}-$UUID.gz
```

- `${HOSTNAME}` は fluent-bit の組み込み環境変数で、 設定読み込み時に展開される (s3_key_format の `$TAG` / `$UUID` のようなプラグイン固有のテンプレートとは異なる)。 環境変数 HOSTNAME が存在しない場合は `gethostname()` で OS のホスト名に補完される (fluent-bit 5.0.9 の flb_env.c で確認)。 本リポジトリの標準運用 (systemd 経由の `make setup-fluent-bit` / `make setup-fluent-bit-for-rustfs`) では HOSTNAME 環境変数が渡されないため、 OS のホスト名が展開される
- Docker 構成 (compose.yml) では HOSTNAME 環境変数にコンテナ ID の短縮形が自動設定されるため、 コンテナ ID が展開される (コンテナ再作成で変化する)。 本 issue の対象は systemd 構成の複数 fluent-bit 環境である。 Docker 構成の compose.yml は変更対象外とするが、 対象ファイルの変更は生成物 fluent-bit.yml を経由して Docker 構成にも波及し、 キーにコンテナ ID が焼き込まれる
- 本リポジトリの標準運用は 1 ホスト 1 fluent-bit (systemd) であり、 ホスト名がそのまま fluent-bit ノード識別子として機能する。 ノード識別子の明示設定 (ノード名の env 追加) は標準運用に追加作業を強いるため採用しない。 ホスト名は OS / コンテナ単位の識別子であるため、 同一ホスト上で複数 fluent-bit を動かす構成ではノード判別に使えない。 ホスト名が一意になるよう運用側で制約する
- 既存オブジェクト (ホスト名なし) と新規オブジェクト (ホスト名あり) が同一 prefix に混在しても、 ingester のカーソル比較は (last_modified, object_name) タプルで行われるため取り込みに影響しない
- Issue 0001 (カーソル同値再走査 + 重複吸収、 採用済み) はカーソル形式 (last_modified, object_name) を維持するため、 object_name へのホスト名追加はカーソル進行・再走査に影響しない。 Issue 0002 の PK 制約も natural key ベースで object_name に依存しない

設定変更対象ファイル:

- `fluent-bit/fluent-bit.yml.rustfs`
- `fluent-bit/fluent-bit.yml.s3`
- `ingester/tests/fixtures/fluent-bit/default-fluent-bit.yml.j2` (テスト fixture、 統合テストが fluent-bit を起動する際に使用)

## 完了条件

- 上記 3 ファイルの `s3_key_format` に `${HOSTNAME}` が含まれる
- S3 上のオブジェクトキーにホスト名が含まれることを検証する統合テストが追加され、 通過する。 テストは fixture の env に HOSTNAME を明示し、 fluent-bit が put したオブジェクトのキーにその値が含まれることを確認する。 fixture の env セクションは fixture を利用する全テストで共有されるため、 既存テスト用にデフォルト値を設けて描画する
- 既存の統合テスト (`test_fluent_bit_rustfs_integration.py`) が引き続き通過する
- `docs/SORA_FLUENT_BIT.md` への追記と、 ホスト名一意性の運用制約の記載は、 Issue 0006 (複数 fluent-bit 運用ガイド) に委譲する (0006 は未 polish のため、 本委譲内容は 0006 の polish 時にスコープへ含める)
