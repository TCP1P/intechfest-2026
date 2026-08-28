package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

const (
	maxRequestBody = 1 << 20
	maxHealthBody  = 4 << 10
)

var gatewayBuild = "dev"

type config struct {
	listen   string
	upstream *url.URL
	targetID string
}

type gateway struct {
	cfg    config
	proxy  *httputil.ReverseProxy
	client *http.Client
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := localHealthcheck(env("RYTHME_GATEWAY_HEALTHCHECK_URL", "http://127.0.0.1:8080/gateway/healthz")); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	cfg, err := loadConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	app := newGateway(cfg)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	server := &http.Server{
		Addr: cfg.listen, Handler: app, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 15 * time.Second, WriteTimeout: 0, IdleTimeout: 60 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdown, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = server.Shutdown(shutdown)
	}()
	fmt.Printf("Rythme transport gateway %s listening on %s for target %.12s\n", gatewayBuild, cfg.listen, cfg.targetID)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func loadConfig() (config, error) {
	raw := strings.TrimSpace(os.Getenv("RYTHME_UPSTREAM"))
	upstream, err := url.Parse(raw)
	if err != nil || upstream.Scheme != "http" || upstream.Host == "" || upstream.User != nil ||
		(upstream.Path != "" && upstream.Path != "/") || upstream.RawQuery != "" || upstream.Fragment != "" {
		return config{}, errors.New("RYTHME_UPSTREAM must be one private HTTP origin")
	}
	targetID := strings.TrimSpace(os.Getenv("RYTHME_TARGET_ID"))
	if !canonicalHash(targetID) {
		return config{}, errors.New("RYTHME_TARGET_ID must be the active container identity")
	}
	return config{listen: env("RYTHME_GATEWAY_LISTEN", ":8080"), upstream: upstream, targetID: targetID}, nil
}

func newGateway(cfg config) *gateway {
	transport := &http.Transport{
		Proxy: nil, MaxIdleConns: 64, MaxIdleConnsPerHost: 32,
		IdleConnTimeout: 45 * time.Second, ResponseHeaderTimeout: 15 * time.Second,
		ExpectContinueTimeout: time.Second,
	}
	proxy := httputil.NewSingleHostReverseProxy(cfg.upstream)
	proxy.Transport = transport
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, _ error) {
		writeProblem(w, http.StatusBadGateway, "arena transport unavailable")
	}
	proxy.ModifyResponse = func(response *http.Response) error {
		response.Header.Del("Server")
		response.Header.Set("X-Content-Type-Options", "nosniff")
		response.Header.Set("Referrer-Policy", "no-referrer")
		return nil
	}
	return &gateway{
		cfg: cfg, proxy: proxy,
		client: &http.Client{Transport: transport.Clone(), Timeout: 4 * time.Second},
	}
}

func (g *gateway) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	if request.URL.Path == "/gateway/healthz" {
		if request.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeProblem(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		g.health(w, request)
		return
	}
	if request.URL.Path == "/api/v1/scoring" || strings.HasPrefix(request.URL.Path, "/api/v1/scoring/") {
		writeProblem(w, http.StatusNotFound, "route not found")
		return
	}
	body, err := readBody(request.Body, request.ContentLength, maxRequestBody)
	if err != nil {
		writeProblem(w, http.StatusRequestEntityTooLarge, "request body exceeds the size limit")
		return
	}
	request.Body = io.NopCloser(bytes.NewReader(body))
	request.ContentLength = int64(len(body))
	request.Header.Del("X-RSCTF-Signature")
	request.Header.Del("X-RSCTF-Timestamp")
	g.proxy.ServeHTTP(w, request)
}

func (g *gateway) health(w http.ResponseWriter, request *http.Request) {
	endpoint := g.cfg.upstream.ResolveReference(&url.URL{Path: "/healthz"})
	probe, err := http.NewRequestWithContext(request.Context(), http.MethodGet, endpoint.String(), nil)
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "arena health unavailable")
		return
	}
	response, err := g.client.Do(probe)
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "arena health unavailable")
		return
	}
	defer response.Body.Close()
	var status struct {
		Status string `json:"status"`
	}
	if response.StatusCode != http.StatusOK || decodeBounded(response.Body, maxHealthBody, &status) != nil || status.Status != "ok" {
		writeProblem(w, http.StatusServiceUnavailable, "arena health unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok", "build": gatewayBuild, "targetId": g.cfg.targetID,
	})
}

func readBody(reader io.ReadCloser, contentLength, limit int64) ([]byte, error) {
	defer reader.Close()
	if contentLength > limit {
		return nil, errors.New("body too large")
	}
	limited := &io.LimitedReader{R: reader, N: limit + 1}
	body, err := io.ReadAll(limited)
	if err != nil || int64(len(body)) > limit {
		return nil, errors.New("body too large")
	}
	return body, nil
}

func decodeBounded(reader io.Reader, limit int64, destination any) error {
	limited := &io.LimitedReader{R: reader, N: limit + 1}
	decoder := json.NewDecoder(limited)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("trailing JSON")
	}
	if limited.N == 0 {
		return errors.New("body too large")
	}
	return nil
}

func localHealthcheck(raw string) error {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(raw)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("gateway health returned HTTP %d", response.StatusCode)
	}
	var status struct {
		Status   string `json:"status"`
		Build    string `json:"build"`
		TargetID string `json:"targetId"`
	}
	if err := decodeBounded(response.Body, maxHealthBody, &status); err != nil || status.Status != "ok" || status.Build == "" || !canonicalHash(status.TargetID) {
		return errors.New("gateway health response is invalid")
	}
	return nil
}

func writeProblem(w http.ResponseWriter, status int, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"status": status, "detail": detail})
}

func canonicalHash(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}

func env(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}
