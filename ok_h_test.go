package kohaku

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestHealth(t *testing.T) {
	// Setup
	e := server.echo
	req := httptest.NewRequest(http.MethodPost, "/.ok", strings.NewReader(""))
	req.Proto = "HTTP/2.0"
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	// Assertions
	server.ok(c)
	// assert.Equal(t, http.StatusNoContent, c.Writer.Status())
	assert.Equal(t, http.StatusNoContent, rec.Code)
}
