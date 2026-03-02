#!/bin/bash

set -euo pipefail

DEBIAN_FRONTEND=noninteractive

apt-get -y update && apt-get -y install curl python3

# UTC ではなく /UTC にリンクが貼られ、DuckDB の TimeZone 設定も /UTC になるため、
# Python API 側で UnknownTimeZoneError になるため、リンクを UTC に変更する
ln -fs /usr/share/zoneinfo/UTC /etc/localtime

curl -LsSf https://astral.sh/uv/install.sh | sh
. $HOME/.local/bin/env

mkdir -p /var/lib/kohaku/duckdb

cd /ingester
rm -rf ./.venv
uv sync
# テーブル作成および初期データの挿入
uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                      --storage "${STORAGE}" \
                      --s3_endpoint "${S3_ENDPOINT}" \
                      --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                      --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                      --s3_bucket "${S3_BUCKET}" \
                      --s3_prefix "${S3_PREFIX}" \
                      init

[ $? -ne 0 ] && echo "Failed to initialize the database." && exit 1

# TODO: 他の定期実行の方法を検討する
# 定期的にデータを更新
while :;
do
  uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                        --storage "${STORAGE}" \
                        --s3_endpoint "${S3_ENDPOINT}" \
                        --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
                        --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
                        --s3_bucket "${S3_BUCKET}" \
                        --s3_prefix "${S3_PREFIX}" \
                        update

  [ $? -ne 0 ] && echo "Failed to update the database." && exit 1

  uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                        --retention_period "${RETENTION_PERIOD}" \
                        delete

  [ $? -ne 0 ] && echo "Failed to delete old logs." && exit 1

  sleep "${UPDATE_INTERVAL}"
done
