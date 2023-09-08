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

func main() {
	// /bin/kohaku -V
	showVersion := flag.Bool("V", false, "バージョン")

	// /bin/kohaku -C ./config.ini
	configFilePath := flag.String("C", "./config.ini", "設定ファイルへのパス")
	flag.Parse()

	if *showVersion {
		fmt.Printf("WebRTC Stats Collector Kohaku version %s\n", kohaku.Version)
		return
	}

	config, err := kohaku.NewConfig(*configFilePath)
	if err != nil {
		// パースに失敗した場合 Fatal で終了
		log.Fatal("cannot parse config file, err=", err)
	}

	// ロガー初期化
	if err := kohaku.InitLogger(config); err != nil {
		// ロガー初期化に失敗したら Fatal で終了
		log.Fatal("cannot parse config file, err=", err)
	}

	kohaku.ShowConfig(config)

	pool, err := kohaku.NewPool(config.PostgresURI)
	if err != nil {
		// TODO: エラーメッセージを修正する
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
		return server.Start(ctx)
	})

	g.Go(func() error {
		return server.StartExporter(ctx)
	})

	if err := g.Wait(); err != nil {
		log.Fatal(err)
	}

}
