# レガシー版の Kohaku

新しい Kohaku はレガシー版の Kohaku と互換性は **ありません** 。
新しい Kohaku のリリースまではレガシー版の Kohaku をお使いください。

<https://github.com/shiguredo/kohaku-legacy>

# 新しい Kohaku を開発中です

- OSS として公開します
- 2025 年の春に公開を予定しています
- 2025 年の夏にリリースを予定しています
- ライセンスは [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.html) として公開を予定しています

# WebRTC Stats Analyzer Kohaku

[![GitHub tag (latest SemVer)](https://img.shields.io/github/tag/shiguredo/kohaku.svg)](https://github.com/shiguredo/kohaku)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## About Shiguredo's open source software

We will not respond to PRs or issues that have not been discussed on Discord. Also, Discord is only available in Japanese.

Please read <https://github.com/shiguredo/oss/blob/master/README.en.md> before use.

## 時雨堂のオープンソースソフトウェアについて

利用前に <https://github.com/shiguredo/oss> をお読みください。

## WebRTC Stats Analyzer Kohaku について

WebRTC Stats Analyzer Kohaku は時雨堂が開発/販売している WebRTC SFU Sora の出力するログを利用して、
WebRTC 統計情報を収集、可視化するソリューションです。

複数のオープンソースやサービスを組み合わせることで実現しています。

## 特徴

- Sora が出力するログを Grafana で可視化することができます
- Docker Compose で簡単に構築できます
- Sora のログを S3 または S3 互換オブジェクトストレージ(以降オブジェクトストレージ)に Fluent Bit で転送します
- ログの保存先をオブジェクトストレージにすることでスケールさせやすい構成になっています
- オブジェクトストレージと DuckDB を利用するだけのため、コストを抑えることができます
- オンプレミスでもクラウドでも利用できます

### 新しい Kohaku とレガシー版の Kohaku との違い

新しい Kohaku はレガシー版の Kohaku とは互換性がありません。

レガシー版の Kohaku は Sora の統計エクスポーター機能を利用して、
WebRTC 統計情報を収集し、TimescaleDB に保存するゲートウェイでした。

新しい Kohaku は Sora のログを Fluent Bit でオブジェクトストレージに転送し、
それを DuckDB で解析し、Grafana を利用して可視化するソリューションです。

## 利用オープンソース

- [Fluent Bit](https://github.com/fluent/fluent-bit)
  - Sora のログをオブジェクトストレージに転送します
- [MinIO](https://github.com/minio/minio)
  - Fluent Bit から転送されてきたログを保存する S3 互換オブジェクトストレージ
- [Grafana](https://github.com/grafana/grafana)
  - DuckDB で処理したデータを可視化します
- [Grafana DuckDB Data Source Plugin](https://github.com/motherduckdb/grafana-duckdb-datasource)
  - DuckDB で取得したデータを Grafana に渡します
- [DuckDB](https://github.com/duckdb/duckdb)
  - オブジェクトストレージに保存されたログを処理します

### MinIO の代わりに利用できるサービス

- [Amazon | S3](https://aws.amazon.com/jp/s3/)
- [Google Cloud | Cloud Storage](https://cloud.google.com/storage?hl=ja)
- [Cloudflare R2](https://www.cloudflare.com/ja-jp/developer-platform/products/r2/)
- [Akamai Cloud | Object Storage](https://www.linode.com/products/object-storage/)
- [DigitalOcean | Spaces](https://www.digitalocean.com/products/spaces)
- [Vultr | Object Storage](https://www.vultr.com/products/object-storage/)

## 対応 Sora

- WebRTC SFU Sora 2024.1 以降

## 優先実装

優先実装とは Sora のライセンスを契約頂いているお客様限定で Kohaku の実装予定機能を有償にて前倒しで実装することです。

**詳細は Discord やメールなどでお気軽にお問い合わせください**

- 追加の Grafana ダッシュボード

## サポートについて

### Discord

- **サポートしません**
- アドバイスします
- フィードバック歓迎します

最新の状況などは Discord で共有しています。質問や相談も Discord でのみ受け付けています。

<https://discord.gg/shiguredo>

### バグ報告

Discord へお願いします。

## ライセンス

Apache License 2.0

```text
Copyright 2025-2025, Hiroshi Yoshida (Original Author)
Copyright 2025-2025, Shiguredo Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
