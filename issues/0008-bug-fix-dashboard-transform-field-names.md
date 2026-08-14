# rtc-stats.json の transform のフィールド参照をフィールド名に統一する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-dashboard-transform-field-names
- Polished: 2026-08-14
- Priority: Medium
- Model: deepseek-v4-flash

## 目的

Grafana ダッシュボード (grafana/dashboards/kohaku/rtc-stats.json) のサブパネルの transform (filterFieldsByName) の names / pattern が、 表示名 (フレーム名 + フィールド名) に依存した不統一な状態で、 パネル 83 はタイトルと異なるメトリックを表示し、 将来の Grafana バージョンで表示名の計算ロジックが変わると他のパネルも表示が壊れるリスクがある。 names / pattern をフィールド名 (カラム名) に統一する。

## 現状

ソースパネル (パネル 3, 40, 66, 89) のクエリ結果を参照するサブパネルの filterFieldsByName の names は 3 形式が混在している:

- プレフィックスなし (例: ["time", "jitter"]) — パネル 4〜24 など
- "A " プレフィックス付き (例: ["time", "A jitter"]) — パネル 31〜39, 41〜61, 65, 68〜87, 90〜97 など
- "B " プレフィックス + キャメルケース (例: ["time", "B packetsReceived/s"]) — パネル 25〜30, 62〜64 など

Grafana 12.4.3 の dashboard datasource は、 参照パネルの targets の refId を無視してソースパネルの全クエリ結果 (A / B フレーム) を返し、 filterFieldsByName の names はフィールド名と表示名 (フレーム名 + フィールド名、 例: "A jitter") の両方に照合する。 そのため A / B プレフィックス付きの names は現状の Grafana 12.4.3 では表示名に一致して動作するが、 表示名の計算ロジックに依存している。

パネル 83 (retransmittedBytesSent) は names が "A retransmitted_packets_sent" で、 パネル 84 (retransmittedPacketsSent) と同一のフィールド (パケット数) を表示している。 ソースパネル 66 の A クエリには retransmitted_bytes_sent と retransmitted_packets_sent の両方が存在するため、 パネル 83 は「バイト数を表示するパネル」のつもりがパケット数を表示しており、 retransmitted_bytes_sent を表示するパネルが欠落している。 また、 outbound video 行に packetsSent を表示するパネルが 2 枚 (パネル 68 と 80) 存在し、 同一のフィールドを重複表示している。

pattern は表示名に対して評価されるが、 多くのパネルの pattern は表示名に一致しない死に設定 (例: "^(jitter .+|Time)" は表示名 "A jitter" に一致しない。 パネル 90〜95 および 69, 72, 75, 78, 81, 84, 87 は末尾に "_" が付いたタイポ、 パネル 21 (totalSquaredInterFrameDelay) はパネル 19 (totalDecodeTime) からのコピペ誤記)。 names が一致するため現状は動作する。

なお、 ソースパネル 89 の B クエリに時間カラム名のタイポ (AS timee) が存在するが、 本 issue の対象外 (transform の修正では治らない)。 ソースパネル 66 / 89 の B クエリ (レート系フィールド) を表示するパネルが存在しない (計算されるが表示先がない) ことも、 パネルの追加を伴うため本 issue の対象外とする。

## 設計方針

実機 (Grafana 12.4.3) でダッシュボードを開き、 全サブパネルの表示を確認したうえで、 各ソースパネルの rawSql の AS 別名とパネルごとの names を突き合わせ、 names をフィールド名 (カラム名) に統一する (表示名の計算ロジックに依存しない形にする)。 パネル 83 は names を retransmitted_bytes_sent に修正する。 pattern は表示名に対して評価されるためフィールド名に統一しても機能せず、 names が一致すれば不要なため削除する。

## 完了条件

- rtc-stats.json の全サブパネルの names がフィールド名 (カラム名) に統一され、 pattern が削除される
- パネル 83 が retransmitted_bytes_sent (バイト数) を表示し、 全パネルがタイトルに対応する正しいメトリックを表示することを実機 (Grafana 12.4.3) で確認する
- 既存の Grafana 統合テスト (ingester/tests/test_grafana_integration.py) が引き続き通過する (names の回帰検出は 0015 の統合テスト拡張で検討する)
