CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TYPE sora_connection_role AS ENUM (
    'sendrecv',
    'sendonly',
    'recvonly'
);

DROP TABLE IF EXISTS sora_connection;
CREATE TABLE IF NOT EXISTS sora_connection (
    pk bigserial NOT NULL PRIMARY KEY,

    -- Sora 側から送られてきたタイムスタンプ
    timestamp timestamptz NOT NULL,

    version TEXT NOT NULL,
    label TEXT NOT NULL,
    node_name TEXT NOT NULL,

    multistream BOOLEAN NOT NULL,
    simulcast BOOLEAN NOT NULL,
    spotlight BOOLEAN NOT NULL,

    role sora_connection_role NOT NULL,
    channel_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);

DROP TABLE IF EXISTS sora_user_agent_stats;
CREATE TABLE IF NOT EXISTS sora_user_agent_stats (
    -- Sora 側から送られてきたタイムスタンプ
    timestamp timestamptz NOT NULL,

    channel_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,

    rtc_stats_timestamp DOUBLE PRECISION NOT NULL,
    rtc_stats_type TEXT NOT NULL,
    rtc_stats_id TEXT NOT NULL,

    rtc_stats_data JSONB NOT NULL,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
);
SELECT create_hypertable('sora_user_agent_stats', 'timestamp');
ALTER TABLE sora_user_agent_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'channel_id, connection_id'
);
-- 圧縮の INTERNVAL の値は自由に変えること
SELECT add_compression_policy('sora_user_agent_stats', INTERVAL '7 days');
-- 保持の INTERNVAL の値は自由に変えること
SELECT add_retention_policy('sora_user_agent_stats', INTERVAL '60 days');