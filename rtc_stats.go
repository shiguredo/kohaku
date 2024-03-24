package kohaku

import (
	"encoding/json"
	"time"

	"github.com/labstack/echo/v4"
)

type RTCStats struct {
	Timestamp float64 `json:"timestamp"`
	ID        string  `json:"id"`
	Type      string  `json:"type"`
}

type RtcStats struct {
	Timestamp time.Time `json:"timestamp"`

	Label    string
	Version  string
	NodeName string

	Multistream bool
	Simulcast   bool
	Spotlight   bool

	Role         string
	ChannelID    string
	SessionID    string
	ClientID     string
	ConnectionID string

	RtcStatsTimestamp float64
	RtcStatsType      string
	RtcStatsID        string
	RtcStatsData      json.RawMessage
}

func (s *Server) rtcStats(c echo.Context, stats soraConnectionStats) error {
	// TODO: Insert する場合は batch が良さそう
	for _, v := range stats.Stats {
		// timestamp と id と type のみを取り出す
		rtcStats := new(RTCStats)
		if err := json.Unmarshal(v, &rtcStats); err != nil {
			return err
		}
		// TODO: ここで timestamp と id と type が空の場合はエラーにする

		// INSERT する
		err := s.conn.Exec(c.Request().Context(), `
			INSERT INTO sora_rtc_stats (
				timestamp,
				version, label, node_name,
				multistream, simulcast, spotlight,
				role,
				channel_id, session_id, client_id, connection_id,
				rtc_stats_timestamp, rtc_stats_type, rtc_stats_id,
				rtc_stats_data
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			stats.Timestamp,
			stats.Version, stats.Label, stats.NodeName,
			stats.Multistream, stats.Simulcast, stats.Spotlight,
			stats.Role,
			stats.ChannelID, stats.SessionID, stats.ClientID, stats.ConnectionID,
			rtcStats.Timestamp, rtcStats.Type, rtcStats.ID,
			// FIXME: JSON.rawMessage はそのまま入れられなさそう
			stats.Stats,
		)
		if err != nil {
			// TODO: エラーメッセージ
			return err
		}
	}

	return nil
}
