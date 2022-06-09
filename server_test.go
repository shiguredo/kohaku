package kohaku

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/http2"

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
	url = fmt.Sprintf("https://localhost:%d/health", port)

	config = &KohakuConfig{
		HTTP2H2c:              false,
		HTTP2FullchainFile:    "cert/server/server.pem",
		HTTP2PrivkeyFile:      "cert/server/server.key",
		HTTP2VerifyCacertPath: "cert/client/ca.pem",
		CollectorPort:         port,
	}

	certPair = &CertPair{
		"cert/client/user.pem",
		"cert/client/user.key",
	}
)

func NewClient(nextProto string, c *CertPair) (*http.Client, error) {
	var client http.Client

	if nextProto == "h2c" {
		client.Transport = &http2.Transport{
			AllowHTTP: true,
			DialTLS: func(network, addr string, cfg *tls.Config) (net.Conn, error) {
				return net.Dial(network, addr)
			},
		}
		return &client, nil
	}

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
	s := NewServer(config, pgPool)
	go (func() {
		s.Start(config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	client, err := NewClient("http/1.1", certPair)
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
	s := NewServer(config, pgPool)
	go (func() {
		s.Start(config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	invalidCertPair := &CertPair{
		"cert/client/invalid.pem",
		"cert/client/invalid.key",
	}
	client, err := NewClient("http/1.1", invalidCertPair)
	if err != nil {
		panic(err)
	}

	ctx := context.Background()
	req, _ := http.NewRequestWithContext(ctx, "POST", url, strings.NewReader(""))
	_, err = client.Do(req)
	assert.NotNil(t, err)
}

func TestH2(t *testing.T) {
	s := NewServer(config, pgPool)
	go (func() {
		s.Start(config)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	client, err := NewClient("h2", certPair)
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

func TestH2C(t *testing.T) {
	h2cConfig := &KohakuConfig{
		HTTP2H2c:      true,
		CollectorPort: 25890,
	}
	s := NewServer(h2cConfig, pgPool)
	go (func() {
		s.Start(h2cConfig)
	})()

	time.Sleep(waitingTime * time.Millisecond)

	// Setup
	client, err := NewClient("h2c", nil)
	if err != nil {
		panic(err)
	}

	url := fmt.Sprintf("http://localhost:%d/health", 25890)

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
