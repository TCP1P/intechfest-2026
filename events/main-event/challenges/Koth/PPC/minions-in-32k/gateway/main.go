package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	maxJSONReply      = 256 * 1024
	maxProxySessions  = 256
	maxProxyPerIP     = 16
	newSessionRate    = 32.0
	newSessionBurst   = 64.0
	perIPSessionRate  = 4.0
	perIPSessionBurst = 8.0
	proxyIdle         = 90 * time.Second
	maxDatagram       = 2048
)

var (
	errProxyAdmission = errors.New("UDP session admission limit reached")
	gatewayBuild      = "dev"
)

type configuration struct {
	TargetID     string
	UpstreamUDP  *net.UDPAddr
	HealthURL    *url.URL
	SessionURL   *url.URL
	PublicRemote string
}

type arenaSession struct {
	Protocol string `json:"protocol"`
	Remote   string `json:"remote"`
	Name     string `json:"name"`
	Key      string `json:"key"`
	CertHash string `json:"certHash"`
}

type gateway struct {
	cfg    configuration
	client *http.Client
	proxy  *udpProxy
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := gatewayHealthcheck(environmentDefault("MINIONS_GATEWAY_HEALTHCHECK_URL", "http://127.0.0.1:8080/gateway/healthz")); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	cfg, err := loadConfiguration()
	if err != nil {
		log.Fatalf("configuration: %v", err)
	}
	proxy, err := newUDPProxy(environmentDefault("MINIONS_UDP_LISTEN", ":4532"), cfg.UpstreamUDP)
	if err != nil {
		log.Fatalf("UDP gateway: %v", err)
	}
	g := &gateway{
		cfg: cfg,
		client: &http.Client{
			Transport: &http.Transport{
				Proxy: http.ProxyFromEnvironment, MaxIdleConns: 32, MaxIdleConnsPerHost: 16,
				IdleConnTimeout: 45 * time.Second, ResponseHeaderTimeout: 8 * time.Second,
				DisableCompression: true,
			},
			Timeout: 12 * time.Second,
		},
		proxy: proxy,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", g.landing)
	mux.HandleFunc("GET /healthz", g.health)
	mux.HandleFunc("GET /gateway/healthz", g.health)
	mux.HandleFunc("POST /api/v1/arena-session", g.createSession)
	server := &http.Server{
		Addr: ":8080", Handler: securityHeaders(mux), ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout: 15 * time.Second, WriteTimeout: 15 * time.Second,
		IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 * 1024,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	errorsChannel := make(chan error, 2)
	go func() { errorsChannel <- server.ListenAndServe() }()
	go func() { errorsChannel <- proxy.serve(ctx) }()
	log.Printf("Minions in 32K transport gateway %s ready target=%s UDP=%s", gatewayBuild, shortID(cfg.TargetID), cfg.PublicRemote)
	select {
	case <-ctx.Done():
	case err := <-errorsChannel:
		if !errors.Is(err, http.ErrServerClosed) && !errors.Is(err, net.ErrClosed) {
			log.Printf("gateway stopped: %v", err)
		}
		stop()
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdownContext)
	proxy.close()
}

func loadConfiguration() (configuration, error) {
	targetID := strings.TrimSpace(os.Getenv("MINIONS_TARGET_ID"))
	if !canonicalHash(targetID) {
		return configuration{}, errors.New("MINIONS_TARGET_ID must be a full container identity")
	}
	upstreamUDP, err := net.ResolveUDPAddr("udp", strings.TrimSpace(os.Getenv("MINIONS_UPSTREAM")))
	if err != nil || upstreamUDP.Port == 0 || upstreamUDP.IP == nil {
		return configuration{}, errors.New("MINIONS_UPSTREAM must be a numeric UDP address with a port")
	}
	healthURL, err := parseInternalHTTPURL("MINIONS_HEALTH_URL", "/healthz")
	if err != nil {
		return configuration{}, err
	}
	sessionURL := *healthURL
	sessionURL.Path = "/api/v1/arena-session"
	publicRemote := environmentDefault("MINIONS_PUBLIC_REMOTE", "tcp.1pc.tf:4532")
	if !validRemote(publicRemote) {
		return configuration{}, errors.New("MINIONS_PUBLIC_REMOTE must be a host and UDP port")
	}
	return configuration{
		TargetID: targetID, UpstreamUDP: upstreamUDP, HealthURL: healthURL,
		SessionURL: &sessionURL, PublicRemote: publicRemote,
	}, nil
}

func parseInternalHTTPURL(name, path string) (*url.URL, error) {
	value, err := url.Parse(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value.Scheme != "http" || value.Host == "" || value.Path != path ||
		value.User != nil || value.RawQuery != "" || value.Fragment != "" {
		return nil, fmt.Errorf("%s is not a valid internal HTTP URL", name)
	}
	return value, nil
}

func (g *gateway) landing(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=300")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"name": "Minions in 32K", "protocol": "minions/0", "transport": "QUIC/UDP",
		"remote": g.cfg.PublicRemote,
		"source": "https://github.com/project-sekai-ctf/sekaictf-2026/tree/main/game/minions-in-16k/challenge",
	})
}

func (g *gateway) createSession(w http.ResponseWriter, request *http.Request) {
	if body, err := readLimited(w, request, 1); err != nil || len(body) != 0 {
		writeProblem(w, http.StatusBadRequest, "session request must have an empty body")
		return
	}
	capability, ok := bearerCapability(request.Header.Get("Authorization"))
	if !ok {
		writeProblem(w, http.StatusUnauthorized, "invalid event capability")
		return
	}
	upstream, err := http.NewRequestWithContext(request.Context(), http.MethodPost, g.cfg.SessionURL.String(), http.NoBody)
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "arena session unavailable")
		return
	}
	upstream.Header.Set("Authorization", "Bearer "+capability)
	upstream.Header.Set("Accept", "application/json")
	upstream.Header.Set("User-Agent", "minions-transport-gateway/1")
	response, err := g.client.Do(upstream)
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "arena session unavailable")
		return
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxJSONReply+1))
	if err != nil || len(body) > maxJSONReply {
		writeProblem(w, http.StatusBadGateway, "arena session response is invalid")
		return
	}
	if response.StatusCode != http.StatusOK {
		forwardProblem(w, response, body)
		return
	}
	var session arenaSession
	if decodeExact(body, &session) != nil || session.Protocol != "minions/0" ||
		session.Name == "" || len(session.Name) > 64 || hasControl(session.Name) ||
		len(session.Key) < 32 || len(session.Key) > 256 || hasControl(session.Key) ||
		!canonicalHash(session.CertHash) {
		writeProblem(w, http.StatusBadGateway, "arena session response is invalid")
		return
	}
	session.Remote = g.cfg.PublicRemote
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(session)
}

