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
    SELECT id
    FROM sora_connection
    WHERE (
        (channel_id = @channel_id::text)
        AND (session_id = @session_id::text)
        AND (client_id = @client_id::text)
        AND (connection_id = @connection_id::text)
      )
  );

-- name: InsertUserAgentStats :exec
INSERT INTO user_agents_stats (
    timestamp,
    channel_id,
    connection_id,
    rtc_stats_timestamp,
    rtc_stats_type,
    rtc_stats_id,
    rtc_stats_data
  )
VALUES (
    @timestamp,
    @channel_id,
    @connection_id,
    @rtc_stats_timestamp,
    @rtc_stats_type,
    @rtc_stats_id,
    @rtc_stats_data
  );