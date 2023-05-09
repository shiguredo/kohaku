package sqlc

import (
	"context"
	"testing"
	"time"

	db "github.com/shiguredo/kohaku/gen/sqlc"
)

func TestSoraConnection(t *testing.T) {
	c := context.Background()
	q.InsertSoraConnection(c, db.InsertSoraConnectionParams{
		Timestamp:    time.Now().UTC(),
		Label:        "label",
		Version:      "version",
		NodeName:     "node_name",
		Multistream:  true,
		Simulcast:    true,
		Spotlight:    true,
		Role:         "sendrecv",
		ChannelID:    "channel_id",
		SessionID:    base32edUUIDv4(),
		ClientID:     base32edUUIDv4(),
		ConnectionID: base32edUUIDv4(),
	})

}