func forwardProblem(w http.ResponseWriter, response *http.Response, body []byte) {
	switch response.StatusCode {
	case http.StatusBadRequest, http.StatusUnauthorized, http.StatusForbidden,
		http.StatusConflict, http.StatusServiceUnavailable:
		if !json.Valid(body) {
			writeProblem(w, http.StatusBadGateway, "arena session response is invalid")
			return
		}
		if retryAfter := response.Header.Get("Retry-After"); retryAfter != "" && len(retryAfter) <= 16 {
			w.Header().Set("Retry-After", retryAfter)
		}
		w.Header().Set("Content-Type", "application/problem+json")
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(body)
	default:
		writeProblem(w, http.StatusBadGateway, "arena session response is invalid")
	}
}

func (g *gateway) health(w http.ResponseWriter, request *http.Request) {
	ctx, cancel := context.WithTimeout(request.Context(), 3*time.Second)
	defer cancel()
	if g.proxy == nil || !g.proxy.healthy() || !g.dependencyHealthy(ctx) {
		writeProblem(w, http.StatusServiceUnavailable, "gateway dependency unavailable")
		return
	}
	sessions, rejected := g.proxy.metrics()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok", "build": gatewayBuild, "udpSessions": sessions,
		"udpAdmissionsRejected": rejected,
	})
}

