#!/bin/sh
set -eu

S3_ALIAS=storage

if [ "${S3_USE_SSL:-}" = "true" ]; then
  endpoint_scheme=https
else
  endpoint_scheme=http
fi

mc alias set "${S3_ALIAS}" "${endpoint_scheme}://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}"

while :; do
  mc find "${S3_ALIAS}/${S3_BUCKET}/${S3_PREFIX}" --older-than "${RETENTION_PERIOD}d" --exec 'mc rm {}'
  sleep "${CLEANUP_INTERVAL}"
done
