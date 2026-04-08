#!/usr/bin/env bash
set -euo pipefail

# テスト目的:
# rustfs を S3 互換ストレージとして起動し、mc-init が
# 1. ストレージへ接続できること
# 2. 指定バケットを作成できること
# を確認する。

NETWORK="kohaku-ci-mc-init-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
RUSTFS_CONTAINER="kohaku-ci-rustfs-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
MC_IMAGE="minio/mc:RELEASE.2025-07-21T05-28-08Z"
RUSTFS_IMAGE="rustfs/rustfs:1.0.0-alpha.89"

AWS_ACCESS_KEY_ID="kohaku-access-key"
AWS_SECRET_ACCESS_KEY="kohaku-secret-key"
S3_ENDPOINT="rustfs:9000"
S3_BUCKET="kohaku-ci-bucket"
RETENTION_PERIOD="7"

cleanup() {
  docker rm -f "${RUSTFS_CONTAINER}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK}" >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker network create "${NETWORK}" >/dev/null

docker run -d \
  --name "${RUSTFS_CONTAINER}" \
  --network "${NETWORK}" \
  --network-alias rustfs \
  -e "RUSTFS_ACCESS_KEY=${AWS_ACCESS_KEY_ID}" \
  -e "RUSTFS_SECRET_KEY=${AWS_SECRET_ACCESS_KEY}" \
  "${RUSTFS_IMAGE}" >/dev/null

# 検証 1:
# 本番同等の実行方法で scripts/mc-init.sh を実行し、
# バケット作成まで完了することを確認する。
docker run --rm \
  --network "${NETWORK}" \
  --entrypoint /bin/sh \
  -v "${PWD}/scripts/mc-init.sh:/scripts/mc-init.sh:ro" \
  -e "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  -e "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  -e "S3_ENDPOINT=${S3_ENDPOINT}" \
  -e "S3_BUCKET=${S3_BUCKET}" \
  -e "S3_USE_SSL=false" \
  -e "RETENTION_PERIOD=${RETENTION_PERIOD}" \
  -e "MC_INIT_ENABLE_ILM=false" \
  -e "MC_INIT_MAX_RETRIES=30" \
  -e "MC_INIT_RETRY_INTERVAL=1" \
  "${MC_IMAGE}" \
  /scripts/mc-init.sh

# 検証 2:
# 作成済みバケットへ mc ls が通ることを確認する。
docker run --rm \
  --network "${NETWORK}" \
  --entrypoint /bin/sh \
  -e "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  -e "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  -e "S3_ENDPOINT=${S3_ENDPOINT}" \
  -e "S3_BUCKET=${S3_BUCKET}" \
  "${MC_IMAGE}" \
  -ceu '
    mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}" >/dev/null
    mc ls "storage/${S3_BUCKET}" >/dev/null
  '
