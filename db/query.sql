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


-- name: InsertSoraUserAgentStats :exec
WITH existing_record AS (
  SELECT *
  FROM sora_user_agent_stats
  WHERE sora_user_agent_stats.channel_id = @channel_id
    AND sora_user_agent_stats.connection_id = @connection_id
    AND sora_user_agent_stats.rtc_stats_type = @rtc_stats_type
    AND sora_user_agent_stats.rtc_stats_id = @rtc_stats_id
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
INSERT INTO sora_user_agent_stats (
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

-- name: TestGetSoraConnection :one
SELECT *
FROM sora_connection
WHERE channel_id = @channel_id
  AND connection_id = @connection_id
LIMIT 1;

-- name: TestGetSoraConnectionCount :one
SELECT count(*)
FROM sora_connection;

-- name: TestGetUserAgentStatsType :one
SELECT rtc_stats_type
FROM sora_user_agent_stats
WHERE channel_id = @channel_id
  AND connection_id = @connection_id
ORDER BY timestamp DESC
LIMIT 1;

-- 指定した channel_id と connection_id と rtc_stats_type のレコードがいくつあるかどうか
-- name: TestGetUserAgentStatsTypeCount :one
SELECT count(*)
FROM sora_user_agent_stats
WHERE rtc_stats_type = @rtc_type_stats
  AND channel_id = @channel_id
  AND connection_id = @connection_id;

-- name: TestDropSoraConnection :exec
DELETE FROM sora_connection;

-- name: TestDropSoraUserAgentStats :exec
DELETE FROM sora_user_agent_stats;
