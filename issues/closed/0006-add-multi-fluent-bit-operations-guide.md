# 複数 fluent-bit 運用ガイドを docs/ に追加する

- Created: 2026-08-03
- Completed: 2026-08-14
- Branch: feature/add-multi-fluent-bit-operations-guide
- Polished: 2026-08-05
- Priority: Medium

## 目的

複数 fluent-bit ノードから同一 S3 バケットに put する運用構成について、 以下の運用上の注意点を docs/ にガイドとして追加する:

- fluent-bit 間の設定不一致リスク (compression / parser / s3_key_format 等)。 compression の不一致は取り込みエラーで該当 target の取り込みが停止する (壊れたオブジェクトを除去するまで継続)。 parser の不一致は行がパースできないと fluent-bit 側で落ちて取り漏れ、 s3_key_format の不一致はリスト対象 prefix の外に出ると取り漏れになる
- fluent-bit の tail DB 破損・消失時のリスク (オフセット消失による停止期間中の取り漏れ。 再送はされない) と、 fluent-bit のクラッシュ等によるオフセット巻き戻し時の at-least-once 再送 (Issue 0002 の PK 制約で吸収) と対策
- 複数 fluent-bit 前提でのパラメータ調整 (`upload_timeout` / `initial_maximum_load` / `update_maximum_load`)
- 障害切り分けの手順 (どの fluent-bit が停止しているか、 Issue 0003 実装後は S3 オブジェクトキーから追跡可能)
- ingester 側で発生する既知問題 (Issue 0001〜0005 への参照)

## 現状

`docs/SORA_FLUENT_BIT.md` は単一 fluent-bit の構築手順のみで、 複数運用の想定がドキュメント化されていない。

複数 fluent-bit 運用時に必要な情報が欠落:

- fluent-bit.yml の生成元 (fluent-bit.yml.s3 / fluent-bit.yml.rustfs と .env の設定値) を全ノードで一致させる運用手順 (ノード個別の値 (SORA_LOG_PATH 等) のみ .env 側で差異を許容する)
- 各 fluent-bit の tail DB (`/var/lib/fluent-bit/rtc_stats.db`・`session_webhook.db`) の永続化前提 (systemd の場合の path、 コンテナの場合の volume)
- `upload_timeout` と ingester の更新間隔の関係 (systemd 構成では kohaku.timer の 5 分間隔、 Docker 構成では `UPDATE_INTERVAL` 環境変数 (秒))
- どの fluent-bit が停止しているかを特定する手段がドキュメント化されていない (Issue 0003 実装後は S3 オブジェクトキーから追跡可能)

## 設計方針

`docs/` 配下に新規ドキュメント `docs/MULTI_FLUENT_BIT.md` を追加し、 あわせて `docs/SORA_FLUENT_BIT.md` への追記 (Issue 0003 の委譲) を行う。 以下のセクション構成:

- 概要: 複数 fluent-bit ノード + 1 台の ingester を運用する構成の説明
- 設定管理: 全ノードで fluent-bit.yml の生成元 (テンプレートと .env の設定値) を一致させる方法 (Ansible / Chef / 手動同期。 ノード個別の値 (SORA_LOG_PATH 等) は .env 側で差異を許容) と、 fluent-bit 間の設定不一致の帰結、 `retry_limit` の扱い (Issue 0002 の委譲。 内部 retry による別 UUID の重複オブジェクト生成、 無効化の選択肢、 0002 の PK 制約での吸収)、 ホスト名一意性の運用制約 (Issue 0003 の委譲。 1 ホスト 1 fluent-bit でホスト名を一意にする)
- tail DB の永続化: systemd / コンテナ両方での永続化手順 (破損時の復旧手順を含む)
- パラメータ調整: `upload_timeout` と更新間隔の関係 (PUT から取り込みまでの遅延の上界)、 `initial_maximum_load` の推奨値の計算式 (Issue 0004 の委譲。 カバーしたい分数 ÷ upload_timeout × N 台) と変更後デフォルト 1000、 `update_maximum_load` は 1 バッチの件数上限で target ごとに適用され、 fluent-bit 台数に依存しないため現行デフォルト 100 のまま。 上書き方法は systemd では kohaku.service の Environment と `.env` (EnvironmentFile が後で評価されるため `.env` 側が優先) / コンテナでは `.env`。 Issue 0007 (時刻ベースの初期取り込みオプション) は対応検討中
- 障害切り分け: fluent-bit ノード単位の put 状況確認手順 (S3 側の grep、 Issue 0003 実装後)。 systemd 構成ではホスト名で追跡可能、 Docker 構成ではコンテナ ID (再作成で変化) のため長期追跡には不向き
- 既知問題: Issue 0001 (同一 last_modified カーソル脱落)、 Issue 0002 (重複データ。 対応時は既存 DB の再 init が必要)、 Issue 0004 (initial_maximum_load デフォルト)、 Issue 0005 (list_objects の OOM) への参照

`docs/README.md` からリンクを張る。

## 完了条件

- `docs/MULTI_FLUENT_BIT.md` が作成され、 設計方針で列挙した各セクションの記載内容が全て含まれる
- `docs/SORA_FLUENT_BIT.md` に S3 オブジェクトキーの形式 (ホスト名を含む) の追記が行われる (Issue 0003 の委譲)
- `docs/README.md` にリンクが追加される
- Issue 0001〜0005 の対応状況を踏まえて記述する (対応済み = 実装完了・ issues/closed へ移動済み なら「Issue 000N で修正済み」、 未対応なら「Issue 000N で対応検討中」)

## 解決方法

docs/MULTI_FLUENT_BIT.md を新規作成し、 概要・設定管理 (fluent-bit.yml の生成元の一致、 設定不一致の影響、 retry_limit の扱い、 ホスト名の一意性)・tail DB の永続化・パラメータ調整 (upload_timeout と更新間隔、 initial_maximum_load の計算式、 update_maximum_load、 上書き方法)・障害切り分け・関連 issue と対応状況の各セクションを記載した。 docs/SORA_FLUENT_BIT.md に S3 オブジェクトキーの形式 (ホスト名を含む) を追記し、 docs/README.md にリンクを追加した。 構成管理ツールの言及を削除する整理も実施した。

- 変更ファイル: docs/MULTI_FLUENT_BIT.md (新規)、 docs/SORA_FLUENT_BIT.md (S3 オブジェクトキーの形式)、 docs/README.md (リンク)
