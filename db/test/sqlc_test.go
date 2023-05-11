package sqlc

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v4/pgxpool"
	"github.com/ory/dockertest/v3"
	"github.com/ory/dockertest/v3/docker"

	db "github.com/shiguredo/kohaku/gen/sqlc"
	base32 "github.com/shogo82148/go-clockwork-base32"
)

// query テスト用
var q *db.Queries

// transaction テスト用
var pp *pgxpool.Pool

func base32edUUIDv4() string {
	id := uuid.New()
	binaryUUID, _ := id.MarshalBinary()
	return base32.NewEncoding().EncodeToString(binaryUUID)
}

func TestMain(m *testing.M) {
	pool, err := dockertest.NewPool("")
	pool.MaxWait = 10 * time.Second
	if err != nil {
		log.Fatalf("Could not connect to docker: %s", err)
	}

	// https://qiita.com/daijinload/items/f6cc602d64f8397faea5
	_, b, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(b), "../../../")

	containerName := "test"

	// ./db/schema.sql のパスを取得する
	schemaPath := filepath.Join(root, "kohaku", "db", "schema.sql")
	schemaFilename := filepath.Base(schemaPath)
	mountFiles := []string{}
	mountPath := fmt.Sprintf("%s:/docker-entrypoint-initdb.d/%s", schemaPath, schemaFilename)
	mountFiles = append(mountFiles, mountPath)
	runOptions := &dockertest.RunOptions{
		Repository: "timescale/timescaledb",
		Tag:        "latest-pg15",
		Env: []string{
			"POSTGRES_USER=postgres",
			"POSTGRES_PASSWORD=password",
			"POSTGRES_DB=dockertest",
			"listen_addresses='*'",
		},
		Mounts: mountFiles,
		Name:   containerName,
	}

	resource, err := pool.RunWithOptions(runOptions,
		func(config *docker.HostConfig) {
			config.AutoRemove = true
			config.RestartPolicy = docker.RestartPolicy{
				Name: "no",
			}
		},
	)
	if err != nil {
		log.Fatalf("Could not start resource: %s", err)
	}

	done := make(chan struct{})
	defer close(done)
	ch := make(chan struct{})
	defer close(ch)

	go func() {
		s := make(chan os.Signal, 1)
		signal.Notify(s, syscall.SIGINT)

		var d bool
		select {
		case <-done:
			d = true
		case <-s:
		}

		if err := pool.Purge(resource); err != nil {
			log.Fatalf("Could not purge resource: %s", err)
		}

		if d {
			ch <- struct{}{}
		} else {
			os.Exit(0)
		}
	}()

	port := resource.GetPort("5432/tcp")
	databaseURL := fmt.Sprintf("postgres://postgres:password@127.0.0.1:%s/dockertest?sslmode=disable", port)

	if err := pool.Retry(func() error {
		config, err := pgxpool.ParseConfig(databaseURL)
		if err != nil {
			return err
		}

		p, err := pgxpool.ConnectConfig(context.Background(), config)
		if err != nil {
			return err
		}

		if err := p.Ping(context.Background()); err != nil {
			return err
		}

		q = db.New(p)
		pp = p
		return nil
	}); err != nil {
		log.Fatalf("Could not connect to database: %s", err)
	}

	code := m.Run()

	done <- struct{}{}
	<-ch

	os.Exit(code)
}
