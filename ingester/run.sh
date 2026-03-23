#!/bin/bash

set -euo pipefail

# UTC ではなく /UTC にリンクが貼られ、DuckDB の TimeZone 設定も /UTC になるため、
# Python API 側で UnknownTimeZoneError になるため、リンクを UTC に変更する
ln -fs /usr/share/zoneinfo/UTC /etc/localtime

mkdir -p /var/lib/kohaku/duckdb

cd /ingester

s3_ssl_args=()
if [ "${S3_USE_SSL:-}" = "true" ]; then
  s3_ssl_args+=(--s3_use_ssl)
fi

# テーブル作成および初期データの挿入
if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                           --storage "${STORAGE}" \
                           --s3_endpoint "${S3_ENDPOINT}" \
                           --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                           --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                           --s3_bucket "${S3_BUCKET}" \
                           --s3_prefix "${S3_PREFIX}" \
                           "${s3_ssl_args[@]}" \
                           init; then
  echo "run.py init failed. continue to update loop." >&2
fi

# TODO: 他の定期実行の方法を検討する
# 定期的にデータを更新
while :;
do
  if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                             --storage "${STORAGE}" \
                             --s3_endpoint "${S3_ENDPOINT}" \
                             --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                             --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                             --s3_bucket "${S3_BUCKET}" \
                             --s3_prefix "${S3_PREFIX}" \
                             "${s3_ssl_args[@]}" \
                             update; then
    echo "run.py update failed. continue loop." >&2
  fi

  uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                        --retention_period "${RETENTION_PERIOD}" \
                        delete

  sleep "${UPDATE_INTERVAL}"
done
