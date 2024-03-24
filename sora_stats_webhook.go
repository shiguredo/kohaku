package kohaku

import (
	"net/http"

	"github.com/labstack/echo/v4"
	zlog "github.com/rs/zerolog/log"
)

// ログレベル、ログメッセージを変更する
func (s *Server) statsWebhook(c echo.Context) error {
	t := c.Request().Header.Get("sora-stats-webhook-type")
	switch t {
	case "connection.rtc":
		stats := new(soraConnectionStats)
		if err := c.Bind(stats); err != nil {
			zlog.Warn().Err(err).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := c.Validate(stats); err != nil {
			zlog.Warn().Err(err).Bool("simulcast", *stats.Simulcast).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := s.rtcStats(c, *stats); err != nil {
			zlog.Warn().Err(err).Str("type", t).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		return c.NoContent(http.StatusNoContent)
	default:
		zlog.Warn().Str("type", t).Msg("UNEXPECTED-TYPE")
		return echo.NewHTTPError(http.StatusBadRequest)
	}
}
