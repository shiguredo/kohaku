package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"

	"github.com/shiguredo/kohaku"
	"golang.org/x/sync/errgroup"
)

// curl -v --http2-prior-knowledge http://localhost:8080

func main() {

	configFilePath := flag.String("c", "./kohaku.ini", "kohaku の設定ファイルへのパス(ini)")
	flag.Parse()

	config, err := kohaku.NewConfig(*configFilePath)
	if err != nil {
		log.Fatal(err)
	}

	if err := kohaku.InitLogger(config); err != nil {
		// ロガー初期化に失敗したら Fatal で終了
		log.Fatal("cannot parse config file, err=", err)
	}

	kohaku.ShowConfig(config)

	pool, err := kohaku.NewPool(config.PostgresURI)
	if err != nil {
		// TODO: エラーメッセージを修正する
		// TODO(v): zlog を利用する
		fmt.Fprintf(os.Stderr, "Unable to connect to database: %v\n", err)
		os.Exit(1)
	}
	defer pool.Close()

	server, err := kohaku.NewServer(config, pool)
	if err != nil {
		log.Fatal(err)
	}

	g, ctx := errgroup.WithContext(context.Background())

	g.Go(func() error {
		return server.Start(ctx, config)
	})

	g.Go(func() error {
		return server.StartExporter(ctx, config)
	})

	if err := g.Wait(); err != nil {
		log.Fatal(err)
	}

}
