#!/bin/sh
set -eu

S3_ALIAS=storage
MC_INIT_MAX_RETRIES="${MC_INIT_MAX_RETRIES:-5}"
MC_INIT_RETRY_INTERVAL="${MC_INIT_RETRY_INTERVAL:-2}"

# while ループの [ "${i}" -ge "${MC_INIT_MAX_RETRIES}" ] が非数値で死ぬのを防ぐ。
# MAX_RETRIES は 1 以上 (1 だと初回失敗で即タイムアウトする) を要求する。
case "${MC_INIT_MAX_RETRIES}" in
  *[!0-9]*|0)
    echo "MC_INIT_MAX_RETRIES must be a positive integer: ${MC_INIT_MAX_RETRIES}" >&2
    exit 1
    ;;
esac
# RETRY_INTERVAL は 0 (即時リトライ) を許容するが、 非数値は sleep が失敗するため弾く。
case "${MC_INIT_RETRY_INTERVAL}" in
  *[!0-9]*)
    echo "MC_INIT_RETRY_INTERVAL must be a non-negative integer: ${MC_INIT_RETRY_INTERVAL}" >&2
    exit 1
    ;;
esac

# mc コマンドで参照する必須環境変数を事前検証する。compose.yml の env や
# EnvironmentFile の編集忘れによる unbound variable を、 mc への接続を試みる前に
# 一度に特定できるようにする。
: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${RETENTION_PERIOD:?RETENTION_PERIOD is required}"

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
    echo "Timed out waiting for storage endpoint: ${endpoint_scheme}://${S3_ENDPOINT}" >&2
    exit 1
  fi
  sleep "${MC_INIT_RETRY_INTERVAL}"
done

mc mb --ignore-existing "${S3_ALIAS}/${S3_BUCKET}"

# mc ilm rule add は冪等ではなく、 同じパラメータで複数回実行するとルールが累積するため、
# 既存ルールを一度全削除してから追加し直す。 初回実行でルールが無いケースを吸収するため
# 失敗を許容する。 mc-init.sh は docker / docker-compose 経由で新規バケットにのみ
# 使われる前提で、 既存運用バケットの ILM 設定を消す心配は無い。
mc ilm rule remove --all --force "${S3_ALIAS}/${S3_BUCKET}" 2>/dev/null || true
mc ilm rule add --expire-days "${RETENTION_PERIOD}" "${S3_ALIAS}/${S3_BUCKET}"
