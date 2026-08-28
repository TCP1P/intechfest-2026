package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestGatewayProxiesGameplayButBlocksPrivateScoring(t *testing.T) {
	var scoringReached bool
	var signingHeaderReached bool
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/healthz":
			_, _ = io.WriteString(w, `{"status":"ok"}`)
		case "/api/v1/scoring":
			scoringReached = true
			http.Error(w, "private", http.StatusOK)
		default:
			signingHeaderReached = request.Header.Get("X-RSCTF-Signature") != "" || request.Header.Get("X-RSCTF-Timestamp") != ""
			body, _ := io.ReadAll(request.Body)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(body)
		}
	}))
	defer upstream.Close()
	parsed, _ := url.Parse(upstream.URL)
	app := newGateway(config{upstream: parsed, targetID: strings.Repeat("a", 64)})

	request := httptest.NewRequest(http.MethodPost, "/api/v1/enroll", strings.NewReader(`{"token":"opaque"}`))
	request.Header.Set("X-RSCTF-Signature", "must-not-cross")
	recorder := httptest.NewRecorder()
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || recorder.Body.String() != `{"token":"opaque"}` || signingHeaderReached {
		t.Fatalf("status=%d body=%q", recorder.Code, recorder.Body.String())
	}

	recorder = httptest.NewRecorder()
	app.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/scoring", nil))
	if recorder.Code != http.StatusNotFound || scoringReached {
		t.Fatalf("private scoring route escaped: status=%d reached=%v", recorder.Code, scoringReached)
	}

	recorder = httptest.NewRecorder()
	app.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/gateway/healthz", nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"targetId"`) {
		t.Fatalf("health status=%d body=%q", recorder.Code, recorder.Body.String())
	}
}

func TestLocalHealthcheckRequiresTargetIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `{"status":"ok","build":"test","targetId":"`+strings.Repeat("c", 64)+`"}`)
	}))
	defer server.Close()
	if err := localHealthcheck(server.URL); err != nil {
		t.Fatal(err)
	}
}

func TestGatewayRejectsChunkedOversizedBody(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Fatal("oversized body reached upstream")
	}))
	defer upstream.Close()
	parsed, _ := url.Parse(upstream.URL)
	app := newGateway(config{upstream: parsed, targetID: strings.Repeat("b", 64)})
	request := httptest.NewRequest(http.MethodPost, "/api/v1/enroll", strings.NewReader(strings.Repeat("x", maxRequestBody+1)))
	request.ContentLength = -1
	recorder := httptest.NewRecorder()
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d body=%q", recorder.Code, recorder.Body.String())
	}
}
