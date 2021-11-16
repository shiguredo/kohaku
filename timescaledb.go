package kohaku

import "time"

// 使ってない
type SoraNode struct {
	Timestamp time.Time `db:"timestamp"`

	Label    string `db:"label"`
	Version  string `db:"version"`
	NodeName string `db:"node_name"`
}

// 使ってない
type SoraConnection struct {
	SoraNode

	Multistream bool `db:"multistream"`
	Simulcast   bool `db:"simulcast"`
	Spotlight   bool `db:"spotlight"`

	Role         string `db:"role"`
	ChannelID    string `db:"channel_id"`
	SessionID    string `db:"session_id"`
	ClientID     string `db:"client_id"`
	ConnectionID string `db:"connection_id"`
}

type ErlangVm struct {
	Time *time.Time `db:"time"`

	Label    string `db:"sora_label"`
	Version  string `db:"sora_version"`
	NodeName string `db:"sora_node_name"`
}

type ErlangVmMemory struct {
	ErlangVm
	ErlangVmMemoryStats
}

type RTC struct {
	Time *time.Time `db:"time"`

	ConnectionID string `db:"sora_connection_id"`
}

type RTCCodec struct {
	RTC
	RTCCodecStats
}

type RTCInboundRtpStream struct {
	RTC
	RTCInboundRtpStreamStats
}

type RTCRemoteInboundRtpStream struct {
	RTC
	RTCRemoteInboundRtpStreamStats
}

type RTCOutboundRtpStream struct {
	RTC
	RTCOutboundRtpStreamStats
}

type RTCRemoteOutboundRtpStream struct {
	RTC
	RTCRemoteOutboundRtpStreamStats
}

type RTCAuidoSource struct {
	RTC
	RTCAudioSourceStats
}

type RTCVideoSource struct {
	RTC
	RTCVideoSourceStats
}

type RTCDataChannel struct {
	RTC
	RTCDataChannelStats
}

type RTCTransport struct {
	RTC
	RTCTransportStats
}

type RTCIceCandidate struct {
	RTC
	RTCIceCandidateStats
}

type RTCIceCandidatePair struct {
	RTC
	RTCIceCandidatePairStats
}
