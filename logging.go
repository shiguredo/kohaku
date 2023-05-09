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
		writer := zerolog.ConsoleWriter{
			Out: os.Stdout,
			FormatTimestamp: func(i interface{}) string {
				_, err := time.ParseInLocation("2006-01-02T15:04:05.000000Z07:00:00", i.(string), time.UTC)
				if err != nil {
					return fmt.Sprintf("\x1b[%dm%s\x1b[0m", 90, i)
				}
				return i.(string)
			},
			NoColor: false,
		}
		prettyFormat(&writer)
		log.Logger = zerolog.New(writer).With().Caller().Timestamp().Logger()
	} else if config.LogStdout {
		// log_stdout = true の時はコンソールにも JSON 形式で出力する
		writer := zerolog.ConsoleWriter{
			Out: os.Stdout,
			FormatTimestamp: func(i interface{}) string {
				_, err := time.ParseInLocation("2006-01-02T15:04:05.000000Z07:00:00", i.(string), time.UTC)
				if err != nil {
					return fmt.Sprintf("\x1b[%dm%s\x1b[0m", 90, i)
				}
				return i.(string)
			},
			NoColor: false,
		}
		log.Logger = zerolog.New(writer).With().Caller().Timestamp().Logger()
	} else {
		// それ以外はファイルにだけ JSON 形式で出力する
		// ファイル出力はログローテーションなどを行う
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

// 現時点での prettyFormat
// 2023-04-17 12:51:56.333485Z [INFO] config.go:102 > CONF | debug=true
func prettyFormat(w *zerolog.ConsoleWriter) {
	w.FormatLevel = func(i interface{}) string {
		return strings.ToUpper(fmt.Sprintf("[%s]", i))
	}
	// TODO: Caller をファイル名と行番号だけの表示で出力する
	//       以下のようなフォーマットにしたい
	//       2023-04-17 12:50:09.334758Z [INFO] [config.go:102] CONF | debug=true
	// TODO: name=value が無い場合に | を消す方法がわからなかった
	w.FormatMessage = func(i interface{}) string {
		return fmt.Sprintf("%s |", i)
	}
	w.FormatFieldName = func(i interface{}) string {
		return fmt.Sprintf("%s=", i)
	}
	// TODO: カンマ区切りを同実現するかわからなかった
	w.FormatFieldValue = func(i interface{}) string {
		return fmt.Sprintf("%s", i)
	}
}
