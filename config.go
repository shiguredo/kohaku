package kohaku

import (
	"flag"
	"fmt"
	"io/ioutil"

	"github.com/goccy/go-yaml"
	"github.com/rs/zerolog/log"
)

var (
	ConfigFilePath = flag.String("c", "./config.yaml", "kohaku 設定ファイルへのパス(yaml)")
	Config         *KohakuConfig
)

type KohakuConfig struct {
	LogDebug  bool   `yaml:"log_debug"`
	LogDir    string `yaml:"log_dir"`
	LogName   string `yaml:"log_name"`
	LogStdout bool   `yaml:"log_stdout"`

	CollectorPort int `yaml:"collector_port"`

	TimescaleURL          string `yaml:"timescale_url"`
	TimescaleSSLMode      string `yaml:"timescale_sslmode"`
	TimescaleRootcertFile string `yaml:"timescale_rootcert_file"`

	// TODO(v): 名前検討
	HTTP2FullchainFile string `yaml:"http2_fullchain_file"`
	// TODO(v): 名前検討
	HTTP2PrivkeyFile string `yaml:"http2_privkey_file"`
	// TODO: 名前検討
	HTTP2VerifyCacertPath string `yaml:"http2_verify_cacert_path"`

	HTTP2H2c                  bool   `yaml:"http2_h2c"`
	HTTP2MaxConcurrentStreams uint32 `yaml:"http2_max_concurrent_streams"`
	HTTP2MaxReadFrameSize     uint32 `yaml:"http2_max_read_frame_size"`
	HTTP2IdleTimeout          uint32 `yaml:"http2_idle_timeout"`
}

// LoadConfigFromFlags 起動パラメータから設定ファイルを読み込みます
func LoadConfigFromFlags(configPath *string) error {
	tmpConfig, err := LoadConfig(*configPath)
	log.Printf("config file path: %s", *configPath)
	if err != nil {
		return err
	}
	Config = tmpConfig

	return nil
}

// LoadConfig 設定ファイルのパスからファイルを読み込み、設定値をバインドした KohakuConfig を返します
func LoadConfig(configPath string) (*KohakuConfig, error) {
	buf, err := ioutil.ReadFile(configPath)
	if err != nil {
		return nil, err
	}
	var config KohakuConfig
	if err := yaml.Unmarshal(buf, &config); err != nil {
		return nil, fmt.Errorf("KohakuConfig bind error: %s", err)
	}
	return &config, nil
}
