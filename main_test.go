package kohaku

import (
	"context"
	"fmt"
	"log"
	"os"
	"testing"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
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

func TestMain(m *testing.M) {
	ctx := context.Background()

	// ClickHouseコンテナのリクエストを作成
	req := testcontainers.ContainerRequest{
		Image:        "clickhouse/clickhouse-server:latest",
		ExposedPorts: []string{"9000/tcp"},
		Env: map[string]string{
			"CLICKHOUSE_DB":       clickhouseDB,
			"CLICKHOUSE_USER":     clickhouseUser,
			"CLICKHOUSE_PASSWORD": clickhousePassword,
		},
		WaitingFor: wait.ForListeningPort("9000/tcp"),
	}

	// コンテナを起動
	clickhouseContainer, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		log.Fatalf("ClickHouseコンテナの起動に失敗しました: %s", err)
	}

	// 終了時にコンテナを停止して削除する
	defer clickhouseContainer.Terminate(ctx)

	// コンテナの情報を取得（例：IPアドレス）
	ip, err := clickhouseContainer.Host(ctx)
	if err != nil {
		log.Fatalf("コンテナのホスト名の取得に失敗しました: %s", err)
	}

	port, err := clickhouseContainer.MappedPort(ctx, "9000")
	if err != nil {
		log.Fatalf("マッピングされたポートの取得に失敗しました: %s", err)
	}

	log.Printf("ClickHouseサーバーが起動しました: %s:%s", ip, port.Port())

	// ip + ":" + port.Port() を表示
	fmt.Print("ClickHouseサーバーのアドレス: ", ip+":"+port.Port(), "\n")

	conn, err = clickhouse.Open(&clickhouse.Options{
		Addr: []string{ip + ":" + port.Port()},
		Auth: clickhouse.Auth{
			Database: "default",
			Username: "default",
			Password: "default",
		},
	})
	if err != nil {
		panic(err)
	}

	err = conn.Ping(ctx)
	if err != nil {
		panic(err)
	}

	// ここでテストを実行する

	code := m.Run()

	conn.Close()

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
