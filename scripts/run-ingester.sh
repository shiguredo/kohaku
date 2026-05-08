#!/bin/sh
set -eu

SUBCOMMAND="${1:-}"

case "${SUBCOMMAND}" in
    init|update)
        set -- \
            --db "${DUCKDB_DB_PATH}" \
            --s3_endpoint "${S3_ENDPOINT}" \
            --s3_access_key_id "${AWS_ACCESS_KEY_ID}" \
            --s3_secret_access_key "${AWS_SECRET_ACCESS_KEY}" \
            --s3_bucket "${S3_BUCKET}" \
            --s3_prefix "${S3_PREFIX}" \
            --initial_maximum_load "${INITIAL_MAXIMUM_LOAD}"

        # --s3_use_ssl は action="store_true" のため、true の場合のみフラグを付与する
        if [ "${S3_USE_SSL:-}" = "true" ]; then
            set -- "$@" --s3_use_ssl
        fi
        ;;
    delete)
        # delete は S3 接続を行わないため S3 オプションは不要
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
