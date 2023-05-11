package kohaku

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
)

// TODO: HTTP/1.1 のテストを追加する
func TestOK(t *testing.T) {
	// Setup
	s := newTestServer(config, pgPool)
	e := s.echo
	req := httptest.NewRequest(http.MethodPost, "/.ok", strings.NewReader(""))
	req.Proto = "HTTP/2.0"
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	// Assertions
	server.ok(c)
	// assert.Equal(t, http.StatusNoContent, c.Writer.Status())
	assert.Equal(t, http.StatusNoContent, rec.Code)
}
