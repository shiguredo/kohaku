package kohaku

import (
	"fmt"

	zlog "github.com/rs/zerolog/log"
	"gopkg.in/ini.v1"
)

const (
	DefaultLogDir  = "."
	DefaultLogName = "kohaku.jsonl"

	// megabytes
	DefaultLogRotateMaxSize    = 200
	DefaultLogRotateMaxBackups = 7
	// days
	DefaultLogRotateMaxAge = 30

	DefaultExporterListenAddr = "0.0.0.0"
	DefaultExporterListenPort = 5891
)

type Config struct {
	Debug bool `ini:"debug"`

	LogDir    string `ini:"log_dir"`
	LogName   string `ini:"log_name"`
	LogStdout bool   `ini:"log_stdout"`

	// MB
	LogRotateMaxSize    int `ini:"log_rotate_max_size"`
	LogRotateMaxBackups int `ini:"log_rotate_max_backups"`
	// Days
	LogRotateMaxAge int `ini:"log_rotate_max_age"`

	HTTPS      bool   `ini:"https"`
	ListenAddr string `ini:"listen_addr"`
	ListenPort int    `ini:"listen_port"`

	// exporter で https を使うかどうか
	// tailscale などを使う場合は不要
	ExporterHTTPS      bool   `ini:"exporter_https"`
	ExporterListenAddr string `ini:"exporter_listen_addr"`
	ExporterListenPort int    `ini:"exporter_listen_port"`

	PostgresURI        string `ini:"postgres_uri"`
	PostgresCACertFile string `ini:"postgres_ca_cert_file"`

	TLSFullchainFile    string `ini:"tls_fullchain_file"`
	TLSPrivkeyFile      string `ini:"tls_privkey_file"`
	TLSVerifyCacertPath string `ini:"tls_verify_cacert_path"`

	HTTP2MaxConcurrentStreams uint32 `ini:"http2_max_concurrent_streams"`
	HTTP2MaxReadFrameSize     uint32 `ini:"http2_max_read_frame_size"`
	HTTP2IdleTimeout          uint32 `ini:"http2_idle_timeout"`
}

func NewConfig(configFilePath string) (*Config, error) {
	config := new(Config)

	iniConfig, err := ini.InsensitiveLoad(configFilePath)
	if err != nil {
		return nil, err
	}

	if err := iniConfig.StrictMapTo(config); err != nil {
		return nil, err
	}

	setDefaultsConfig(config)

	if err := validateConfig(config); err != nil {
		return nil, err
	}

	return config, nil
}

func setDefaultsConfig(config *Config) {
	if config.LogDir == "" {
		config.LogDir = DefaultLogDir
	}

	if config.LogName == "" {
		config.LogDir = DefaultLogName
	}

	if config.LogRotateMaxSize == 0 {
		config.LogRotateMaxSize = DefaultLogRotateMaxSize
	}

	if config.LogRotateMaxBackups == 0 {
		config.LogRotateMaxBackups = DefaultLogRotateMaxBackups
	}

	if config.LogRotateMaxAge == 0 {
		config.LogRotateMaxAge = DefaultLogRotateMaxAge
	}

	if config.ExporterListenAddr == "" {
		config.ExporterListenAddr = DefaultExporterListenAddr
	}

	if config.ExporterListenPort == 0 {
		config.ExporterListenPort = DefaultExporterListenPort
	}
}

func validateConfig(config *Config) error {
	if config.HTTPS || config.ExporterHTTPS {
		if config.TLSFullchainFile == "" {
			return fmt.Errorf("tls_fullchain_file is required")
		}

		if config.TLSPrivkeyFile == "" {
			return fmt.Errorf("tls_privkey_file is required")
		}
	}

	return nil
}

func ShowConfig(config *Config) {

	zlog.Info().Bool("debug", config.Debug).Msg("CONF")

	zlog.Info().Str("log_dir", config.LogDir).Msg("CONF")
	zlog.Info().Str("log_name", config.LogName).Msg("CONF")
	zlog.Info().Bool("log_stdout", config.LogStdout).Msg("CONF")

	zlog.Info().Int("log_rotate_max_size", config.LogRotateMaxSize).Msg("CONF")
	zlog.Info().Int("log_rotate_max_backups", config.LogRotateMaxBackups).Msg("CONF")
	zlog.Info().Int("log_rotate_max_age", config.LogRotateMaxAge).Msg("CONF")

	zlog.Info().Bool("https", config.HTTPS).Msg("CONF")
	zlog.Info().Str("listen_addr", config.ListenAddr).Msg("CONF")
	zlog.Info().Int("listen_port", config.ListenPort).Msg("CONF")

	zlog.Info().Bool("exporter_https", config.ExporterHTTPS).Msg("CONF")
	zlog.Info().Str("exporter_listen_addr", config.ExporterListenAddr).Msg("CONF")
	zlog.Info().Int("exporter_listen_port", config.ExporterListenPort).Msg("CONF")

}
