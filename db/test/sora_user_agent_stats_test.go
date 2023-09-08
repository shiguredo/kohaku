package sqlc

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgtype"
	db "github.com/shiguredo/kohaku/gen/sqlc"
	"github.com/stretchr/testify/assert"
)

func TestSoraUserAgentStats(t *testing.T) {
	c := context.Background()
	var err error

	channelID := "channel_id"
	connectionID := base32edUUIDv4()

	rawStatsData := []byte(`{"timestamp": 1683605052194.28, "id": "UUIDv4", "type": "outbound-rtp"}`)
	statsData := pgtype.JSONB{}
	statsData.Set(rawStatsData)

	err = q.InsertSoraUserAgentStats(c, db.InsertSoraUserAgentStatsParams{
		Timestamp:         time.Now().UTC(),
		ChannelID:         channelID,
		ConnectionID:      connectionID,
		RtcStatsTimestamp: 1683605052194.865,
		RtcStatsType:      "outbound-rtp",
		RtcStatsID:        "UUIDv4",
		RtcStatsData:      statsData,
	})
	assert.NoError(t, err)

	count, err := q.TestGetUserAgentStatsTypeCount(c, db.TestGetUserAgentStatsTypeCountParams{
		RtcTypeStats: "outbound-rtp",
		ChannelID:    channelID,
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)
	assert.EqualValues(t, 1, count)

	// 時間だけが違うやつ
	err = q.InsertSoraUserAgentStats(c, db.InsertSoraUserAgentStatsParams{
		Timestamp:         time.Now().UTC(),
		ChannelID:         channelID,
		ConnectionID:      connectionID,
		RtcStatsTimestamp: 1683605052200.000,
		RtcStatsType:      "outbound-rtp",
		RtcStatsID:        "UUIDv4",
		RtcStatsData:      statsData,
	})
	assert.NoError(t, err)

	// 時間だけ違うのでレコードは増えない
	count, err = q.TestGetUserAgentStatsTypeCount(c, db.TestGetUserAgentStatsTypeCountParams{
		RtcTypeStats: "outbound-rtp",
		ChannelID:    channelID,
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)
	assert.EqualValues(t, 1, count)

	// a: b が追加されている
	rawNewStatsData := []byte(`{"timestamp": 1683605052194.28, "id": "UUIDv4", "type": "outbound-rtp", "a": "b"}`)
	newStatsData := pgtype.JSONB{}
	newStatsData.Set(rawNewStatsData)

	// 時間が違い、 a: b が追加されているのでレコードが増える
	err = q.InsertSoraUserAgentStats(c, db.InsertSoraUserAgentStatsParams{
		Timestamp:         time.Now().UTC(),
		ChannelID:         channelID,
		ConnectionID:      connectionID,
		RtcStatsTimestamp: 1683605052200.500,
		RtcStatsType:      "outbound-rtp",
		RtcStatsID:        "UUIDv4",
		RtcStatsData:      newStatsData,
	})
	assert.NoError(t, err)

	// レコードが増える
	count, err = q.TestGetUserAgentStatsTypeCount(c, db.TestGetUserAgentStatsTypeCountParams{
		RtcTypeStats: "outbound-rtp",
		ChannelID:    channelID,
		ConnectionID: connectionID,
	})
	assert.NoError(t, err)
	assert.EqualValues(t, 2, count)

	err = q.TestDropSoraUserAgentStats(c)
	assert.NoError(t, err)
}
