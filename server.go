package kohaku

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v4/pgxpool"
	"github.com/labstack/echo-contrib/prometheus"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	zlog "github.com/rs/zerolog/log"
	db "github.com/shiguredo/kohaku/gen/sqlc"

	"golang.org/x/net/http2"

	"github.com/go-playground/validator/v10"
)

type Server struct {
	config *Config

	pool  *pgxpool.Pool
	query *db.Queries

	echo         *echo.Echo
	echoExporter *echo.Echo

	http.Server
}

func NewPool(connStr string) (*pgxpool.Pool, error) {
	config, err := pgxpool.ParseConfig(connStr)
	if err != nil {
		return nil, err
	}

	pool, err := pgxpool.ConnectConfig(context.Background(), config)
	if err != nil {
		return nil, err
	}

	if err := pool.Ping(context.Background()); err != nil {
		return nil, err
	}

	return pool, nil
}

func NewServer(c *Config, pool *pgxpool.Pool) (*Server, error) {
	h2s := &http2.Server{
		MaxConcurrentStreams: c.HTTP2MaxConcurrentStreams,
		MaxReadFrameSize:     c.HTTP2MaxReadFrameSize,
		IdleTimeout:          time.Duration(c.HTTP2IdleTimeout) * time.Second,
	}

	e := echo.New()

	s := &Server{
		config: c,
		pool:   pool,
		query:  db.New(pool),
		Server: http.Server{
			Addr:    net.JoinHostPort("", strconv.Itoa(c.ListenPort)),
			Handler: e,
		},
		echo: e,
	}

	// クライアント認証をするかどうかのチェック
	if c.HTTP2VerifyCacertPath != "" {
		clientCAPath := c.HTTP2VerifyCacertPath
		certPool, err := appendCerts(clientCAPath)
		if err != nil {
			zlog.Error().Err(err).Send()
			return nil, err
		}

		tlsConfig := &tls.Config{
			ClientAuth: tls.RequireAndVerifyClientCert,
			ClientCAs:  certPool,
		}
		s.Server.TLSConfig = tlsConfig
	}

	if err := http2.ConfigureServer(&s.Server, h2s); err != nil {
		return nil, err
	}

	e.Pre(middleware.RemoveTrailingSlash())

	e.Use(middleware.LoggerWithConfig(middleware.LoggerConfig{
		Skipper: func(c echo.Context) bool {
			// /.ok の時はログを吐き出さない
			return strings.HasPrefix(c.Request().URL.Path, "/.ok")
		},
	}))

	// e.Use(httpLogger())
	e.Use(middleware.Recover())

	validator := validator.New()
	// string をバイナリ文字列の長さとしてのチェックをできるようにする
	if err := validator.RegisterValidation("maxb", maximumNumberOfBytesFunc); err != nil {
		zlog.Error().Err(err).Send()
		return nil, err
	}

	e.Validator = &Validator{validator: validator}

	// ヘルスチェック
	e.POST("/.ok", s.ok)

	// 統計情報を突っ込むところ
	e.POST("/collector", s.collector)

	echoExporter := echo.New()
	echoExporter.HideBanner = true
	prom := prometheus.NewPrometheus("echo", nil)

	e.Use(prom.HandlerFunc)
	prom.SetMetricsPath(echoExporter)

	s.echo = e
	// exporter
	s.echoExporter = echoExporter

	return s, nil
}

func (s *Server) Start(ctx context.Context, c *Config) error {
	http2FullchainFile := s.config.HTTP2FullchainFile
	http2PrivkeyFile := s.config.HTTP2PrivkeyFile

	if _, err := os.Stat(http2FullchainFile); err != nil {
		return fmt.Errorf("http2FullchainFile error: %s", err)
	}

	if _, err := os.Stat(http2PrivkeyFile); err != nil {
		return fmt.Errorf("http2PrivkeyFile error: %s", err)
	}

	ch := make(chan error)
	go func() {
		defer close(ch)
		if err := s.ListenAndServeTLS(http2FullchainFile, http2PrivkeyFile); err != http.ErrServerClosed {
			ch <- err
		}
	}()

	defer func() {
		if err := s.Shutdown(ctx); err != nil {
			zlog.Error().Err(err).Send()
		}
	}()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-ch:
		return err
	}
}

func (s *Server) StartExporter(ctx context.Context, config *Config) error {
	ch := make(chan error)
	go func() {
		err := s.echoExporter.Start(net.JoinHostPort(config.ListenExporterAddr, strconv.Itoa(config.ListenExporterPort)))
		if err != nil {
			ch <- err
		}
	}()

	defer func() {
		if err := s.echoExporter.Shutdown(ctx); err != nil {
			zlog.Error().Err(err).Send()
		}
	}()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-ch:
		return err
	}
}

func appendCerts(clientCAPath string) (*x509.CertPool, error) {
	certPool := x509.NewCertPool()
	fi, err := os.Stat(clientCAPath)
	if err != nil {
		return nil, err
	}
	if fi.IsDir() {
		files, err := os.ReadDir(clientCAPath)
		if err != nil {
			return nil, err
		}
		for _, f := range files {
			clientCAPath := filepath.Join(clientCAPath, f.Name())
			if err := appendCertsFromPEM(certPool, clientCAPath); err != nil {
				return nil, err
			}
		}
	} else {
		if err := appendCertsFromPEM(certPool, clientCAPath); err != nil {
			return nil, err
		}
	}
	return certPool, nil
}

func appendCertsFromPEM(certPool *x509.CertPool, filepath string) error {
	clientCA, err := os.ReadFile(filepath)
	if err != nil {
		return err
	}
	ok := certPool.AppendCertsFromPEM(clientCA)
	if !ok {
		return fmt.Errorf("failed to append certificates: %s", filepath)
	}
	return nil
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
