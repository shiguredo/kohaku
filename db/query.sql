-- name: InsertSoraConnection :exec
INSERT INTO sora_connection (
  timestamp,
  label, version, node_name,
  multistream, simulcast, spotlight,
  role, channel_id, session_id, client_id, connection_id
)
SELECT
  @timestamp,
  @label, @version, @node_name,
  @multistream, @simulcast, @spotlight,
  @role, @channel_id, @session_id, @client_id, @connection_id
WHERE
  NOT EXISTS (
    SELECT id
    FROM sora_connection
    WHERE (
      (channel_id = @channel_id::varchar(255)) AND
      (session_id = @session_id::char(26)) AND
      (client_id = @client_id::varchar(255)) AND
      (connection_id = @connection_id::char(26))
    )
);