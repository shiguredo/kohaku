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

	err = q.InsertSoraConnection(c, db.InsertSoraConnectionParams{
		Timestamp:    time.Now().UTC(),
		Label:        "label",
		Version:      "version",
		NodeName:     "node_name",
		Multistream:  true,
		Simulcast:    true,
		Spotlight:    true,
		Role:         db.SoraConnectionRoleSendrecv,
		ChannelID:    "channel_id",
		SessionID:    base32edUUIDv4(),
		ClientID:     "client_id",
		ConnectionID: base32edUUIDv4(),
	})
	assert.NoError(t, err)

	err = q.TestDropSoraConnection(c)
	assert.NoError(t, err)
}
