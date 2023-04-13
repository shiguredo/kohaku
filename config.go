package kohaku

import (
	zlog "github.com/rs/zerolog/log"
	"gopkg.in/ini.v1"
)

const (
	defaultLogDir  = "."
	defaultLogName = "kohaku.jsonl"

	defaultListenPrometheusAddr = "0.0.0.0"
	defaultListenPrometheusPort = 4000
)

type Config struct {
	Debug bool `ini:"debug"`

	LogDir    string `ini:"log_dir"`
	LogName   string `ini:"log_name"`
	LogLevel  string `ini:"log_level"`
	LogDebug  bool   `ini:"log_debug"`
	LogStdout bool   `ini:"log_stdout"`

	ListenAddr string `ini:"listen_addr"`
	ListenPort int    `ini:"listen_port"`

	PostgresURI string `ini:"postgres_uri"`

	HTTP2FullchainFile    string `ini:"http2_fullchain_file"`
	HTTP2PrivkeyFile      string `ini:"http2_privkey_file"`
	HTTP2VerifyCacertPath string `ini:"http2_verify_cacert_path"`

	HTTP2MaxConcurrentStreams uint32 `ini:"http2_max_concurrent_streams"`
	HTTP2MaxReadFrameSize     uint32 `ini:"http2_max_read_frame_size"`
	HTTP2IdleTimeout          uint32 `ini:"http2_idle_timeout"`

	ListenExporterAddr string `ini:"listen_exporter_addr"`
	ListenExporterPort int    `ini:"listen_exporter_port"`
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
		config.LogDir = defaultLogDir
	}

	if config.LogName == "" {
		config.LogDir = defaultLogName
	}

	if config.ListenExporterAddr == "" {
		config.ListenExporterAddr = defaultListenPrometheusAddr
	}

	if config.ListenExporterPort == 0 {
		config.ListenExporterPort = defaultListenPrometheusPort
	}

	zlog.Info().Bool("debug", config.Debug).Msg("KohakuConf")
	zlog.Info().Str("log_dir", config.LogDir).Msg("KohakuConf")
	zlog.Info().Str("log_name", config.LogName).Msg("KohakuConf")
	zlog.Info().Str("log_level", config.LogLevel).Msg("KohakuConf")

	zlog.Info().Str("listen_addr", config.ListenAddr).Msg("KohakuConf")
	zlog.Info().Int("listen_port", config.ListenPort).Msg("KohakuConf")

	zlog.Info().Str("listen_prometheus_addr", config.ListenExporterAddr).Msg("KohakuConf")
	zlog.Info().Int("listen_prometheus_port", config.ListenExporterPort).Msg("KohakuConf")
}
