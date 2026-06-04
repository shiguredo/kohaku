#!/bin/sh
set -eu

SUBCOMMAND="${1:-}"

# サブコマンド共通で DB パスは必須
: "${DUCKDB_DB_PATH:?DUCKDB_DB_PATH is required}"

case "${SUBCOMMAND}" in
    init|update)
        # init / update では S3 接続に関わる環境変数を事前検証する。systemd EnvironmentFile
        # の編集忘れによる unbound variable をここで一度に特定できるようにする。
        : "${S3_ENDPOINT:?S3_ENDPOINT is required}"
        : "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
        : "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
        : "${S3_BUCKET:?S3_BUCKET is required}"
        : "${S3_PREFIX:?S3_PREFIX is required}"

        set -- \
            --db "${DUCKDB_DB_PATH}" \
            --s3_endpoint "${S3_ENDPOINT}" \
            --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
            --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
            --s3_bucket "${S3_BUCKET}" \
            --s3_prefix "${S3_PREFIX}" \
            --s3_region "${S3_REGION:-ap-northeast-1}"

        # 未設定なら引数自体を渡さず run.py の argparse デフォルトに委ねる
        if [ -n "${INITIAL_MAXIMUM_LOAD:-}" ]; then
            set -- "$@" --initial_maximum_load "${INITIAL_MAXIMUM_LOAD}"
        fi

        # update_maximum_load は update でのみ参照されるため、update の時だけ渡す
        if [ "${SUBCOMMAND}" = "update" ] && [ -n "${UPDATE_MAXIMUM_LOAD:-}" ]; then
            set -- "$@" --update_maximum_load "${UPDATE_MAXIMUM_LOAD}"
        fi

        # --s3_use_ssl は action="store_true" のため、true の場合のみフラグを付与する
        if [ "${S3_USE_SSL:-}" = "true" ]; then
            set -- "$@" --s3_use_ssl
        fi
        ;;
    delete)
        # delete は S3 接続を行わないため S3 オプションは不要
        : "${RETENTION_PERIOD:?RETENTION_PERIOD is required}"
        set -- \
            --db "${DUCKDB_DB_PATH}" \
            --retention_period "${RETENTION_PERIOD}"
        ;;
    *)
        echo "Usage: $0 {init|update|delete}" >&2
        exit 1
        ;;
esac

exec /opt/uv/bin/uv run python src/run.py "$@" "${SUBCOMMAND}"
