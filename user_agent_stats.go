package kohaku

import (
	"encoding/json"

	"github.com/jackc/pgtype"
	"github.com/labstack/echo/v4"
	db "github.com/shiguredo/kohaku/gen/sqlc"
)

type RTCStats struct {
	Timestamp float64 `json:"timestamp"`
	ID        string  `json:"id"`
	Type      string  `json:"type"`
}

func (s *Server) collectorUserAgentStats(c echo.Context, stats soraConnectionStats) error {
	if err := s.query.InsertSoraConnection(c.Request().Context(), db.InsertSoraConnectionParams{
		Timestamp:    stats.Timestamp,
		Label:        stats.Label,
		Version:      stats.Version,
		NodeName:     stats.NodeName,
		Multistream:  *stats.Multistream,
		Simulcast:    *stats.Simulcast,
		Spotlight:    *stats.Spotlight,
		Role:         stats.Role,
		ChannelID:    stats.ChannelID,
		SessionID:    stats.SessionID,
		ClientID:     stats.ClientID,
		ConnectionID: stats.ConnectionID,
	}); err != nil {
		return err
	}

	for _, v := range stats.Stats {
		// ここで data をいれる JSONB を用意する
		var jsonb pgtype.JSONB
		if err := jsonb.Set(v); err != nil {
			return err
		}

		// timestamp と id と type のみを取り出す
		rtcStats := new(RTCStats)
		if err := json.Unmarshal(v, &rtcStats); err != nil {
			return err
		}

		// 保存する
		if err := s.query.InsertUserAgentStats(c.Request().Context(), db.InsertUserAgentStatsParams{
			Timestamp:         stats.Timestamp,
			ChannelID:         stats.ChannelID,
			ConnectionID:      stats.ConnectionID,
			RtcStatsTimestamp: rtcStats.Timestamp,
			RtcStatsType:      rtcStats.Type,
			RtcStatsID:        rtcStats.ID,
			RtcStatsData:      jsonb,
		}); err != nil {
			return err
		}
	}

	return nil
}
