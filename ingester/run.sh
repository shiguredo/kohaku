#!/bin/bash

set -euo pipefail

# 必須環境変数を事前検証する。compose.yml の env や systemd EnvironmentFile の編集忘れに
# よる unbound variable をスクリプト冒頭で一度に特定できるようにする。
# 本スクリプトは docker / compose 経由で init + update ループ + delete を 1 プロセスで
# 回すため、 全サブコマンドが参照する必須変数を冒頭でまとめて検証する。
# scripts/run-ingester.sh は systemd 経由で 1 サブコマンドのみ実行する起動形態のため、
# 検証する必須変数の組が異なる点に注意 (両ファイル変更時は対象サブコマンドを揃えること)。
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

# update_maximum_load は update でのみ参照されるため、 init には渡さず update の呼び出し
# にのみ展開する。
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
  # init 失敗のまま update ループに入ると s3_objects テーブルが無い状態で update が
  # CliUsageError で連続失敗するため、 init 失敗時はここで終了し、 systemd / docker の
  # restart policy 側で再起動戦略を制御してもらう。
  echo "run.py init failed. exit to let the restart policy retry." >&2
  exit 1
fi

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
