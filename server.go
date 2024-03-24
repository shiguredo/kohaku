package kohaku

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net"
	"net/http"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/labstack/echo-contrib/prometheus"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
	zlog "github.com/rs/zerolog/log"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"

	"github.com/go-playground/validator/v10"
)

type Server struct {
	config *Config

	conn driver.Conn

	echo         *echo.Echo
	echoExporter *echo.Echo
}

func NewConnect(config *Config) (driver.Conn, error) {
	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr: []string{config.ClickHouseAddr},
		Auth: clickhouse.Auth{
			Database: config.ClickHouseDatabase,
			Username: config.ClickHouseUsername,
			Password: config.ClickHousePassword,
		},
		ClientInfo: clickhouse.ClientInfo{
			Products: []struct {
				Name    string
				Version string
			}{
				{Name: "kohaku-app", Version: "0.1"},
			},
		},
		Debug: config.ClickHouseDebug,
		Debugf: func(format string, v ...any) {
			fmt.Printf(format+"\n", v...)
		},
		// この辺りの設定は外に出すか検討
		Settings: clickhouse.Settings{
			"max_execution_time": 60,
		},
		Compression: &clickhouse.Compression{
			Method: clickhouse.CompressionLZ4,
		},
		DialTimeout:          time.Second * 30,
		MaxOpenConns:         5,
		MaxIdleConns:         5,
		ConnMaxLifetime:      time.Duration(10) * time.Minute,
		ConnOpenStrategy:     clickhouse.ConnOpenInOrder,
		BlockBufferSize:      10,
		MaxCompressionBuffer: 10240,
		// FIXME: これは開発中のみで実際は false にする
		TLS: &tls.Config{
			InsecureSkipVerify: true,
		},
	})
	if err != nil {
		return nil, err
	}
	if err := conn.Ping(context.Background()); err != nil {
		// TODO: error log
		return nil, err
	}
	return conn, nil
}

func NewServer(c *Config, conn driver.Conn) (*Server, error) {
	s := &Server{
		config: c,
		conn:   conn,
	}

	if err := s.setupEchoServer(); err != nil {
		return nil, err
	}

	zlog.Info().
		Str("addr", s.config.ListenAddr).
		Int("port", s.config.ListenPort).
		Msg("SERVER-STARTED")

	s.setupEchoExporter()

	zlog.Info().
		Bool("https", s.config.ExporterHTTPS).
		Str("addr", s.config.ExporterListenAddr).
		Int("port", s.config.ExporterListenPort).
		Msg("EXPORTER-STARTED")

	return s, nil
}

func (s *Server) setupEchoServer() error {
	e := echo.New()
	// stdout にバナー出さない
	e.HideBanner = true
	// stdout にポート番号出力しない
	e.HidePort = true

	// アドレスとして正しいことを確認する
	_, err := netip.ParseAddr(s.config.ListenAddr)
	if err != nil {
		return err
	}

	e.Server = &http.Server{
		Addr: net.JoinHostPort(s.config.ListenAddr, strconv.Itoa(s.config.ListenPort)),
	}

	// クライアント認証をするかどうかのチェック
	if s.config.TLSVerifyCacertPath != "" {
		clientCAPath := s.config.TLSVerifyCacertPath
		certPool, err := appendCerts(clientCAPath)
		if err != nil {
			zlog.Error().Err(err).Send()
			return err
		}

		tlsConfig := &tls.Config{
			ClientAuth: tls.RequireAndVerifyClientCert,
			ClientCAs:  certPool,
		}
		e.Server.TLSConfig = tlsConfig
	}

	e.Pre(middleware.RemoveTrailingSlash())

	e.Use(middleware.RequestLoggerWithConfig(middleware.RequestLoggerConfig{
		Skipper: func(c echo.Context) bool {
			// /.ok の時はログを吐き出さない
			return strings.HasPrefix(c.Request().URL.Path, "/.ok")
		},
		LogRemoteIP:      true,
		LogHost:          true,
		LogMethod:        true,
		LogURI:           true,
		LogStatus:        true,
		LogError:         true,
		LogLatency:       true,
		LogUserAgent:     true,
		LogContentLength: true,
		LogResponseSize:  true,
		LogValuesFunc: func(c echo.Context, v middleware.RequestLoggerValues) error {
			zlog.Info().
				Str("remote_ip", v.RemoteIP).
				Str("host", v.Host).
				Str("user_agent", v.UserAgent).
				Str("uri", v.URI).
				Int("status", v.Status).
				Err(v.Error).
				Str("latency", v.Latency.String()).
				Str("bytes_in", v.ContentLength).
				Int64("bytes_out", v.ResponseSize).
				Msg(v.Method)

			return nil
		},
	}))

	// e.Use(httpLogger())
	e.Use(middleware.Recover())

	validator := validator.New()
	// string をバイナリ文字列の長さとしてのチェックをできるようにする
	if err := validator.RegisterValidation("maxb", maximumNumberOfBytesFunc); err != nil {
		zlog.Error().Err(err).Send()
		return err
	}

	e.Validator = &Validator{validator: validator}

	// ヘルスチェック
	e.GET("/.ok", s.ok)

	// 統計ウェブフックを処理する
	e.POST(s.config.StatsWebhookPath, s.statsWebhook)

	s.echo = e

	return nil
}

func (s *Server) setupEchoExporter() {
	e := echo.New()
	e.HideBanner = true
	e.HidePort = true

	prom := prometheus.NewPrometheus("echo", nil)

	s.echo.Use(prom.HandlerFunc)
	prom.SetMetricsPath(e)

	s.echoExporter = e
}

func (s *Server) Start(ctx context.Context) error {
	ch := make(chan error)
	go func() {
		defer close(ch)
		if s.config.HTTPS {
			if err := s.echo.Server.ListenAndServeTLS(s.config.TLSFullchainFile, s.config.TLSPrivkeyFile); err != http.ErrServerClosed {
				ch <- err
			}
		} else {
			// HTTP/2 over TCP
			if err := s.echo.Server.ListenAndServe(); err != http.ErrServerClosed {
				ch <- err
			}
		}
	}()

	defer func() {
		if err := s.echo.Shutdown(ctx); err != nil {
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

func (s *Server) StartExporter(ctx context.Context) error {
	ch := make(chan error)
	go func() {
		var err error
		// exporter も HTTPS にしたい場合はこちら
		if s.config.ExporterHTTPS {
			err = s.echoExporter.StartTLS(
				net.JoinHostPort(s.config.ExporterListenAddr, strconv.Itoa(s.config.ExporterListenPort)),
				s.config.TLSFullchainFile, s.config.TLSPrivkeyFile,
			)
		} else {
			// TODO: StartTLS 可能にする?
			err = s.echoExporter.Start(
				net.JoinHostPort(s.config.ExporterListenAddr, strconv.Itoa(s.config.ExporterListenPort)),
			)
		}

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
