package kohaku

import (
	"context"
	"net/http"

	"github.com/labstack/echo/v4"
	zlog "github.com/rs/zerolog/log"
)

// TODO: ログレベル、ログメッセージを変更する
func (s *Server) health(c echo.Context) error {
	if err := s.pool.Ping(context.Background()); err != nil {
		zlog.Error().Err(err).Send()
		return echo.NewHTTPError(http.StatusInternalServerError, err.Error())
	}

	return c.NoContent(http.StatusNoContent)
}