func (g *gateway) dependencyHealthy(ctx context.Context) bool {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, g.cfg.HealthURL.String(), nil)
	if err != nil {
		return false
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "minions-transport-gateway/1")
	response, err := g.client.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 2049))
	return err == nil && len(body) <= 2048 && response.StatusCode == http.StatusOK
}

func gatewayHealthcheck(endpoint string) error {
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Get(endpoint)
	if err != nil {
		return fmt.Errorf("gateway health request: %w", err)
	}
	defer response.Body.Close()
	read, readErr := io.Copy(io.Discard, io.LimitReader(response.Body, 1025))
	if readErr != nil || read > 1024 || response.StatusCode != http.StatusOK {
		return errors.New("gateway health response is invalid")
	}
	return nil
}

type proxySession struct {
	client   *net.UDPAddr
	upstream *net.UDPConn
	lastSeen time.Time
	clientIP string
}

type admissionBucket struct {
	tokens   float64
	updated  time.Time
	lastUsed time.Time
	rate     float64
	burst    float64
}

func newAdmissionBucket(now time.Time, rate, burst float64) *admissionBucket {
	return &admissionBucket{tokens: burst, updated: now, lastUsed: now, rate: rate, burst: burst}
}

func (b *admissionBucket) refill(now time.Time) {
	if elapsed := now.Sub(b.updated).Seconds(); elapsed > 0 {
		b.tokens = min(b.burst, b.tokens+elapsed*b.rate)
		b.updated = now
	}
	b.lastUsed = now
}

type udpProxy struct {
	public          *net.UDPConn
	upstream        *net.UDPAddr
	mu              sync.Mutex
	sessions        map[string]*proxySession
	perIP           map[string]*admissionBucket
	global          *admissionBucket
	maximumSessions int
	maximumPerIP    int
	rejected        uint64
	stopped         bool
}

func newUDPProxy(listen string, upstream *net.UDPAddr) (*udpProxy, error) {
	address, err := net.ResolveUDPAddr("udp", listen)
	if err != nil {
		return nil, err
	}
	public, err := net.ListenUDP("udp", address)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	return &udpProxy{
		public: public, upstream: upstream, sessions: make(map[string]*proxySession),
		perIP: make(map[string]*admissionBucket), global: newAdmissionBucket(now, newSessionRate, newSessionBurst),
		maximumSessions: maxProxySessions, maximumPerIP: maxProxyPerIP,
	}, nil
}

func (p *udpProxy) serve(ctx context.Context) error {
	go func() {
		<-ctx.Done()
		p.close()
	}()
	go p.cleanup(ctx)
	buffer := make([]byte, maxDatagram)
	for {
		count, client, err := p.public.ReadFromUDP(buffer)
		if err != nil {
			return err
		}
		if count == 0 {
			continue
		}
		session, err := p.session(client)
		if err != nil {
			continue
		}
		if _, err := session.upstream.Write(buffer[:count]); err != nil {
			p.drop(client.String(), session)
		}
	}
}

func (p *udpProxy) session(client *net.UDPAddr) (*proxySession, error) {
	key := client.String()
	now := time.Now()
	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return nil, net.ErrClosed
	}
	if existing := p.sessions[key]; existing != nil {
		existing.lastSeen = now
		p.mu.Unlock()
		return existing, nil
	}
	for candidateKey, candidate := range p.sessions {
		if now.Sub(candidate.lastSeen) > proxyIdle {
			delete(p.sessions, candidateKey)
			_ = candidate.upstream.Close()
		}
	}
	clientIP := client.IP.String()
	perIPCount := 0
	for _, candidate := range p.sessions {
		if candidate.clientIP == clientIP {
			perIPCount++
		}
	}
	if len(p.sessions) >= p.maximumSessions || perIPCount >= p.maximumPerIP || !p.admitNewSessionLocked(clientIP, now) {
		p.rejected++
		p.mu.Unlock()
		return nil, errProxyAdmission
	}
	upstream, err := net.DialUDP("udp", nil, p.upstream)
	if err != nil {
		p.mu.Unlock()
		return nil, err
	}
	clientCopy := *client
	clientCopy.IP = append(net.IP(nil), client.IP...)
	session := &proxySession{client: &clientCopy, upstream: upstream, lastSeen: now, clientIP: clientIP}
	p.sessions[key] = session
	p.mu.Unlock()
	go p.relayBack(key, session)
	return session, nil
}

