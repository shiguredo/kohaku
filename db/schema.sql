CREATE EXTENSION IF NOT EXISTS timescaledb;

DROP TABLE IF EXISTS sora_connection;
CREATE TABLE IF NOT EXISTS sora_connection (
    pk bigserial NOT NULL PRIMARY KEY,

    -- クライアント側から送られてきたタイムスタンプ
    timestamp timestamptz NOT NULL,

    version TEXT NOT NULL,
    label TEXT NOT NULL,
    node_name TEXT NOT NULL,

    multistream boolean NOT NULL,
    simulcast boolean NOT NULL,
    spotlight boolean NOT NULL,

    role TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);

DROP TABLE IF EXISTS sora_user_agents_stats;
CREATE TABLE IF NOT EXISTS sora_user_agents_stats (
    timestamp timestamptz NOT NULL,

    channel_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,

    rtc_stats_timestamp DOUBLE PRECISION NOT NULL,
    rtc_stats_type TEXT NOT NULL,
    rtc_stats_id TEXT NOT NULL,

    rtc_stats_data JSONB NOT NULL,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);
SELECT create_hypertable('sora_user_agents_stats', 'timestamp');
ALTER TABLE sora_user_agents_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'channel_id, connection_id'
);
-- 圧縮の INTERNVAL の値は自由に変えること
SELECT add_compression_policy('sora_user_agents_stats', INTERVAL '3 days');
-- 保持の INTERNVAL の値は自由に変えること
SELECT add_retention_policy('sora_user_agents_stats', INTERVAL '14 days');