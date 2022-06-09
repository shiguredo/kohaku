package kohaku

import (
	"encoding/json"
	"strconv"
	"time"

	"github.com/go-playground/validator/v10"
	zlog "github.com/rs/zerolog/log"
)

// TODO: validator 処理の追加

type soraStats struct {
	Type string `json:"type" validate:"required"`

	Timestamp time.Time `json:"timestamp" validate:"required"`

	Label    string `json:"label" validate:"required"`
	Version  string `json:"version" validate:"required"`
	NodeName string `json:"node_name" validate:"required"`
}

// type: connection.user-agent / type: connection.sora
type soraConnectionStats struct {
	soraStats

	Multistream *bool `json:"multistream" validate:"required"`
	Simulcast   *bool `json:"simulcast" validate:"required"`
	Spotlight   *bool `json:"spotlight" validate:"required"`

	Role         string `json:"role" validate:"required,len=8"`
	ChannelID    string `json:"channel_id" validate:"required,maxb=255"`
	SessionID    string `json:"session_id" validate:"required,len=26"`
	ClientID     string `json:"client_id" validate:"required,maxb=255"`
	ConnectionID string `json:"connection_id" validate:"required,len=26"`

	Stats []json.RawMessage `json:"stats" validate:"required"`
}

func maximumNumberOfBytesFunc(fl validator.FieldLevel) bool {
	param := fl.Param()

	// 255 バイトまで指定可能
	length, err := strconv.ParseUint(param, 10, 8)
	if err != nil {
		zlog.Error().Err(err).Send()
		panic(err)
	}

	return uint64(fl.Field().Len()) <= length
}
