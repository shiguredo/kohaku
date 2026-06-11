#!/bin/bash

set -euo pipefail

# 必須環境変数を事前検証する。compose.yml の env や systemd EnvironmentFile の編集忘れに
# よる unbound variable をスクリプト冒頭で一度に特定できるようにする。
# 同じ必須環境変数のチェックは scripts/run-ingester.sh にも入っている。 本スクリプトは
# Docker 経由で init/update/delete を 1 プロセスで順番に動かす想定のため一括で必須にし、
# scripts/run-ingester.sh は systemd 経由でサブコマンド単位に動かす想定のためサブコマンド
# ごとに必要なものだけを検証している。 必須環境変数を増減する場合は両方を見直す。
: "${DUCKDB_DB_PATH:?DUCKDB_DB_PATH is required}"
: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${S3_PREFIX:?S3_PREFIX is required}"
: "${RETENTION_PERIOD:?RETENTION_PERIOD is required}"
: "${UPDATE_INTERVAL:?UPDATE_INTERVAL is required}"

# grafana グループ (GID=0) と DB ファイルを共有するためグループ書き込みを許可する
umask 0002

# タイムゾーン設定 (/etc/localtime のリンク) と /var/lib/kohaku/duckdb の作成は
# Dockerfile のビルド時に済ませているため、ここでは行わない。

cd /ingester

s3_ssl_args=()
if [ "${S3_USE_SSL:-}" = "true" ]; then
  s3_ssl_args+=(--s3_use_ssl)
fi

# 未設定なら引数自体を渡さず run.py の argparse デフォルトに委ねる
initial_maximum_load_args=()
if [ -n "${INITIAL_MAXIMUM_LOAD:-}" ]; then
  initial_maximum_load_args=(--initial_maximum_load "${INITIAL_MAXIMUM_LOAD}")
fi

update_maximum_load_args=()
if [ -n "${UPDATE_MAXIMUM_LOAD:-}" ]; then
  update_maximum_load_args=(--update_maximum_load "${UPDATE_MAXIMUM_LOAD}")
fi

# テーブル作成および初期データの挿入
if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                           --s3_endpoint "${S3_ENDPOINT}" \
                           --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                           --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                           --s3_bucket "${S3_BUCKET}" \
                           --s3_prefix "${S3_PREFIX}" \
                           --s3_region "${S3_REGION:-ap-northeast-1}" \
                           "${initial_maximum_load_args[@]}" \
                           "${s3_ssl_args[@]}" \
                           init; then
  echo "run.py init failed. continue to update loop." >&2
fi

# TODO: 他の定期実行の方法を検討する
# 定期的にデータを更新
while :;
do
  if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                             --s3_endpoint "${S3_ENDPOINT}" \
                             --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                             --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                             --s3_bucket "${S3_BUCKET}" \
                             --s3_prefix "${S3_PREFIX}" \
                             --s3_region "${S3_REGION:-ap-northeast-1}" \
                             "${initial_maximum_load_args[@]}" \
                             "${update_maximum_load_args[@]}" \
                             "${s3_ssl_args[@]}" \
                             update; then
    echo "run.py update failed. continue loop." >&2
  fi

  if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                             --retention_period "${RETENTION_PERIOD}" \
                             delete; then
    echo "run.py delete failed. continue loop." >&2
  fi

  sleep "${UPDATE_INTERVAL}"
done
