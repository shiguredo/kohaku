#!/bin/bash

set -euo pipefail

# grafana グループ (GID=0) と DB ファイルを共有するためグループ書き込みを許可する
umask 0002

# タイムゾーン設定 (/etc/localtime のリンク) と /var/lib/kohaku/duckdb の作成は
# Dockerfile のビルド時に済ませているため、ここでは行わない。

cd /ingester

s3_ssl_args=()
if [ "${S3_USE_SSL:-}" = "true" ]; then
  s3_ssl_args+=(--s3_use_ssl)
fi

initial_maximum_load="${INITIAL_MAXIMUM_LOAD:-100}"
update_maximum_load="${UPDATE_MAXIMUM_LOAD:-100}"

# テーブル作成および初期データの挿入
if ! uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                           --s3_endpoint "${S3_ENDPOINT}" \
                           --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                           --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                           --s3_bucket "${S3_BUCKET}" \
                           --s3_prefix "${S3_PREFIX}" \
                           --initial_maximum_load "${initial_maximum_load}" \
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
                             --initial_maximum_load "${initial_maximum_load}" \
                             --update_maximum_load "${update_maximum_load}" \
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
