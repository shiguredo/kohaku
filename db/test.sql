INSERT INTO sora_rtc_stats (
    timestamp,

    version,
    label,
    node_name,

    multistream,
    simulcast,
    spotlight,

    role,

    channel_id,
    session_id,
    client_id,
    connection_id,

    rtc_stats_timestamp,
    rtc_stats_type,
    rtc_stats_id,

    rtc_stats_data
) VALUES (
    now(),

    '2024.1.0',
    'label',
    'sora@192.0.2.1',

    true,
    false,
    false,

    'sendrecv',

    'channel-id',
    'session-id',
    'client-id',
    'connection-id',

    900000.1000,
    'inbound-rtp',
    'ID',

    '{"a": 1, "b": { "c": 2, "d": [1, 2, 3] }}'
);