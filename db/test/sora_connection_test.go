package sqlc

import (
	"context"
	"testing"
	"time"

	db "github.com/shiguredo/kohaku/gen/sqlc"
	"github.com/stretchr/testify/assert"
)

func TestSoraConnection(t *testing.T) {
	c := context.Background()
	var err error

	channelID := "channel_id"
	sessionID := base32edUUIDv4()
	connectionID := base32edUUIDv4()

	err = q.InsertSoraConnection(c, db.InsertSoraConnectionParams{
		Timestamp:    time.Now().UTC(),
		Label:        "label",
		Version:      "version",
		NodeName:     "node_name",
		Multistream:  true,
		Simulcast:    true,
		Spotlight:    true,
		Role:         db.SoraConnectionRoleSendrecv,
		ChannelID:    channelID,
		SessionID:    sessionID,
		ClientID:     "client_id",
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)

	sc, err := q.TestGetSoraConnection(c, db.TestGetSoraConnectionParams{
		ChannelID:    "channel_id",
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)
	assert.Equal(t, "label", sc.Label)
	assert.Equal(t, db.SoraConnectionRoleSendrecv, sc.Role)

	count, err := q.TestGetSoraConnectionCount(c)
	assert.NoError(t, err)
	assert.EqualValues(t, 1, count)

	// 同じものを追加
	err = q.InsertSoraConnection(c, db.InsertSoraConnectionParams{
		Timestamp:    time.Now().UTC(),
		Label:        "label",
		Version:      "version",
		NodeName:     "node_name",
		Multistream:  true,
		Simulcast:    true,
		Spotlight:    true,
		Role:         db.SoraConnectionRoleSendrecv,
		ChannelID:    channelID,
		SessionID:    sessionID,
		ClientID:     "client_id",
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)

	// 1 そのまま
	count, err = q.TestGetSoraConnectionCount(c)
	assert.NoError(t, err)
	assert.EqualValues(t, 1, count)

	newConnectionID := base32edUUIDv4()

	// コネクション ID だけ新しくする
	err = q.InsertSoraConnection(c, db.InsertSoraConnectionParams{
		Timestamp:    time.Now().UTC(),
		Label:        "label",
		Version:      "version",
		NodeName:     "node_name",
		Multistream:  true,
		Simulcast:    true,
		Spotlight:    true,
		Role:         db.SoraConnectionRoleSendrecv,
		ChannelID:    channelID,
		SessionID:    sessionID,
		ClientID:     "client_id",
		ConnectionID: newConnectionID,
	})
	assert.NoError(t, err)

	// 2 になる
	count, err = q.TestGetSoraConnectionCount(c)
	assert.NoError(t, err)
	assert.EqualValues(t, 2, count)

	err = q.TestDropSoraConnection(c)
	assert.NoError(t, err)
}
