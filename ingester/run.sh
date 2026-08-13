#!/bin/bash

set -euo pipefail

# 必須環境変数を事前検証する。compose.yml の env や systemd EnvironmentFile の編集忘れに
# よる未定義変数エラーをスクリプト冒頭で一度に特定できるようにする。
# 本スクリプトは Docker Compose 経由で init + update ループ + delete を 1 プロセスで
# 回すため、全サブコマンドが参照する必須変数を冒頭でまとめて検証する。
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

# Grafana グループ (GID=0) と DB ファイルを共有するためグループ書き込みを許可する
umask 0002

cd /ingester

# SIGTERM / SIGINT を受けたら実行中の子プロセスへ TERM を転送してから正常終了する。tini が
# PID 1 として動く前提 (compose.yml の init: true) で、本スクリプトは tini の子として起動される。
# bash は外部コマンドの実行中に trap をすぐ処理しないため、長時間実行する uv run と sleep は
# run_bg 経由で子プロセスとして起動し、wait しながらシグナルを処理できるようにする。
child_pid=0
term_handler() {
  if [ "${child_pid}" -ne 0 ]; then
    kill -TERM "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
  fi
  exit 0
}
trap term_handler TERM INT

# コマンドを子プロセスとして起動して wait し、wait の exit code をそのまま返す。set -e 下でも
# wait の非ゼロ exit code で関数外へ抜けないよう、一時的に set +e で括る
# (呼び出し側が if ! ...; then で判定する挙動を維持する)。
run_bg() {
  "$@" &
  child_pid=$!
  set +e
  wait "${child_pid}"
  local rc=$?
  set -e
  child_pid=0
  return "${rc}"
}

# S3_REGION のデフォルト ap-northeast-1 は compose.yml / compose.external-s3.yml にも
# 同じ値が定義されている (意図的な二重管理)。compose 経由ではコンテナに必ず値が渡るため
# 本ファイルの :-ap-northeast-1 は通常使われないが、念のため compose 側とデフォルトを揃える。

# --s3_use_ssl はデフォルト true のため、false の場合のみ --no-s3_use_ssl を付与する。
s3_ssl_args=()
if [ "${S3_USE_SSL:-}" = "false" ]; then
  s3_ssl_args+=(--no-s3_use_ssl)
fi

# 未設定なら引数自体を渡さず run.py の argparse デフォルトに委ねる。
initial_maximum_load_args=()
if [ -n "${INITIAL_MAXIMUM_LOAD:-}" ]; then
  initial_maximum_load_args=(--initial_maximum_load "${INITIAL_MAXIMUM_LOAD}")
fi

update_maximum_load_args=()
if [ -n "${UPDATE_MAXIMUM_LOAD:-}" ]; then
  update_maximum_load_args=(--update_maximum_load "${UPDATE_MAXIMUM_LOAD}")
fi

# update_maximum_load は update でのみ参照されるため、init には渡さず update の呼び出し
# にのみ展開する。
# init でテーブル作成と初期取り込みを行う。
if ! run_bg uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
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
  # CliUsageError で連続失敗するため、init 失敗時はここで終了する。systemd 経由
  # (scripts/run-ingester.sh) は kohaku.timer の 5 分間隔で再実行、docker 経由
  # (本スクリプト) は compose の restart: no のため運用者の手動 up が前提。
  echo "run.py init failed. exiting; restart the container manually after fixing the cause." >&2
  exit 1
fi

# 定期的にデータを更新する。update / delete の失敗時も while ループを継続するため、
# S3 の一時不通等は次回以降の実行で回復できる。一方、壊れた DB 等は自動復帰しないため、
# 運用者が stderr の連続失敗を検知して手動対応する前提。init は初期化に失敗した時点で
# 継続できないため終了するが、update/delete は一時障害からの復旧を待つためループを継続する。
while :;
do
  if ! run_bg uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
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

  if ! run_bg uv run python src/run.py --db "${DUCKDB_DB_PATH}" \
                                       --retention_period "${RETENTION_PERIOD}" \
                                       delete; then
    echo "run.py delete failed. continue loop." >&2
  fi

  run_bg sleep "${UPDATE_INTERVAL}"
done
