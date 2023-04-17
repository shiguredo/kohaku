package kohaku

import (
	zlog "github.com/rs/zerolog/log"
	"gopkg.in/ini.v1"
)

const (
	DEFAULT_LOG_DIR  = "."
	DEFAULT_LOG_NAME = "kohaku.jsonl"

	// megabytes
	DEFAULT_LOG_ROTATE_MAX_SIZE    = 200
	DEFAULT_LOG_ROTATE_MAX_BACKUPS = 7
	// days
	DEFAULT_LOG_ROTATE_MAX_AGE = 30

	DEFAULT_EXPORTER_LISTEN_ADDR = "0.0.0.0"
	DEFAULT_EXPORTER_LISTEN_PORT = 5891
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

	ListenAddr string `ini:"listen_addr"`
	ListenPort int    `ini:"listen_port"`

	ExporterListenAddr string `ini:"exporter_listen_addr"`
	ExporterListenPort int    `ini:"exporter_listen_port"`

	PostgresURI        string `ini:"postgres_uri"`
	PostgresCACertFile string `ini:"postgres_ca_cert_file"`

	HTTP2FullchainFile    string `ini:"http2_fullchain_file"`
	HTTP2PrivkeyFile      string `ini:"http2_privkey_file"`
	HTTP2VerifyCacertPath string `ini:"http2_verify_cacert_path"`

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

	return config, nil
}

func setDefaultsConfig(config *Config) {
	if config.LogDir == "" {
		config.LogDir = DEFAULT_LOG_DIR
	}

	if config.LogName == "" {
		config.LogDir = DEFAULT_LOG_NAME
	}

	if config.LogRotateMaxSize == 0 {
		config.LogRotateMaxSize = DEFAULT_LOG_ROTATE_MAX_SIZE
	}

	if config.LogRotateMaxBackups == 0 {
		config.LogRotateMaxBackups = DEFAULT_LOG_ROTATE_MAX_BACKUPS
	}

	if config.LogRotateMaxAge == 0 {
		config.LogRotateMaxAge = DEFAULT_LOG_ROTATE_MAX_AGE
	}

	if config.ExporterListenAddr == "" {
		config.ExporterListenAddr = DEFAULT_EXPORTER_LISTEN_ADDR
	}

	if config.ExporterListenPort == 0 {
		config.ExporterListenPort = DEFAULT_EXPORTER_LISTEN_PORT
	}

	zlog.Info().Bool("debug", config.Debug).Msg("KohakuConf")

	zlog.Info().Str("log_dir", config.LogDir).Msg("KohakuConf")
	zlog.Info().Str("log_name", config.LogName).Msg("KohakuConf")

	zlog.Info().Int("log_rotate_max_size", config.LogRotateMaxSize).Msg("KohakuConf")
	zlog.Info().Int("log_rotate_max_backups", config.LogRotateMaxBackups).Msg("KohakuConf")
	zlog.Info().Int("log_rotate_max_age", config.LogRotateMaxAge).Msg("KohakuConf")

	zlog.Info().Str("listen_addr", config.ListenAddr).Msg("KohakuConf")
	zlog.Info().Int("listen_port", config.ListenPort).Msg("KohakuConf")

	zlog.Info().Str("exporter_listen_addr", config.ExporterListenAddr).Msg("KohakuConf")
	zlog.Info().Int("exporter_listen_port", config.ExporterListenPort).Msg("KohakuConf")
}
