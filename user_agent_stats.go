package kohaku

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/doug-martin/goqu/v9"
	"github.com/labstack/echo/v4"
	db "github.com/shiguredo/kohaku/gen/sqlc"
)

// TODO(v): sqlc したいが厳しそう
func (s *Server) collectorUserAgentStats(c echo.Context, stats soraConnectionStats) error {
	if err := s.InsertSoraConnections(c.Request().Context(), stats); err != nil {
		return err
	}

	rtc := &rtc{
		Time:         &stats.Timestamp,
		ConnectionID: stats.ConnectionID,
	}

	for _, v := range stats.Stats {
		rtcStats := new(rtcStats)
		if err := json.Unmarshal(v, &rtcStats); err != nil {
			return err
		}

		// Type が送られてこない場合を考慮してる
		switch *rtcStats.Type {
		case "codec":
			stats := new(rtcCodecStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}

			ds := goqu.Insert("rtc_codec_stats").Rows(
				rtcCodec{
					rtc:           *rtc,
					rtcCodecStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "inbound-rtp":
			stats := new(rtcInboundRTPStreamStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}

			if stats.PerDscpPacketsReceived != nil {
				// record は一旦文字列として扱う
				perDscpPacketsReceived, err := json.Marshal(stats.PerDscpPacketsReceived)
				if err != nil {
					return err
				}
				stats.PerDscpPacketsReceived = string(perDscpPacketsReceived)
			}

			ds := goqu.Insert("rtc_inbound_rtp_stream_stats").Rows(
				rtcInboundRTPStream{
					rtc:                      *rtc,
					rtcInboundRTPStreamStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "outbound-rtp":
			stats := new(rtcOutboundRTPStreamStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}

			// record は一旦文字列として扱う
			if *stats.Kind == "video" {
				qualityLimitationDurations, err := json.Marshal(stats.QualityLimitationDurations)
				if err != nil {
					return err
				}
				stats.QualityLimitationDurations = string(qualityLimitationDurations)

				if stats.PerDscpPacketsSent != nil {
					perDscpPacketsSent, err := json.Marshal(stats.PerDscpPacketsSent)
					if err != nil {
						return err
					}
					stats.PerDscpPacketsSent = string(perDscpPacketsSent)
				}
			}

			ds := goqu.Insert("rtc_outbound_rtp_stream_stats").Rows(
				rtcOutboundRTPStream{
					rtc:                       *rtc,
					rtcOutboundRTPStreamStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "remote-inbound-rtp":
			stats := new(rtcRemoteInboundRTPStreamStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_remote_inbound_rtp_stream_stats").Rows(
				rtcRemoteInboundRTPStream{
					rtc:                            *rtc,
					rtcRemoteInboundRTPStreamStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "remote-outbound-rtp":
			stats := new(rtcRemoteOutboundRTPStreamStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_remote_outbound_rtp_stream_stats").Rows(
				rtcRemoteOutboundRTPStream{
					rtc:                             *rtc,
					rtcRemoteOutboundRTPStreamStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "media-source":
			// RTCAudioSourceStats or RTCVideoSourceStats depending on its kind.
			stats := new(rtcMediaSourceStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			switch *stats.Kind {
			case "audio":
				stats := new(rtcAudioSourceStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
				ds := goqu.Insert("rtc_audio_source_stats").Rows(
					rtcAuidoSource{
						rtc:                 *rtc,
						rtcAudioSourceStats: *stats,
					},
				)
				insertSQL, _, _ := ds.ToSQL()
				_, err := s.pool.Exec(context.Background(), insertSQL)
				if err != nil {
					return err
				}
			case "video":
				stats := new(rtcVideoSourceStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
				ds := goqu.Insert("rtc_video_source_stats").Rows(
					rtcVideoSource{
						rtc:                 *rtc,
						rtcVideoSourceStats: *stats,
					},
				)
				insertSQL, _, _ := ds.ToSQL()
				_, err := s.pool.Exec(context.Background(), insertSQL)
				if err != nil {
					return err
				}
			}
		case "csrc":
			stats := new(rtcRTPContributingSourceStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		case "peer-connection":
			stats := new(rtcPeerConnectionStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		case "data-channel":
			stats := new(rtcDataChannelStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_data_channel_stats").Rows(
				rtcDataChannel{
					rtc:                 *rtc,
					rtcDataChannelStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "stream":
			// Obsolete stats
			return nil
		case "track":
			// Obsolete stats
			return nil
		case "transceiver":
			// TODO(v): データベース書き込み
			stats := new(rtcRTPTransceiverStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		case "sender":
			// TODO(v): データベース書き込み
			stats := new(rtcMediaHandlerStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			switch *stats.Kind {
			case "audio":
				stats := new(rtcAudioSenderStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
			case "video":
				stats := new(rtcVideoSenderStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
			}
		case "receiver":
			// TODO(v): データベース書き込み
			stats := new(rtcMediaHandlerStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			switch *stats.Kind {
			case "audio":
				stats := new(rtcAudioReceiverStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
			case "video":
				stats := new(rtcVideoReceiverStats)
				if err := json.Unmarshal(v, &stats); err != nil {
					return err
				}
			}
		case "transport":
			stats := new(rtcTransportStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_transport_stats").Rows(
				rtcTransport{
					rtc:               *rtc,
					rtcTransportStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "sctp-transport":
			stats := new(rtcSctpTransportStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		case "candidate-pair":
			stats := new(rtcIceCandidatePairStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_ice_candidate_pair_stats").Rows(
				rtcIceCandidatePair{
					rtc:                      *rtc,
					rtcIceCandidatePairStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "local-candidate", "remote-candidate":
			stats := new(rtcIceCandidateStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
			ds := goqu.Insert("rtc_ice_candidate_stats").Rows(
				rtcIceCandidate{
					rtc:                  *rtc,
					rtcIceCandidateStats: *stats,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := s.pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		case "certificate":
			stats := new(rtcCertificateStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		case "ice-server":
			stats := new(rtcIceServerStats)
			if err := json.Unmarshal(v, &stats); err != nil {
				return err
			}
		default:
			return fmt.Errorf("unexpected rtcStats.Type: %s", *rtcStats.Type)
		}

	}
	return nil
}

func (s *Server) InsertSoraConnections(ctx context.Context, stats soraConnectionStats) error {
	if err := s.query.InsertSoraConnection(ctx, db.InsertSoraConnectionParams{
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
	return nil
}
