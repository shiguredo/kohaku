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
		Role:         db.SoraConnectionRole(stats.Role),
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
		// TODO: ここで timestamp と id と type が空の場合はエラーにする

		/*
			保存する、ただし channel_id と connection_id と rtc_stats_id と rtc_stats_type が同一の場合、
			rtc_stats_timestamp 以外が変更されていた場合のみ更新する
		*/
		if err := s.query.InsertSoraUserAgentStats(c.Request().Context(), db.InsertSoraUserAgentStatsParams{
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
