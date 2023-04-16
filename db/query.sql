-- name: InsertSoraConnection :exec
INSERT INTO sora_connection (
    timestamp,
    label,
    version,
    node_name,
    multistream,
    simulcast,
    spotlight,
    role,
    channel_id,
    session_id,
    client_id,
    connection_id
  )
SELECT @timestamp,
  @label,
  @version,
  @node_name,
  @multistream,
  @simulcast,
  @spotlight,
  @role,
  @channel_id,
  @session_id,
  @client_id,
  @connection_id
WHERE NOT EXISTS (
    SELECT 1
    FROM sora_connection
    WHERE (
        (channel_id = @channel_id::text)
        AND (session_id = @session_id::text)
        AND (client_id = @client_id::text)
        AND (connection_id = @connection_id::text)
      )
  );


-- name: InsertUserAgentStats :exec
WITH existing_record AS (
  SELECT *
  FROM user_agents_stats
  WHERE user_agents_stats.channel_id = @channel_id
    AND user_agents_stats.connection_id = @connection_id
    AND user_agents_stats.rtc_stats_type = @rtc_stats_type
    AND user_agents_stats.rtc_stats_id = @rtc_stats_id
),
data_without_timestamp AS (
  SELECT jsonb_strip_nulls(
      jsonb_set(
        existing_record.rtc_stats_data,
        '{timestamp}',
        'null'
      )
    ) as old_data,
    jsonb_strip_nulls(
      jsonb_set(@rtc_stats_data, '{timestamp}', 'null')
    ) as new_data
  FROM existing_record
)
INSERT INTO user_agents_stats (
    timestamp,
    channel_id,
    connection_id,
    rtc_stats_timestamp,
    rtc_stats_type,
    rtc_stats_id,
    rtc_stats_data
  )
SELECT @timestamp,
  @channel_id,
  @connection_id,
  @rtc_stats_timestamp,
  @rtc_stats_type,
  @rtc_stats_id,
  @rtc_stats_data
WHERE NOT EXISTS (
  SELECT 1
  FROM data_without_timestamp
  WHERE data_without_timestamp.old_data = data_without_timestamp.new_data
);


-- test query

-- name: TestGetRtcStatsType :one
SELECT rtc_stats_type
FROM user_agents_stats
WHERE channel_id = @channel_id
  AND connection_id = @connection_id
-- TODO: 最新を取るように order がほしい？
LIMIT 1;

-- name: TestDropUserAgentStats :exec
DELETE FROM user_agents_stats;

-- 指定した type のレコードがいくつあるかどうか
-- name: TestRtcStatsCounts :one
SELECT count(*)
FROM user_agents_stats
WHERE rtc_stats_type = @rtc_type_stats
  AND channel_id = @channel_id
  AND connection_id = @connection_id;