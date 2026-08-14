# 時刻ベースの初期取り込みオプションを追加する

- Created: 2026-08-05
- Branch: feature/add-initial-max-age-hours-option
- Polished: 2026-08-06
- Priority: Medium

## pending にする理由

起票時点の前提 (initial_maximum_load のデフォルト 100 では複数 fluent-bit 環境の初期取り込みカバー時間が不足) は、 0004 のデフォルト値変更 (100 → 1000。 10 台構成で約 8 時間カバー) で大幅に改善された。 また 0006 の運用ガイドに推奨値の計算式 (カバーしたい分数 ÷ upload_timeout × ノード数) と上書き方法 (systemd の Environment / .env、 コンテナの .env) がドキュメント化され、 運用者は件数ベースのまま「過去 N 時間分」に相当する値を設定できる。

「過去 N 時間分の取り込みを保証する」目的は既存オプションの値調整で達成可能であり、 現時点で時刻ベースのオプションが無くても困る状況ではないため保留とする。 件数ベースの台数依存を設計として解消したいニーズが再燃した場合に再検討する。

## 目的

複数 fluent-bit 環境では、 件数ベースの `--initial_maximum_load` による初期取り込みのカバー時間が fluent-bit 台数に依存して変わる (現時点のデフォルト値 100 件では、 10 台構成で約 50 分、 20 台構成で約 25 分) ため、 時刻ベースで「過去 N 時間分を取り込む」ことを指定できるオプションを追加する。 件数ベースでは運用者が「過去 N 時間分」の取り込みを保証できない。

## 現状

`ingester/src/run.py` の `initialize_log_table` は、 `list_objects` の降順結果から先頭 `initial_maximum_load` 件のみを取り込み、 カーソルは全体最新オブジェクトに進める。 取り込まれるオブジェクトの時間的カバー範囲は、 単一 fluent-bit では最大 500 分 (100 件 × upload_timeout 5 分。 アイドル時レートの上界であり、 ログ量が多い環境ではさらに短くなる) だが、 N 台構成では約 500 / N 分に縮む (数値は現時点のデフォルト値 100 前提。 0004 のデフォルト変更 (100 → 1000) 適用後は 10 倍になる)。

## 設計方針

`--initial_max_age_hours` (正の整数。 時間単位) を追加し、 オブジェクトの last_modified (S3 への PUT 完了時刻、 タイムゾーン情報を含む) が「現在時刻 (UTC) - N 時間」以降のオブジェクトを初期取り込みする。

- 時刻の基準は last_modified とする (`list_objects` のソートキーは (last_modified, object_name) のみで、 ログ行の timestamp 基準はオブジェクト内容の読み取りが必要になりコストが増えるため)。 last_modified はログ行の timestamp と通常 upload_timeout 程度乖離し (リトライやバッファリングでさらに遅延し得る)、 「過去 N 時間分」はオブジェクト単位の近似になる
- 既存の `--initial_maximum_load` とは相互排他とする (両方指定した場合は CliUsageError で拒否する)。 相互排他の判定は「`--initial_maximum_load` が明示指定されたか」で行う (デフォルト値 100 が常に入るため、 argparse のデフォルトを None 化して明示指定を判定する等)。 未指定時は既存の件数ベースの動作を維持する。 なお systemd 経路では `Environment=INITIAL_MAXIMUM_LOAD=100` が常に渡される (update ループでも毎回渡るため、 時刻指定時は init / update が失敗し続ける) ため、 時刻指定で運用する場合は kohaku.service の Environment 設定の削除等で環境変数を渡さない設定に見直す
- カーソルは全体最新オブジェクトに進める (現行と同じ)。 時刻窓より古いオブジェクトは「古すぎるデータを取り込まない」方針に従い取り込まない
- `initialize_log_table` は init 経路と update 経路の初登場 target 初期化の両方で使われるため、 新オプションは両経路に適用される
- 時刻指定時は時刻窓内の全オブジェクトを取り込む (メモリは窓内オブジェクト数に比例する。 0005 の「先頭 initial_maximum_load 件のみ保持」のメモリ保証は時刻指定時には適用されない)
- 時刻窓内にオブジェクトが無い場合は、 既存の 0 件時と同じく何もしない (テーブルもカーソルも作成しない)
- 実装順序: 0005 (list_objects の pagination) が `initialize_log_table` の絞り込みを変更するため、 0005 の後に実装する (0005 の設計方針に実装順序の注意が明記済み)
- テストは S3 の last_modified が PUT 時刻になる制約を踏まえ、 時刻窓外のオブジェクト (過去の last_modified) を用意する手段 (RustFS のローカルファイル mtime 変更等) を検証して実装する (モック・スタブ禁止のため)
- 見送った案: ログ行の timestamp 基準での絞り込み (オブジェクト内容の読み取りが必要になりコストが増えるため)、 時刻指定時の件数上限併用 (件数上限が時刻指定を打ち消し「過去 N 時間分」の保証ができなくなるため)
- 運用ガイドへの反映は 0006 (複数 fluent-bit 運用ガイド) に委譲する

変更対象:

- `ingester/src/run.py`: main の argparse (`--initial_max_age_hours` 追加) と `initialize_log_table` (時刻フィルタ)
- `ingester/run.sh`・`scripts/run-ingester.sh`: INITIAL_MAX_AGE_HOURS 環境変数 → 引数の受け渡し (既存の INITIAL_MAXIMUM_LOAD パターンと同様)
- `.env.common.template`・`systemd/kohaku.service`・`compose.yml`・`compose.external-s3.yml`: 運用経路に応じた環境変数の定義
- `docs/DOCKER.md`・`docs/INGESTER_GRAFANA.md`: 環境変数一覧
- `ingester/README.md`: 実行例
- `ingester/tests/test_ingester.py`・`ingester/tests/test_run_unit.py`: 初期取り込みのテスト

## 完了条件

- 時刻ベースで「過去 N 時間分」を指定したときに (init 経路と update 経路の初登場 target 初期化の両方)、 指定範囲 (last_modified が now - N 時間以降) のオブジェクトが取り込まれ、 指定範囲外のオブジェクトが取り込まれないことを検証するテストが追加され、 通過する (カーソルが全体最新オブジェクトに進むこと、 両方指定時に CliUsageError で拒否されること、 時刻窓内にオブジェクトが無い場合にテーブル・カーソルを作成しないことを検証する)
- 既存の `--initial_maximum_load` の動作が引き続き有効である (既存テストが引き続き通過し、 未指定時 (デフォルト) に件数ベースの動作になることも検証する)
- 設計方針の各決定 (時刻の基準・相互排他・カーソル進行・時刻窓内の全オブジェクト取り込み) の選定理由と、 見送った案 (ログ行の timestamp 基準・時刻指定時の件数上限併用) の却下理由が commit メッセージまたは docstring に残る
