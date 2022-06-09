package kohaku

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io/ioutil"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/jackc/pgx/v4/pgxpool"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	zlog "github.com/rs/zerolog/log"
	db "github.com/shiguredo/kohaku/gen/sqlc"

	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"

	"github.com/go-playground/validator/v10"
)

type Server struct {
	config *KohakuConfig
	pool   *pgxpool.Pool
	query  *db.Queries
	echo   *echo.Echo
	http.Server
}

func NewServer(c *KohakuConfig, pool *pgxpool.Pool) *Server {
	e := echo.New()

	validator := validator.New()
	if err := validator.RegisterValidation("maxb", maximumNumberOfBytesFunc); err != nil {
		zlog.Error().Err(err).Send()
		panic(err)
	}

	e.Validator = &Validator{validator: validator}

	// e.Use(httpLogger())
	e.Use(middleware.Recover())

	// TODO(v): こいつ自身の統計情報を /stats でとれた方がいい

	h2s := &http2.Server{
		MaxConcurrentStreams: c.HTTP2MaxConcurrentStreams,
		MaxReadFrameSize:     c.HTTP2MaxReadFrameSize,
		IdleTimeout:          time.Duration(c.HTTP2IdleTimeout) * time.Second,
	}

	s := &Server{
		config: c,
		pool:   pool,
		query:  db.New(pool),
		Server: http.Server{
			Addr:    fmt.Sprintf(":%d", c.CollectorPort),
			Handler: h2c.NewHandler(e, h2s),
		},
		echo: e,
	}

	http2H2c := c.HTTP2H2c
	if !http2H2c {
		if c.HTTP2VerifyCacertPath != "" {
			clientCAPath := c.HTTP2VerifyCacertPath
			certPool, err := appendCerts(clientCAPath)
			if err != nil {
				zlog.Error().Err(err).Send()
				panic(err)
			}

			tlsConfig := &tls.Config{
				ClientAuth: tls.RequireAndVerifyClientCert,
				ClientCAs:  certPool,
			}
			s.Server.TLSConfig = tlsConfig
		}
	}

	// 統計情報を突っ込むところ
	e.POST("/collector", s.collector, validateHTTPVersion)
	// ヘルスチェック
	e.POST("/health", s.health)

	return s
}

func (s *Server) Start(c *KohakuConfig) error {
	http2H2c := c.HTTP2H2c

	if http2H2c {
		if err := s.ListenAndServe(); err != http.ErrServerClosed {
			return err
		}
	} else {
		http2FullchainFile := c.HTTP2FullchainFile
		http2PrivkeyFile := c.HTTP2PrivkeyFile

		if _, err := os.Stat(http2FullchainFile); err != nil {
			return fmt.Errorf("http2FullchainFile error: %s", err)
		}

		if _, err := os.Stat(http2PrivkeyFile); err != nil {
			return fmt.Errorf("http2PrivkeyFile error: %s", err)
		}

		if err := s.ListenAndServeTLS(http2FullchainFile, http2PrivkeyFile); err != http.ErrServerClosed {
			return err
		}
	}

	return nil
}

func validateHTTPVersion(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		// prior knowledge ではない場合
		if upgrade, ok := c.Request().Header["Upgrade"]; ok && upgrade[0] == "h2c" {
			return echo.NewHTTPError(http.StatusBadRequest)
		}

		if c.Request().Proto != "HTTP/2.0" {
			err := fmt.Errorf("http version not supported: %s", c.Request().Proto)
			return echo.NewHTTPError(http.StatusBadRequest, err.Error())
		}

		return next(c)
	}
}

// func httpLogger() gin.HandlerFunc {
// 	return func(c *gin.Context) {
// 		c.Next()
//
// 		event := logEvent(c.Writer.Status())
//
// 		req := c.Request
//
// 		event.
// 			Int("status", c.Writer.Status()).
// 			Str("address", req.RemoteAddr).
// 			Str("method", req.Method).
// 			Str("path", req.URL.Path).
// 			Int64("len", req.ContentLength).
// 			Msg("")
// 	}
// }

// func logEvent(status int) *zerolog.Event {
// 	var event *zerolog.Event
//
// 	switch status / 100 {
// 	case 5:
// 		event = zlog.Error()
// 	case 4:
// 		event = zlog.Warn()
// 	default:
// 		event = zlog.Info()
// 	}
//
// 	return event
// }

func appendCerts(clientCAPath string) (*x509.CertPool, error) {
	certPool := x509.NewCertPool()
	fi, err := os.Stat(clientCAPath)
	if err != nil {
		return nil, err
	}
	if fi.IsDir() {
		files, err := ioutil.ReadDir(clientCAPath)
		if err != nil {
			return nil, err
		}
		for _, f := range files {
			clientCA, err := ioutil.ReadFile(filepath.Join(clientCAPath, f.Name()))
			if err != nil {
				return nil, err
			}
			ok := certPool.AppendCertsFromPEM(clientCA)
			if !ok {
				return nil, fmt.Errorf("failed to append certificates: %s", filepath.Join(clientCAPath, f.Name()))
			}
		}
	} else {
		clientCA, err := ioutil.ReadFile(clientCAPath)
		if err != nil {
			return nil, err
		}
		ok := certPool.AppendCertsFromPEM(clientCA)
		if !ok {
			return nil, fmt.Errorf("failed to append certificates: %s", clientCAPath)
		}
	}
	return certPool, nil
}

type Validator struct {
	validator *validator.Validate
}

func (v *Validator) Validate(i interface{}) error {
	if err := v.validator.Struct(i); err != nil {
		return echo.NewHTTPError(http.StatusBadRequest, err.Error())
	}
	return nil
}
