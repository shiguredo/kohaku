package kohaku

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v4/pgxpool"
	"github.com/ory/dockertest/v3"
	"github.com/ory/dockertest/v3/docker"
)

const (
	connStr          = "postgres://%s:%s@%s/%s?sslmode=disable"
	postgresUser     = "postgres"
	postgresPassword = "password"
	postgresDB       = "kohakutest"

	port = 15890
)

var (
	pgPool *pgxpool.Pool
	server *Server

	config = &Config{
		TLSFullchainFile:    "cert/server/server.pem",
		TLSPrivkeyFile:      "cert/server/server.key",
		TLSVerifyCacertPath: "cert/client/ca.pem",
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
		Repository: "timescale/timescaledb",
		Tag:        "latest-pg15",
		Env: []string{
			"POSTGRES_PASSWORD=" + postgresPassword,
			"POSTGRES_USER=" + postgresUser,
			"POSTGRES_DB=" + postgresDB,
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

	hostAndPort := resource.GetHostPort("5432/tcp")
	dbURI := fmt.Sprintf(connStr, postgresUser, postgresPassword, hostAndPort, postgresDB)
	config.PostgresURI = dbURI

	resource.Expire(60)
	pool.MaxWait = 60 * time.Second
	if err = pool.Retry(func() error {
		config, err := pgxpool.ParseConfig(dbURI)
		if err != nil {
			return err
		}
		pgPool, err = pgxpool.ConnectConfig(context.Background(), config)
		if err != nil {
			return err
		}

		return pgPool.Ping(context.Background())
	}); err != nil {
		panic(err)
	}

	server = newTestServer(config, pgPool)

	code := m.Run()

	if err := pool.Purge(resource); err != nil {
		panic(err)
	}

	os.Exit(code)
}