func (p *udpProxy) admitNewSessionLocked(clientIP string, now time.Time) bool {
	bucket := p.perIP[clientIP]
	if bucket == nil {
		bucket = newAdmissionBucket(now, perIPSessionRate, perIPSessionBurst)
		p.perIP[clientIP] = bucket
	}
	p.global.refill(now)
	bucket.refill(now)
	if p.global.tokens < 1 || bucket.tokens < 1 {
		return false
	}
	p.global.tokens--
	bucket.tokens--
	return true
}

func (p *udpProxy) relayBack(key string, session *proxySession) {
	buffer := make([]byte, maxDatagram)
	for {
		count, err := session.upstream.Read(buffer)
		if err != nil {
			p.drop(key, session)
			return
		}
		if count > 0 {
			if _, err := p.public.WriteToUDP(buffer[:count], session.client); err != nil {
				p.drop(key, session)
				return
			}
		}
	}
}

func (p *udpProxy) cleanup(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			p.mu.Lock()
			for key, session := range p.sessions {
				if now.Sub(session.lastSeen) > proxyIdle {
					delete(p.sessions, key)
					_ = session.upstream.Close()
				}
			}
			for clientIP, bucket := range p.perIP {
				if now.Sub(bucket.lastUsed) <= proxyIdle {
					continue
				}
				active := false
				for _, session := range p.sessions {
					if session.clientIP == clientIP {
						active = true
						break
					}
				}
				if !active {
					delete(p.perIP, clientIP)
				}
			}
			p.mu.Unlock()
		}
	}
}

func (p *udpProxy) drop(key string, session *proxySession) {
	p.mu.Lock()
	if p.sessions[key] == session {
		delete(p.sessions, key)
	}
	p.mu.Unlock()
	_ = session.upstream.Close()
}

func (p *udpProxy) healthy() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return !p.stopped
}

func (p *udpProxy) metrics() (int, uint64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.sessions), p.rejected
}

func (p *udpProxy) close() {
	p.mu.Lock()
	if p.stopped {
		p.mu.Unlock()
		return
	}
	p.stopped = true
	_ = p.public.Close()
	for key, session := range p.sessions {
		delete(p.sessions, key)
		_ = session.upstream.Close()
	}
	p.mu.Unlock()
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, request)
	})
}

func readLimited(w http.ResponseWriter, request *http.Request, limit int64) ([]byte, error) {
	request.Body = http.MaxBytesReader(w, request.Body, limit)
	defer request.Body.Close()
	return io.ReadAll(request.Body)
}

func writeProblem(w http.ResponseWriter, status int, title string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"title": title, "status": status})
}

func decodeExact(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("multiple JSON values")
	}
	return nil
}

func bearerCapability(header string) (string, bool) {
	if !strings.HasPrefix(header, "Bearer ") {
		return "", false
	}
	token := strings.TrimPrefix(header, "Bearer ")
	if len(token) < 13 || len(token) > 133 || !strings.HasPrefix(token, "koth_") || token != strings.TrimSpace(token) {
		return "", false
	}
	for _, character := range token {
		if !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') &&
			!(character >= '0' && character <= '9') && character != '_' && character != '-' {
			return "", false
		}
	}
	return token, true
}

func validRemote(value string) bool {
	address, err := net.ResolveUDPAddr("udp", value)
	return err == nil && address.Port > 0
}

func canonicalHash(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	for _, character := range value {
		if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')) {
			return false
		}
	}
	return true
}

func hasControl(value string) bool {
	for _, character := range value {
		if character < 0x20 || character == 0x7f {
			return true
		}
	}
	return false
}

func environmentDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func shortID(value string) string {
	if len(value) > 12 {
		return value[:12]
	}
	return value
}
