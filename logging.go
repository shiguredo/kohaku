package kohaku

import (
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"github.com/shiguredo/lumberjack/v3"
)

// InitLogger ロガーを初期化する
func InitLogger(config *Config) error {

	if f, err := os.Stat(config.LogDir); os.IsNotExist(err) || !f.IsDir() {
		return err
	}

	logPath := fmt.Sprintf("%s/%s", config.LogDir, config.LogName)

	// https://github.com/rs/zerolog/issues/77
	zerolog.TimestampFunc = func() time.Time {
		return time.Now().UTC()
	}

	zerolog.TimeFieldFormat = time.RFC3339Nano

	if config.Debug {
		zerolog.SetGlobalLevel(zerolog.DebugLevel)
	} else {
		zerolog.SetGlobalLevel(zerolog.InfoLevel)
	}

	// debug = true かつ log_stdout = true の場合は stdout には pretty logging 形式で出力する
	if config.Debug && config.LogStdout {
		writer := zerolog.ConsoleWriter{Out: os.Stdout, TimeFormat: "2006-01-02 15:04:05.000000Z"}
		writer.FormatLevel = func(i interface{}) string {
			return strings.ToUpper(fmt.Sprintf("[%s]", i))
		}
		// TODO: Caller をファイル名と行番号だけの表示で出力する
		// 以下のようなフォーマット
		// 2023-04-17 12:50:09.334758Z [INFO] [config.go:102] CONF | debug=true
		writer.FormatMessage = func(i interface{}) string {
			return fmt.Sprintf("%s |", i)
		}
		writer.FormatFieldName = func(i interface{}) string {
			return fmt.Sprintf("%s=", i)
		}
		// TODO: カンマ区切りを同実現するかわからなかった
		writer.FormatFieldValue = func(i interface{}) string {
			return fmt.Sprintf("%s", i)
		}
		log.Logger = zerolog.New(writer).With().Caller().Timestamp().Logger()
	} else if config.LogStdout {
		// log_stdout = true の時はコンソールにも JSON 形式で出力する
		writer := zerolog.ConsoleWriter{Out: os.Stdout, TimeFormat: "2006-01-02 15:04:05.000000Z"}
		log.Logger = zerolog.New(writer).With().Caller().Timestamp().Logger()
	} else {
		// それ以外はファイルにだけ JSON 形式で出力する
		writer := &lumberjack.Logger{
			Filename:   logPath,
			MaxSize:    config.LogRotateMaxSize,
			MaxBackups: config.LogRotateMaxBackups,
			MaxAge:     config.LogRotateMaxAge,
			Compress:   false,
		}
		log.Logger = zerolog.New(writer).With().Caller().Timestamp().Logger()
	}

	return nil
}

func format(w *zerolog.ConsoleWriter) {
}
