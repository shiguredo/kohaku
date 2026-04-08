#!/usr/bin/env bash
set -euo pipefail

# テスト目的:
# rustfs を S3 互換ストレージとして起動し、s3-cleaner が
# 保持期間超過オブジェクトを削除できることを確認する。
# scripts/s3-cleaner.sh 本体は無変更のまま検証する。

NETWORK="kohaku-ci-s3-cleaner-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
RUSTFS_CONTAINER="kohaku-ci-rustfs-cleaner-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
MC_IMAGE="minio/mc:RELEASE.2025-07-21T05-28-08Z"
RUSTFS_IMAGE="rustfs/rustfs:1.0.0-alpha.89"

AWS_ACCESS_KEY_ID="kohaku-access-key"
AWS_SECRET_ACCESS_KEY="kohaku-secret-key"
S3_ENDPOINT="rustfs:9000"
S3_BUCKET="kohaku-ci-bucket"
S3_PREFIX="cleanup-target"

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

# 事前準備:
# テスト用バケットと削除対象オブジェクトを作成する。
docker run --rm \
  --network "${NETWORK}" \
  --entrypoint /bin/sh \
  -e "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  -e "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  -e "S3_ENDPOINT=${S3_ENDPOINT}" \
  -e "S3_BUCKET=${S3_BUCKET}" \
  -e "S3_PREFIX=${S3_PREFIX}" \
  "${MC_IMAGE}" \
  -ceu '
    mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}" >/dev/null
    mc mb --ignore-existing "storage/${S3_BUCKET}" >/dev/null
    echo "cleanup test object" | mc pipe "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null
    mc stat "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null
  '

sleep 2

# 検証:
# s3-cleaner は無限ループなので、短時間起動して停止し、
# 1 回以上のクリーンアップが走る時間を与える。
CLEANER_CONTAINER="kohaku-ci-s3-cleaner-runner-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
docker run -d \
  --name "${CLEANER_CONTAINER}" \
  --network "${NETWORK}" \
  --entrypoint /bin/sh \
  -v "${PWD}/scripts/s3-cleaner.sh:/scripts/s3-cleaner.sh:ro" \
  -e "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  -e "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  -e "S3_ENDPOINT=${S3_ENDPOINT}" \
  -e "S3_BUCKET=${S3_BUCKET}" \
  -e "S3_PREFIX=${S3_PREFIX}" \
  -e "S3_USE_SSL=false" \
  -e "RETENTION_PERIOD=0" \
  -e "CLEANUP_INTERVAL=1" \
  "${MC_IMAGE}" \
  /scripts/s3-cleaner.sh >/dev/null
sleep 4
docker rm -f "${CLEANER_CONTAINER}" >/dev/null

# 検証結果:
# 作成したオブジェクトが存在しないことを確認する。
docker run --rm \
  --network "${NETWORK}" \
  --entrypoint /bin/sh \
  -e "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}" \
  -e "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}" \
  -e "S3_ENDPOINT=${S3_ENDPOINT}" \
  -e "S3_BUCKET=${S3_BUCKET}" \
  -e "S3_PREFIX=${S3_PREFIX}" \
  "${MC_IMAGE}" \
  -ceu '
    mc alias set storage "http://${S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY}" >/dev/null
    if mc stat "storage/${S3_BUCKET}/${S3_PREFIX}/old.log" >/dev/null 2>&1; then
      echo "s3-cleaner が対象オブジェクトを削除できていません" >&2
      exit 1
    fi
  '
