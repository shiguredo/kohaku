# list_objects が pagination なしで全件をメモリ展開する

- Created: 2026-08-03
- Completed: 2026-08-14
- Branch: feature/fix-list-objects-pagination
- Polished: 2026-08-05
- Priority: Medium

## 目的

`ingester/src/run.py` の `list_objects` は target 別 prefix 配下の全オブジェクトを一度にメモリ展開してから sorted する。 単一 fluent-bit の通常運用 (数万件) では問題ないが、 複数 fluent-bit 環境や ILM ルール設定漏れで数十万件超が溜まったバケットに対しては、 メモリ大量消費により init / update が OOM で異常終了する堅牢性欠陥がある。 大規模化に耐えるよう、 全件展開をやめて必要な分だけを保持する方式に変更する。

## 現状

`list_objects` の実装は以下:

```python
def list_objects(client, bucket, prefix):
    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    return sorted(
        objects, key=lambda obj: (obj.last_modified, obj.object_name), reverse=True
    )
```

- MinIO Python SDK の `client.list_objects` は generator を返し、 ページ (1 リクエスト 1000 件) を遅延取得する。 全件をメモリ展開するのは kohaku 側の `list(...)` 呼び出し
- `list(...)` で全件を一度にメモリ展開
- `sorted(...)` でさらに全件を保持したまま sort
- kohaku 側で `initial_maximum_load` / `update_maximum_load` で切り詰めるのは sorted 後
- minio SDK の `Object` は object_name / last_modified / etag / size 等を保持するため、 1 件あたり数百 B〜1 KB 程度のメモリを使用し、 数十万件で数百 MB に達する

問題:

- 数十万オブジェクトで数百 MB のメモリ使用
- 大規模バケットで初回 init を実行すると OOM の可能性 (update 時も全件展開のため同様)
- update 時も毎回全件取得する (リクエスト数はオブジェクト数に比例。 この issue の対象外であり、 LIST リクエスト数は削減できない)

## 設計方針

案 2 (全ページ走査 + 必要分のみ保持) を採用する。 案 1・案 3 は以下に述べる理由で却下する。

- 案 2 (採用): SDK の generator を list 化せず、 ページ単位で走査して必要なオブジェクトのみを保持する
  - `initialize_log_table` は降順ソートの先頭 `initial_maximum_load` 件 (先頭 1 件が全体最新でカーソルを兼ねる) を保持する。 メモリは O(initial_maximum_load)
  - `insert_log_from_s3` はカーソル以降 (`is_after_s3_cursor` で判定) のオブジェクトのうち、 古い側 `update_maximum_load` 件を保持し、 あわせてカーソルと同値の last_modified のオブジェクト (0001 の再走査対象。 カーソル行自身を除く) を全件保持する。 メモリは O(update_maximum_load + 同値グループサイズ) で、 同値グループは通常小さい。 同値グループの取り込みは Issue 0001 のスコープであり、 本 issue は保持のみを行う。 0001 実装前の `is_after_s3_cursor` (タプル比較) では同値かつ object_name が大きいオブジェクトは両方の集合に属しうるが、 保持の重複はメモリ上界に影響しない
  - LIST リクエスト数は削減できない (キーの日付 prefix はチャンク生成時刻由来で last_modified と乖離しうるため、 ページのキー範囲と last_modified の対応は保証されず、 「カーソルを超えたページ」の打ち切りは成立しない)
  - Issue 0001 (カーソル同値再走査 + 重複吸収、 採用済み) の同値グループ再走査は全ページ走査が前提のため、 本変更と両立する
  - Issue 0007 (時刻ベースの初期取り込みオプション) は `initialize_log_table` の絞り込みを変更するため、 本 issue の変更対象と重なる。 実装順序 (0005 → 0007) に注意する
- 案 1 (却下): start_after ベースのカーソル絞り込み。 却下理由: S3 の start_after はキー基準のページングで、 カーソル (last_modified, object_name) に変換できない (last_modified はキーに含まれない)。 0001 の確定方針 (カーソル形式 (last_modified, object_name) 維持) の下では、 現行コメントの却下理由 (オブジェクトキーが時系列順とは限らない) は不変
- 案 3 (却下): list_objects_v2 + Delimiter で日付 prefix 単位に絞り込み。 却下理由: キーの日付 (`$TAG/%Y/%m/%d/`) はチャンク生成時刻由来で、 last_modified (PUT 完了時刻) とは独立。 upload_timeout 5m の日付境界跨ぎや retry による遅延 PUT で乖離し、 日付 prefix でスキャン範囲を絞るとカーソルより新しい last_modified のオブジェクトが取り漏れる

変更対象は `ingester/src/run.py` の `list_objects` と、 その呼び出し側の `initialize_log_table` / `insert_log_from_s3` (オブジェクトの保持方法と切り詰めの変更)。

## 完了条件

- `list_objects` とその呼び出し経路が全件をメモリ展開しないことを検証するテストが追加され、 通過する。 init 経路 (先頭 `initial_maximum_load` 件のみ保持) と update 経路 (カーソルより古い多数のオブジェクトとカーソルより新しい少数のオブジェクト、 およびカーソルと同値の last_modified のオブジェクトを混在させ、 保持するオブジェクト数が上限を超えないこと) の両方を検証する
- 既存の統合テストが引き続き通過する (init / update の取り込み件数・カーソル進行の挙動が変わらないこと)
- 実装方針 (案 2) の選定理由と、 見送った案 (案 1・案 3) の却下理由が commit メッセージまたは docstring に残る

## 解決方法

MinIO SDK の generator をそのまま返す iter_objects に変更し、 呼び出し側で必要な分だけを保持する方式にした。 init 経路は keep_latest_objects (降順上位 N 件のみ保持)、 update 経路は collect_update_targets (最古 N 件とカーソル同値グループを 1 回の走査で収集) を使う。

- 変更ファイル: ingester/src/run.py (iter_objects / keep_latest_objects / collect_update_targets を新設)、 ingester/tests/test_ingester.py (保持件数の上限とメモリ (tracemalloc) を検証するテスト)、 ingester/tests/test_run_unit.py (docstring の参照修正)
- 選定理由と却下理由 (案 1: start_after はカーソル (last_modified, object_name) に変換できない、 案 3: 日付 prefix はチャンク生成時刻由来で last_modified と乖離する) は iter_objects の docstring に記載
