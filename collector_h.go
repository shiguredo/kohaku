package kohaku

import (
	"net/http"

	"github.com/labstack/echo/v4"
	zlog "github.com/rs/zerolog/log"
)

// ログレベル、ログメッセージを変更する
func (s *Server) collector(c echo.Context) error {
	if c.Request().ProtoMajor != 2 {
		zlog.Error().
			Str("Proto", c.Request().Proto).
			Int("ProtoMajor", c.Request().ProtoMajor).
			Int("ProtoMinor", c.Request().ProtoMinor).
			Msg("PROTOCOL-VIOLATION")
		return echo.NewHTTPError(http.StatusBadRequest)
	}

	t := c.Request().Header.Get("x-sora-stats-exporter-type")
	switch t {
	case "connection.user-agent":
		stats := new(soraConnectionStats)
		if err := c.Bind(stats); err != nil {
			zlog.Warn().Err(err).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := c.Validate(stats); err != nil {
			zlog.Warn().Err(err).Bool("simulcast", *stats.Simulcast).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := s.collectorUserAgentStats(c, *stats); err != nil {
			zlog.Warn().Err(err).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		return c.NoContent(http.StatusNoContent)
	default:
		zlog.Warn().Str("type", t).Msg("UNEXPECTED-TYPE")
		return echo.NewHTTPError(http.StatusBadRequest)
	}
}
