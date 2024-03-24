SET allow_experimental_object_type = 1;

DROP TABLE IF EXISTS sora_rtc_stats;
CREATE TABLE IF NOT EXISTS sora_rtc_stats (
    timestamp DateTime64(3, 'UTC'),

    version String,
    label String,
    node_name String,

    multistream UInt8,
    simulcast UInt8,
    spotlight UInt8,

    role Enum8('sendrecv' = 1, 'sendonly' = 2, 'recvonly' = 3),

    channel_id String,
    session_id String,
    client_id String,
    connection_id String,

    rtc_stats_timestamp Float64,
    rtc_stats_type String,
    rtc_stats_id String,

    rtc_stats_data JSON,

    created_at DateTime64(3, 'UTC') DEFAULT now()
) ENGINE = MergeTree()
PRIMARY KEY (connection_id, timestamp)
