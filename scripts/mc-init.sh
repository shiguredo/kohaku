#!/bin/sh
set -eu

S3_ALIAS=storage
MC_INIT_ENABLE_ILM="${MC_INIT_ENABLE_ILM:-false}"

sleep 5

if [ "${S3_USE_SSL:-}" = "true" ]; then
  endpoint_scheme=https
else
  endpoint_scheme=http
fi

mc alias set "${S3_ALIAS}" "${endpoint_scheme}://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}"
mc mb --ignore-existing "${S3_ALIAS}/${S3_BUCKET}"

if [ "${MC_INIT_ENABLE_ILM}" = "true" ]; then
  mc ilm rule add --expire-days "${RETENTION_PERIOD}" "${S3_ALIAS}/${S3_BUCKET}"
fi
