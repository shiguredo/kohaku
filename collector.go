package kohaku

import (
	"net/http"

	"github.com/labstack/echo/v4"
	zlog "github.com/rs/zerolog/log"
)

// TODO: ログレベル、ログメッセージを変更する
func (s *Server) collector(c echo.Context) error {
	t := c.Request().Header.Get("x-sora-stats-exporter-type")
	switch t {
	case "connection.user-agent":
		stats := new(soraConnectionStats)
		if err := c.Bind(stats); err != nil {
			zlog.Debug().Str("type", t).Err(err).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := c.Validate(stats); err != nil {
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		if err := s.collectorUserAgentStats(c, *stats); err != nil {
			zlog.Warn().Str("type", t).Err(err).Send()
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}
		return c.NoContent(http.StatusNoContent)
	default:
		zlog.Warn().Str("type", t).Msgf("UNEXPECTED-TYPE")
		return echo.NewHTTPError(http.StatusBadRequest)
	}
}
