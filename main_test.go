package kohaku

import (
	"context"
	"crypto/tls"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/ory/dockertest/v3"
	"github.com/ory/dockertest/v3/docker"
)

const (
	connStr            = "%s:%s"
	clickhouseUser     = "default"
	clickhousePassword = "default"
	clickhouseDB       = "default"

	port = 15890
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
		ListenPort:          port,
	}
)

func TestMain(m *testing.M) {
	pool, err := dockertest.NewPool("")
	if err != nil {
		panic(err)
	}

	pwd, _ := os.Getwd()

	resource, err := pool.RunWithOptions(&dockertest.RunOptions{
		Repository: "clickhouse/clickhouse-server",
		Tag:        "latest",
		Env: []string{
			"CLICKHOUSE_DB=" + clickhouseDB,
			"CLICKHOUSE_USER=" + clickhouseDB,
			"CLICKHOUSE_PASSWORD=" + clickhouseDB,
			"listen_addresses = '*'",
		},
		Mounts: []string{
			pwd + "/db/schema.sql:/docker-entrypoint-initdb.d/schema.sql",
		},
	}, func(config *docker.HostConfig) {
		config.AutoRemove = true
		config.RestartPolicy = docker.RestartPolicy{Name: "no"}
	})
	if err != nil {
		panic(err)
	}

	config.ClickHouseAddr = resource.GetHostPort("9000/tcp")

	resource.Expire(60)
	pool.MaxWait = 60 * time.Second
	if err = pool.Retry(func() error {
		conn, _ = clickhouse.Open(&clickhouse.Options{
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
					{Name: "kohaku-test", Version: "0.1"},
				},
			},
			// FIXME: これは開発中のみで実際は false にする
			TLS: &tls.Config{
				InsecureSkipVerify: true,
			},
		})
		return conn.Ping(context.Background())
	}); err != nil {
		panic(err)
	}

	server = newTestServer(config, conn)

	code := m.Run()

	if err := pool.Purge(resource); err != nil {
		panic(err)
	}

	os.Exit(code)
}

func newTestServer(c *Config, conn driver.Conn) *Server {
	s := &Server{
		config: c,
		conn:   conn,
	}

	s.setupEchoServer()

	return s
}
