package kohaku

import (
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"github.com/shiguredo/lumberjack/v3"
)

var (
	// megabytes
	logRotateMaxSize    = 200
	logRotateMaxBackups = 7
	// days
	logRotateMaxAge = 30
)

// InitLogger ロガーを初期化します
func InitLogger(logDir string, logName string, debug bool, isStdout bool) error {

	if f, err := os.Stat(logDir); os.IsNotExist(err) || !f.IsDir() {
		return err
	}

	logPath := fmt.Sprintf("%s/%s", logDir, logName)

	writer := &lumberjack.Logger{
		Filename:   logPath,
		MaxSize:    logRotateMaxSize,
		MaxBackups: logRotateMaxBackups,
		MaxAge:     logRotateMaxAge,
		Compress:   false,
	}

	// https://github.com/rs/zerolog/issues/77
	zerolog.TimestampFunc = func() time.Time {
		return time.Now().UTC()
	}

	zerolog.TimeFieldFormat = time.RFC3339Nano

	var writers io.Writer
	output := zerolog.ConsoleWriter{Out: writer, NoColor: true, TimeFormat: "2006-01-02 15:04:05.000000Z"}
	format(&output)
	if debug {
		zerolog.SetGlobalLevel(zerolog.DebugLevel)
	} else {
		zerolog.SetGlobalLevel(zerolog.InfoLevel)
	}
	// log_stdout: true の時はコンソールにもだす
	if isStdout {
		stdout := zerolog.ConsoleWriter{Out: os.Stdout, TimeFormat: "2006-01-02 15:04:05.000000Z"}
		format(&stdout)
		writers = io.MultiWriter(stdout, output)
	} else {
		writers = output
	}
	log.Logger = zerolog.New(writers).With().Timestamp().Logger()
	return nil
}

func format(w *zerolog.ConsoleWriter) {
	w.FormatLevel = func(i interface{}) string {
		return strings.ToUpper(fmt.Sprintf("[%s]", i))
	}
	w.FormatFieldName = func(i interface{}) string {
		return fmt.Sprintf("%s=", i)
	}
	w.FormatFieldValue = func(i interface{}) string {
		return fmt.Sprintf("%s", i)
	}
}
