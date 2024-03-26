package kohaku

import (
	"context"
	"log"
	"os"
	"testing"

	ch "github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/testcontainers/testcontainers-go"

	"github.com/testcontainers/testcontainers-go/modules/clickhouse"
)

const (
	connStr            = "%s:%s"
	clickhouseUser     = "default"
	clickhousePassword = "default"
	clickhouseDB       = "default"
)

var (
	conn   driver.Conn
	server *Server

	config = &Config{
		TLSFullchainFile:    "cert/server/server.pem",
		TLSPrivkeyFile:      "cert/server/server.key",
		TLSVerifyCacertPath: "cert/client/ca.pem",
		HTTPS:               true,
		ListenAddr:          "0.0.0.0",
		ListenPort:          15890,
	}
)

// https://github.com/rilldata/rill/blob/65e7cb07060deab31ef1fef32e345d64d9611cb4/runtime/queries/metricsview_toplist_test.go#L25

func TestMain(m *testing.M) {
	ctx := context.Background()

	clickHouseContainer, err := clickhouse.RunContainer(ctx,
		testcontainers.WithImage("clickhouse/clickhouse-server:latest"),
		clickhouse.WithUsername(clickhouseUser),
		clickhouse.WithPassword(clickhousePassword),
		clickhouse.WithDatabase(clickhouseDB),
	)
	if err != nil {
		log.Fatalf("failed to start container: %s", err)
	}

	defer func() {
		if err := clickHouseContainer.Terminate(ctx); err != nil {
			log.Fatalf("failed to terminate container: %s", err)
		}
	}()

	// state, err := clickHouseContainer.State(ctx)
	// if err != nil {
	// 	log.Fatalf("failed to get container state: %s", err) // nolint:gocritic
	// }

	host, err := clickHouseContainer.Host(ctx)
	if err != nil {
		log.Fatalf("failed to get container host: %s", err)
	}
	port, err := clickHouseContainer.MappedPort(ctx, "9000")
	if err != nil {
		log.Fatalf("failed to get container port: %s", err)
	}

	conn := ch.OpenDB(&ch.Options{
		Addr: []string{host + ":" + port.Port()},
		Auth: ch.Auth{
			Username: clickhouseUser,
			Password: clickhousePassword,
			Database: clickhouseDB,
		},
	})

	err = conn.Ping()
	if err != nil {
		log.Fatalf("failed to ping ClickHouse: %s", err)
	}

	// ここでテストを実行する

	code := m.Run()

	// 処理が終わったら、deferによってコンテナが自動的に停止して削除される

	os.Exit(code)
}

func TestDummy(t *testing.T) {
	log.Println("ダミーのテストが実行されました。")
}

func newTestServer(c *Config, conn driver.Conn) *Server {
	s := &Server{
		config: c,
		conn:   conn,
	}

	s.setupEchoServer()

	return s
}
