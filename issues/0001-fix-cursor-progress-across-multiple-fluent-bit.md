# 複数 fluent-bit 環境での同一 last_modified カーソル脱落

- Created: 2026-08-03
- Completed: 2026-08-14
- Branch: feature/fix-cursor-progress-across-multiple-fluent-bit
- Polished: 2026-08-03
- Priority: High

## 目的

同一 last_modified 値を持つ複数の S3 オブジェクトが存在するとき、 ingester のカーソルがそのうち辞書順最大のものに進むと、 辞書順が小さいオブジェクトは `is_after_s3_cursor` が False 判定を返し、 永久に取り込まれずデータ欠損する問題を修正する。

単一 fluent-bit ノードの運用では同一 last_modified 値でオブジェクトが並ぶ機会が少ない (upload_timeout ごとに 1 回しか PUT しない) ため実質発生しないが、 複数 fluent-bit ノードが同一 prefix に並列 PUT する運用では、 PUT 完了タイミングが重なって同一 last_modified 値のオブジェクトが並びやすくなる。

## 現状

`ingester/src/run.py` のカーソル進行は以下の 2 関数が決める:

- `initialize_log_table`: `log_objects[0]` (全体最新) にカーソルを進める
- `insert_log_from_s3`: バッチ内最新 `target_log_objects[0]` にカーソルを進める

`insert_log_from_s3` は `is_after_s3_cursor` で `(last_modified, object_name)` タプルの辞書順比較 (> 判定) によりカーソル以降を絞り込む。 `initialize_log_table` はこの比較を使わず、 `list_objects` の降順ソート (`(last_modified, object_name)` 降順) に依存してカーソルを決める。 `sync_log_for_update` は初登場 target のときに `initialize_log_table` を呼ぶ。

S3 の `last_modified` は PUT 完了時刻を ISO 8601 形式で返す。 精度はサーバ実装依存で、 AWS / MinIO / RustFS 等の主要実装はミリ秒精度 (RFC 7231 の HTTP-date とは無関係) だが、 精度にかかわらず同一の last_modified 値を持つ複数のオブジェクトが並びうる。 fluent-bit の `s3_key_format` は `/${S3_PREFIX}/$TAG/%Y/%m/%d/$UUID.gz` (`fluent-bit/fluent-bit.yml.rustfs` / `.s3`) で、 `$UUID` はランダム UUID のため、 object_name の辞書順は PUT 完了時刻と無相関になる。 そのため、 同一 last_modified 値で完了した複数オブジェクトのうちどれがカーソルとして残るかは偶然で決まり、 カーソルより辞書順が小さいオブジェクトは永久脱落しうる。

再現条件:

1. 複数 fluent-bit ノードが同一 prefix (`log/rtc_stats/YYYY/MM/DD/`) に PUT する構成
2. fluent-bit A が t=12:00:00.500 に object_name "M" のオブジェクトを PUT (S3 last_modified=12:00:00.500)
3. ingester の update が実行され、 カーソルが (12:00:00.500, "M") に進む
4. 別の fluent-bit B の PUT が同じ 12:00:00.500 の last_modified で完了し、 次の update の LIST に object_name "A" のオブジェクトとして現れる (同一ミリ秒内の PUT 完了と LIST 取得の競合。 fluent-bit の `s3_key_format` の `$UUID` はランダムなので object_name の辞書順は PUT 完了時刻と無相関で、 "A" のようにカーソルより辞書順が小さい名前になりうる)
5. `is_after_s3_cursor((12:00:00.500, "A"), (12:00:00.500, "M"))` は第二要素 "A" > "M" が False → 以後の update でも対象にならず永久脱落

カーソル通過後に現れるオブジェクトの last_modified は、 PUT 完了時刻を last_modified として付与し、 PUT 完了が LIST 取得までに必ず反映される (強い一貫性) S3 では完了時刻 (カーソル通過時刻より後) になるため通常はカーソル値より新しい。 カーソル値と同値の last_modified で現れるのは、 同一ミリ秒内の PUT 完了と LIST 取得の競合、 サーバ時計が ingester より遅れている (ドリフト)、 などの条件が重なった場合である。 本 issue は強い一貫性の S3 を前提とし、 一貫性の弱い実装で発生しうる「通過前に完了した PUT の遅延出現」による別の脱落モードは対象外とする。

なお、 init 時の `initial_maximum_load` による切り捨ては「古すぎるデータを取り込まない」 意図的仕様であり、 本 issue の修正対象外とする。 カーソルと同値の last_modified を持つグループが切り捨て境界をまたぐ場合のみ、 本修正 (案 2) の再走査で救済される。

## 設計方針

