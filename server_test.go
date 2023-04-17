package kohaku

import (
	"context"
	"crypto/tls"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/http2"

	"github.com/jackc/pgx/v4/pgxpool"
	db "github.com/shiguredo/kohaku/gen/sqlc"
	"github.com/stretchr/testify/assert"
)

type CertPair struct {
	CertificateFile string
	KeyFile         string
}

const (
	port = 15890

	// millisecond
	waitingTime = 100
)

var (
	url = fmt.Sprintf("https://localhost:%d/.ok", port)

	config = &Config{
		TLSFullchainFile:    "cert/server/server.pem",
		TLSPrivkeyFile:      "cert/server/server.key",
		TLSVerifyCacertPath: "cert/client/ca.pem",
		ListenPort:          port,
	}

	certPair = &CertPair{
		"cert/client/user.pem",
		"cert/client/user.key",
	}
)

func newTestServer(c *Config, pool *pgxpool.Pool) *Server {
	s := &Server{
		config: c,
		pool:   pool,
		query:  db.New(pool),
	}

	s.setupEchoServer()

	return s
}

func newTestClient(nextProto string, c *CertPair) (*http.Client, error) {
	var client http.Client

	cert, err := tls.LoadX509KeyPair(c.CertificateFile, c.KeyFile)
	if err != nil {
		return nil, err
	}

	var certs []tls.Certificate
	certs = append(certs, cert)
	tlsConfig := &tls.Config{
		MaxVersion:         tls.VersionTLS13,
		Certificates:       certs,
		InsecureSkipVerify: true,
		NextProtos:         []string{nextProto},
	}

	if nextProto == "h2" {
		client.Transport = &http2.Transport{
			TLSClientConfig: tlsConfig,
		}
	} else {
		client.Transport = &http.Transport{
			TLSClientConfig: tlsConfig,
		}
	}

	return &client, nil
}

func TestMutualTLS(t *testing.T) {
	s := newTestServer(config, pgPool)
	go (func() {
		s.Start(context.Background(), config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	client, err := newTestClient("http/1.1", certPair)
	if err != nil {
		panic(err)
	}

	ctx := context.Background()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(""))
	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()

	assert.Equal(t, "HTTP/1.1", resp.Proto)
	assert.Equal(t, http.StatusNoContent, resp.StatusCode)
}

func TestInvalidClientCertificate(t *testing.T) {
	s := newTestServer(config, pgPool)
	go (func() {
		s.Start(context.Background(), config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	invalidCertPair := &CertPair{
		"cert/client/invalid.pem",
		"cert/client/invalid.key",
	}
	client, err := newTestClient("http/1.1", invalidCertPair)
	if err != nil {
		panic(err)
	}

	ctx := context.Background()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(""))
	_, err = client.Do(req)
	assert.NotNil(t, err)
}

func TestH2(t *testing.T) {
	s := newTestServer(config, pgPool)
	go (func() {
		s.Start(context.Background(), config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	client, err := newTestClient("h2", certPair)
	if err != nil {
		panic(err)
	}

	ctx := context.Background()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(""))
	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()

	assert.Equal(t, "HTTP/2.0", resp.Proto)
	assert.Equal(t, http.StatusNoContent, resp.StatusCode)
}
