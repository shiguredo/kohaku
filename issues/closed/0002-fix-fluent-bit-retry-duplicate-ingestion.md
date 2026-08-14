# fluent-bit の retry / 再送による重複データが ingester で除去されない

- Created: 2026-08-03
- Completed: 2026-08-14
- Branch: feature/fix-fluent-bit-retry-duplicate-ingestion
- Polished: 2026-08-03
- Priority: High

## 目的

fluent-bit の retry / 再送で、 同一 event が複数の `$UUID` で S3 に put された場合、 ingester 側に重複除去機構がなく、 DuckDB に重複行が挿入される問題を修正する。

重複の発生経路は次の 2 つ:

- s3 出力プラグインの内部 retry (`retry_limit` デフォルト 5): アップロードに失敗したチャンクは内部リトライで再送され、 再アップロード時に新しい `$UUID` が生成されるため、 以前のオブジェクトは上書きされない。 なお `auto_retry_requests` (デフォルト true) は同一リクエストの即時再試行で、 新しい `$UUID` は生成しないため重複の原因にならない
- fluent-bit の at-least-once 配信: 予期しない停止 (SIGKILL 等) 時にオフセットが巻き戻り、 送信済みの行が再送される

Grafana の集計値 (COUNT / SUM) が実際より大きくなり、 ダッシュボードの信頼性が失われる。 再送の規模が大きいほど集計値の乖離が拡大する。

## 現状

`ingester/src/run.py` の `create_log_table` と `insert_log` は、 `DUCKDB_COLUMNS/*.yml` で定義したスキーマで `read_json` → `rel.create` / `rel.insert_into` する。

- `DUCKDB_COLUMNS/rtc_stats.yml`: PK 制約なし。 natural key 候補は (connection_id, rtc_id, rtc_timestamp)。 テストデータ 1000 行でこの組の重複・NULL は 0 件 (同一 (connection_id, rtc_id) は約 60 秒間隔で最大 16 回出現)。 なお id は同一 connection の複数の rtc サブイベントで共有されるため単体では一意でない
- `DUCKDB_COLUMNS/session_webhook.yml`: PK 制約なし。 natural key 候補は id (Sora が発行する UUID)
- ingester は `is_after_s3_cursor` で S3 オブジェクト単位の重複取得は防いでいるが、 S3 上に別 UUID で重複 put されたオブジェクトの内容は素通しで DuckDB に insert される

再現条件:

1. fluent-bit A が rtc_stats のログ行 X を put (S3 上の UUID "U1")
2. ingester が U1 を取り込み、 DuckDB に X の行が入る
3. fluent-bit A の retry (サーバには put 成功したがクライアント側で失敗と判断した場合等) により、 同じログ行 X が別 UUID "U2" で put される
4. ingester が U2 を取り込み → DuckDB に同じ (connection_id, rtc_id, rtc_timestamp) の行が 2 つ存在

## 設計方針

案 1 (DuckDB PK 制約 + INSERT ... ON CONFLICT DO NOTHING) を採用する。 案 2・案 3 は以下に述べる理由で却下する。

- 案 1 (採用): `rtc_stats` テーブルに (connection_id, rtc_id, rtc_timestamp) の PK、 `session_webhook` テーブルに id の PK を追加し、 重複行を `INSERT ... ON CONFLICT DO NOTHING` で吸収する
  - 実装上の注意 (以下の項目と既存 DB の挙動は実機確認済み):
    - `rel.create` は PK 引数を持たないため、 `CREATE TABLE ... PRIMARY KEY` への書き換えが必要
    - `rel.insert_into` は ON CONFLICT を指定できないため、 `insert_log` も `INSERT ... SELECT ... ON CONFLICT DO NOTHING` 形式の SQL への書き換えが必要
    - `DUCKDB_COLUMNS/*.yml` は「カラム名: 型」の並びのみのため、 PK 定義の保持方法 (別セクション化等) と `load_columns` の変更が必要
    - PK カラムは暗黙 NOT NULL で、 natural key のいずれかが欠落した行が混入すると insert 全体が失敗する。 実データでの非 NULL・一意性の確認を実装時の作業項目に含め、 一意でなかった場合は key の再検討または許容判断を行う
  - 既存 DB は、 重複行の有無にかかわらず DB ファイル削除 + init 再実行を必須とする (データは破棄される)。 PK なしテーブルへの `INSERT ... ON CONFLICT` は BinderException で失敗し、 重複行がある場合の `ALTER TABLE ADD PRIMARY KEY` も失敗するため、 既存 DB をそのまま更新し続けることはできない。 なお init 再実行は最新 `initial_maximum_load` (デフォルト 100) オブジェクト分しか再取得しないため、 保持期間内の古いデータも失われる。 再 init 時に `--initial_maximum_load` を引き上げるか、 データ損失を許容するかを運用側で判断する
  - Issue 0001 (カーソル同値再走査 + 重複吸収、 採用済み) は、 本 issue の PK 制約 (案 1) の採用に依存する (0001 の設計方針参照)。 本 issue の PK 制約により、 0001 の再走査で再取り込みされる行重複も吸収される
- 案 2 (却下): `read_json` の結果を `SELECT DISTINCT ...` してから insert する。 却下理由: DISTINCT は同一バッチ内の重複のみを除去し、 既に DB に挿入済みの行・別バッチで到着した再送分 (本 issue の再現条件) の重複は除去できない
- 案 3 (却下): fluent-bit 側で重複を出さない (`retry_limit` の無効化・tail DB 永続化の徹底などの運用ガイド化)。 却下理由: 単独では ingester 側の重複を防げず、 完了条件 1 を満たせない。 運用ガイド化は Issue 0006 (複数 fluent-bit 運用ガイド) のスコープに委譲する

## 完了条件

- fluent-bit の再送を模した統合テストが追加され、 通過する。 テストは「空バケットで init → 同一 event を UUID "U1" で put → update で取り込み → 同一 event を別 UUID "U2" で put → update」のクロスバッチ手順 (2 回の update にまたがる手順) で行い、 DuckDB の該当行 (natural key で特定) が 1 行のみになることを検証する。 `rtc_stats` / `session_webhook` の両テーブルで検証し、 同一 last_modified になった場合のカーソル比較 (0001 未実装時) に備えて UUID の辞書順を U1 < U2 に固定する
- 単一 fluent-bit 運用時の既存テストが引き続き通過する (案 1 の PK 導入で失敗する既存テスト 5 件 (test_update、 test_update_skips_broken_object_and_continues_other_targets の broken-gzip / malformed-json の 2 ケース、 test_update_maximum_load_splits_batches、 test_update_maximum_load_one_takes_single_object_per_call) は、 同一内容の再 put で件数増を期待するもののため、 一意な行を使うデータ変更または期待値変更を加えた上で通過させる)
- 実装方針 (案 1) の選定理由と、 見送った案 (案 2・案 3) の却下理由が commit メッセージまたは docstring に残る

## 解決方法

rtc_stats / session_webhook テーブルに natural key の PK 制約を追加し、 INSERT ... ON CONFLICT DO NOTHING で重複行を吸収するようにした。

- 変更ファイル: ingester/src/run.py (create_log_table / insert_log の PK 制約と ON CONFLICT 句)、 ingester/DUCKDB_COLUMNS/rtc_stats.yml・session_webhook.yml (primary_key 定義)、 ingester/tests/test_ingester.py (test_update_deduplicates_retransmitted_object 等)
- 既存 DB は PK 制約の無いテーブルのため、 重複行の有無にかかわらず DB ファイル削除 + init 再実行が必要
