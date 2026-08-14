# initial_maximum_load のデフォルトが複数 fluent-bit スケールに不足

- Created: 2026-08-03
- Branch: feature/change-initial-maximum-load-default-for-multi-fluent-bit
- Polished: 2026-08-05
- Priority: Medium

## 目的

`--initial_maximum_load` のデフォルト値 (100) が単一 fluent-bit を前提にした値になっており、 複数 fluent-bit 環境で ingester を新規に立ち上げた際、 初期取り込みのカバー範囲 (時間換算) が想定より短くなり、 保持されるべき古い側のデータが取り込まれない。 複数運用スケールに合わせてデフォルトを見直す。 想定する複数構成として 10 台を基準とする。

## 現状

`ingester/src/run.py` の `DEFAULT_INITIAL_MAXIMUM_LOAD = 100` は、 init 時に読み込むファイル数の上限で、 `initialize_log_table` は降順ソートの先頭 `initial_maximum_load` 件のみを取り込み、 超過した古い側は「古すぎるデータを取り込まない」ため意図的に取り込まない (run.py の `initialize_log_table` の docstring に明記)。 カーソルは全体最新オブジェクトに進むため、 切り捨てられたオブジェクトは init 後の update でも取り込まれない。

取り込まれるオブジェクトの時間的カバー範囲は upload_timeout に依存する (単一 fluent-bit の upload_timeout=5m (fluent-bit/fluent-bit.yml.rustfs) では最大 500 分 = 約 8 時間。 これはアイドル時レートの上界であり、 ログ量が多い環境ではさらに短くなる)。 `initial_maximum_load` の上限は target (rtc_stats / session_webhook) ごとに適用される。 複数 fluent-bit (N 台) 環境では target ごとに 5 分間に N オブジェクトが put されるため、 100 オブジェクトのカバー範囲は約 500 / N 分に縮む (例: 10 台で約 50 分、 20 台で約 25 分)。

デフォルト値 100 は「古すぎるデータを取り込まない」 上限という仕組みとしては妥当だが、 複数運用のスケールに対して値が小さすぎる。 また、 適切な値が「複数運用の N 台」 と「upload_timeout」 に依存することはドキュメント化されていない。

## 設計方針

案 1 (デフォルト値の見直し) を採用する。 案 2・案 3 は以下に述べる理由で却下する。

- 案 1 (採用): `DEFAULT_INITIAL_MAXIMUM_LOAD` を 100 から 1000 に変更する。 1000 は 10 台構成で単一運用と同等の約 8 時間 (500 分) カバーを実現する値 (カバーしたい分数 ÷ upload_timeout 5 分 × N 台 = 500 ÷ 5 × 10)。 20 台構成では約 4 時間。 単一運用では初回取り込み量が 10 倍になるが、 対象オブジェクトは gzip 圧縮済みの小さな JSON オブジェクトで実害は限定的。 選定の根拠は定数直上のコメントに記載する
  - 変更対象は `ingester/src/run.py` の `DEFAULT_INITIAL_MAXIMUM_LOAD` に加えて、 `systemd/kohaku.service` の `Environment=INITIAL_MAXIMUM_LOAD=100` (systemd 経路では環境変数が必ず `--initial_maximum_load` に渡されるため、 デフォルト変更だけでは効かない) と、 `ingester/README.md` の実行例の `--initial_maximum_load 100`・`.env.common.template` のコメント値 100
  - `initial_maximum_load` は init 経路と、 update 経路の初登場 target の初期化 (`sync_log_for_update` → `initialize_log_table`) の両方で使われる
- 案 2 (却下): デフォルト値は維持し、 計算式を README / docs に追記して運用者が調整する前提にする。 却下理由: コード変更を伴わないドキュメント作業であり、 本 issue (change) のスコープ外。 計算式の記載は Issue 0006 (複数 fluent-bit 運用ガイド) の「パラメータ調整」セクションに委譲する
- 案 3 (却下・分割): `--initial_max_age_hours` のような時刻ベースのオプションを追加する。 却下理由: 新規 CLI オプションの機能追加であり、 本 issue (change) のスコープ外。 別 issue として起票済み (Issue 0007 時刻ベースの初期取り込みオプションを追加する)

## 完了条件

- `DEFAULT_INITIAL_MAXIMUM_LOAD` が 1000 に変更され、 選定の根拠 (10 台構成で約 8 時間カバー) が定数直上のコメントに記載される。 あわせて `DEFAULT_INITIAL_MAXIMUM_LOAD` の値が 1000 であることを検証するテストが追加される
- `systemd/kohaku.service` の `INITIAL_MAXIMUM_LOAD` 設定、 `ingester/README.md` の実行例、 `.env.common.template` のコメント値が新デフォルトと整合する (更新または削除)
- 既存テストが引き続き通過する (既存テストは `initial_maximum_load` を明示指定しているため、 デフォルト変更の影響を受けない)
- 実装方針 (案 1) の選定理由と、 見送った案 (案 2・案 3) の却下理由が commit メッセージまたは定数直上のコメントに残る
