#!/bin/sh
set -eu

S3_ALIAS=storage
MC_INIT_MAX_RETRIES="${MC_INIT_MAX_RETRIES:-5}"
MC_INIT_RETRY_INTERVAL="${MC_INIT_RETRY_INTERVAL:-2}"

if [ "${S3_USE_SSL:-}" = "true" ]; then
  endpoint_scheme=https
else
  endpoint_scheme=http
fi

i=0
while ! mc alias set "${S3_ALIAS}" "${endpoint_scheme}://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}" >/dev/null 2>&1 \
   || ! mc ls "${S3_ALIAS}" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "${i}" -ge "${MC_INIT_MAX_RETRIES}" ]; then
    echo "storage 接続待機がタイムアウトしました: ${endpoint_scheme}://${S3_ENDPOINT}" >&2
    exit 1
  fi
  sleep "${MC_INIT_RETRY_INTERVAL}"
done

mc mb --ignore-existing "${S3_ALIAS}/${S3_BUCKET}"

mc ilm rule add --expire-days "${RETENTION_PERIOD}" "${S3_ALIAS}/${S3_BUCKET}"