案 2 (カーソル同値再走査 + 重複吸収) を採用する。 案 1 は以下に述べる理由で却下する。

- 案 2 (採用): カーソルと同値の last_modified を持つオブジェクトを object_name に関係なく毎回の update で再走査して取り込む。 これにより、 カーソルと同値の last_modified で後に現れたオブジェクトも、 カーソルがその last_modified を抜ける (より新しい last_modified に進む) までの update で拾える。 カーソルが同値グループを抜けた後に同値の last_modified で現れたオブジェクトは再走査の対象外になるが、 強い一貫性かつ時計ずれが無い S3 では理論上発生しない (サーバ時計のドリフト等の条件下では脱落が残りうる)。 再走査で発生する行重複は、 LOG_TARGETS テーブルに natural key の PK 制約を追加し、 `INSERT ... ON CONFLICT DO NOTHING` で吸収する
  - 実装上の要件は、 取り込み対象を次の 2 つに分けて処理すること:
    - カーソルより新しい last_modified のオブジェクト: 従来どおり古い順に `update_maximum_load` 件ずつバッチ分割して取り込み、 カーソルはバッチ内最新に進める
    - カーソルと同値の last_modified のオブジェクト (カーソル行自身を除く): バッチ分割の対象外として毎回の update で全件取り込む (同値グループは通常小さい)
  - 同値グループの再走査をバッチ分割に混ぜると、 カーソルが同値グループ内に滞留して、 グループより新しい last_modified のオブジェクトがバッチに載らず永久に取り込まれない (旧実装では取り込めていたオブジェクトが取り込めなくなる回帰になるため、 上記の分離は必須)
  - 両方の取り込み (新しい側のバッチと同値側の全件) とカーソル更新は、 既存の `insert_log_from_s3` と同様に 1 つのトランザクション (con.begin() / con.commit()) に含める。 同値側を別トランザクションにすると、 同値側が失敗した update でカーソルだけが進み、 同値オブジェクトが次回以降の対象から外れて永久脱落する
  - PK 制約の実装は Issue 0002 (fluent-bit の retry / 再送による重複データ除去) に委譲する。 本 issue の実装は 0002 の PK 制約 (0002 の設計方針 案 1) の採用に依存するため、 0002 が PK 制約を採用しない場合は再走査方式を再検討する。 PK 制約が未実装の状態で再走査を有効化すると、 同値グループが再取り込みされるたびに重複行が増殖するため、 0002 の完了前に本 issue の変更をリリースしない
  - 変更対象は `ingester/src/run.py` のカーソル比較 (`is_after_s3_cursor`) と、 `insert_log_from_s3` の対象絞り込み・バッチ分割 (同値グループの扱い)。 カーソル形式は現行の (last_modified, object_name) のまま変更しないため、 既存カーソル行の変換は不要。 `DUCKDB_COLUMNS/*.yml` の PK 定義と `insert_log` の重複吸収 SQL は 0002 側の変更対象
- 案 1 (却下): 現在時刻から N 秒以内の `last_modified` を持つオブジェクトを対象外にする安全境界フィルタ。 却下理由:
  - 最新 N 秒分のデータが恒常的に取り込み遅延する
  - N の選定が ingester と S3 サーバの時計ずれの許容量に依存し、 時計ずれが N を超えると問題が再発する
  - 既存の統合テスト (put 直後に init / update を実行する前提) を確実に壊し、 完了条件 2 と矛盾する

## 完了条件

- 同一 last_modified 値で PUT された全オブジェクトが取り込まれることを検証する統合テストが追加され、 通過する。 テストは s3_objects のカーソル行の書き換え等により、 タイミングの偶然に依存せずに同一 last_modified 値の状況を再現する
- 本 issue の変更のリリースは 0002 の PK 制約実装の完了後に行う
- 単一 fluent-bit 運用時の既存テストが引き続き通過する (既存のバッチ分割テストは put が同一 last_modified 値で完了しない前提のため、 実装時に対象オブジェクトの last_modified のばらつきを確認する)
- 実装方針 (案 2) の選定理由と、 見送った案 (案 1) の却下理由が commit メッセージまたは docstring に残る

## 解決方法

カーソルと同値の last_modified を持つオブジェクト (カーソル行自身を除く) を、 毎回の update で再走査して取り込むようにした (insert_log_from_s3 の同値グループ再走査)。 重複行は PK 制約 + INSERT ... ON CONFLICT DO NOTHING で吸収する。 カーソルはバッチ内最新まで進める。

- 変更ファイル: ingester/src/run.py (insert_log_from_s3 の同値グループ取り込み)、 ingester/tests/test_ingester.py (test_update_ingests_same_last_modified_object 等)
- 0002 の PK 制約実装と合わせてリリースした
