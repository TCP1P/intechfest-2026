package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
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
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	maxJSONReply      = 256 * 1024
	maxReportBody     = 64 * 1024
	activeGrace       = 8 * time.Minute
	waveRetention     = 2 * time.Hour
	readyWindow       = 3 * time.Second
	arenaRetryReady   = 5 * time.Second
	arenaStaging      = 30 * time.Second
	arenaStartMargin  = 10 * time.Second
	scoringLegLength  = time.Minute
	scoringLegCount   = uint32(5)
	maxLegDeaths      = uint32(31)
	maxLegTakeovers   = uint32(31)
	maxProxySessions  = 256
	maxProxyPerIP     = 16
	newSessionRate    = 32.0
	newSessionBurst   = 64.0
	perIPSessionRate  = 4.0
	perIPSessionBurst = 8.0
	proxyIdle         = 90 * time.Second
	maxDatagram       = 2048
)

var errProxyAdmission = errors.New("UDP session admission limit reached")

var bridgeBuild = "dev"

var objectiveIDs = []string{"official-score"}

type teamSecret struct {
	Name            string `json:"name"`
	ArenaUUID       string `json:"arenaUuid"`
	ArenaKey        string `json:"arenaKey"`
	ScoringEligible bool   `json:"scoringEligible"`
}

type secretFile struct {
	ObserverSecret string       `json:"observerSecret"`
	ReportToken    string       `json:"reportToken"`
	Teams          []teamSecret `json:"teams"`
}

type configuration struct {
	GameID       int
	ChallengeID  int
	TargetID     string
	UpstreamUDP  *net.UDPAddr
	HealthURL    *url.URL
	RSCTF        *url.URL
	PublicRemote string
	StateDir     string
	Secrets      secretFile
}

type capabilityIdentity struct {
	TeamID   string `json:"teamId"`
	TeamName string `json:"teamName"`
}

type arenaSession struct {
	Protocol string `json:"protocol"`
	Remote   string `json:"remote"`
	Name     string `json:"name"`
	Key      string `json:"key"`
	CertHash string `json:"certHash"`
}

type arenaRuntimeStatus struct {
	Name              string `json:"name"`
	Status            string `json:"status"`
	Protocol          string `json:"protocol"`
	ProtocolVersion   int    `json:"protocolVersion"`
	RenderWidth       int    `json:"renderWidth"`
	RenderHeight      int    `json:"renderHeight"`
	MatchID           string `json:"matchId"`
	ExpectedEndUnixMS int64  `json:"expectedEndUnixMs"`
	Players           int    `json:"players"`
	NPCs              int    `json:"npcs"`
	CertHash          string `json:"certHash"`
	Build             string `json:"build"`
}

type arenaStartPlayer struct {
	UUID string `json:"uuid"`
	Key  string `json:"key"`
	Name string `json:"name"`
	Bot  bool   `json:"bot"`
}

type arenaStartRequest struct {
	Players []arenaStartPlayer `json:"players"`
}

type arenaRetryError struct {
	retryAt time.Time
}

func (e *arenaRetryError) Error() string {
	return "the current Crown cycle cannot fit a complete match"
}

type upstreamHTTPError struct {
	service string
	status  int
}

func (e *upstreamHTTPError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", e.service, e.status)
}

type arenaRetryProblem struct {
	Title         string `json:"title"`
	Detail        string `json:"detail"`
	Status        int    `json:"status"`
	Code          string `json:"code"`
	RetryAtUnixMS int64  `json:"retryAtUnixMs"`
}

type liveSession struct {
	TokenHash string    `json:"tokenHash"`
	LastSeen  time.Time `json:"lastSeen"`
}

type replayArtifact struct {
	SHA256     string    `json:"sha256"`
	Size       int64     `json:"size"`
	ReceivedAt time.Time `json:"receivedAt"`
}

type reportPlayer struct {
	TeamUUID        string `json:"team_uuid"`
	Name            string `json:"name"`
	Rank            uint32 `json:"rank"`
	Kills           uint32 `json:"kills"`
	ConfirmedKills  uint32 `json:"confirmed_kills"`
	Deaths          uint32 `json:"deaths"`
	Connected       bool   `json:"connected"`
	Takeovers       uint32 `json:"takeovers"`
	IntegrityStatus string `json:"integrity_status"`
}

type matchReport struct {
	MatchID       string         `json:"match_id"`
	Kind          string         `json:"kind"`
	Sequence      uint32         `json:"sequence"`
	LegCount      uint32         `json:"leg_count"`
	StartedUnixMS int64          `json:"started_unix_ms"`
	EndedUnixMS   int64          `json:"ended_unix_ms"`
	DurationMS    int64          `json:"duration_ms"`
	Players       []reportPlayer `json:"players"`
}

type matchSequence struct {
	LegCount    uint32
	LegKeys     map[uint32]string
	Final       *matchReport
	FinalDigest [32]byte
	LastEndedAt time.Time
}

type finalizedWave struct {
	ID         string
	EndedAt    time.Time
	Result     matchReport
	Identities map[string]string
	Digest     [32]byte
}

type observerContext struct {
	APIVersion          string   `json:"apiVersion"`
	Context             string   `json:"context"`
	CycleNumber         int      `json:"cycleNumber"`
	ResetAttempt        int      `json:"resetAttempt"`
	RoundNumber         int      `json:"roundNumber"`
	CycleEndsAt         int64    `json:"cycleEndsAt"`
	WaveWindowStartsAt  int64    `json:"waveWindowStartsAt"`
	WaveWindowEndsAt    int64    `json:"waveWindowEndsAt"`
	EligibleTokenHashes []string `json:"eligibleTokenHashes"`
	ObjectiveIDs        []string `json:"objectiveIds"`
	ObjectiveSchemaHash *string  `json:"objectiveSchemaHash"`
	GeneratedAt         int64    `json:"generatedAt"`
}

type evidenceRatio struct {
	Earned   int64 `json:"earned"`
	Possible int64 `json:"possible"`
}

type teamEvidence struct {
	TokenHash  string          `json:"tokenHash"`
	Activity   evidenceRatio   `json:"activity"`
	Objectives []evidenceRatio `json:"objectives"`
	IsCrown    bool            `json:"isCrown"`
}

type waveEvidence struct {
	WaveID  string         `json:"waveId"`
	EndedAt int64          `json:"endedAtUnixMs"`
	Teams   []teamEvidence `json:"teams"`
}

type observation struct {
	Context      string         `json:"context"`
	ObjectiveIDs []string       `json:"objectiveIds"`
	Waves        []waveEvidence `json:"waves"`
}

type acceptedObservation struct {
	Accepted        bool  `json:"accepted"`
	CycleNumber     int   `json:"cycleNumber"`
	ResetAttempt    int   `json:"resetAttempt"`
	RoundNumber     int   `json:"roundNumber"`
	SubmittedWaves  int   `json:"submittedWaves"`
	SubmittedTeams  int   `json:"submittedTeams"`
	RecognizedTeams int   `json:"recognizedTeams"`
	AcceptedAt      int64 `json:"acceptedAt"`
}

type bridge struct {
	cfg    configuration
	client *http.Client
	proxy  *udpProxy

	mu            sync.Mutex
	arenaMu       sync.Mutex
	sessions      map[string]liveSession
	waves         map[string]finalizedWave
	matches       map[string]*matchSequence
	replays       map[string]replayArtifact
	lastTimestamp int64
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := bridgeHealthcheck(environmentDefault("MINIONS_BRIDGE_HEALTHCHECK_URL", "http://127.0.0.1:8080/bridge/healthz")); err != nil {
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
		log.Fatalf("UDP proxy: %v", err)
	}
	b := &bridge{
		cfg:      cfg,
		client:   &http.Client{Transport: transport(), Timeout: 12 * time.Second},
		proxy:    proxy,
		sessions: make(map[string]liveSession),
		waves:    make(map[string]finalizedWave),
		matches:  make(map[string]*matchSequence),
		replays:  make(map[string]replayArtifact),
	}
	if err := b.loadPersistentState(time.Now().UTC()); err != nil {
		log.Fatalf("persistent state: %v", err)
	}

	publicMux := http.NewServeMux()
	publicMux.HandleFunc("GET /{$}", b.landing)
	publicMux.HandleFunc("GET /healthz", b.health)
	publicMux.HandleFunc("GET /bridge/healthz", b.health)
	publicMux.HandleFunc("POST /api/v1/arena-session", b.createSession)

	internalMux := http.NewServeMux()
	internalMux.HandleFunc("POST /internal/match-report", b.acceptReport)
	internalMux.HandleFunc("POST /internal/replay", b.acceptReplay)
	internalMux.HandleFunc("GET /internal/healthz", b.health)

	publicServer := secureServer(":8080", publicMux)
	internalServer := secureServer(":8081", internalMux)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go b.observe(ctx)
	errorsChannel := make(chan error, 3)
	go func() { errorsChannel <- publicServer.ListenAndServe() }()
	go func() { errorsChannel <- internalServer.ListenAndServe() }()
	go func() { errorsChannel <- proxy.serve(ctx) }()
	log.Printf("Minions in 32K bridge %s ready game=%d challenge=%d target=%s UDP=%s", bridgeBuild, cfg.GameID, cfg.ChallengeID, shortID(cfg.TargetID), cfg.PublicRemote)

	select {
	case <-ctx.Done():
	case err := <-errorsChannel:
		if !errors.Is(err, http.ErrServerClosed) && !errors.Is(err, net.ErrClosed) {
			log.Printf("bridge stopped: %v", err)
		}
		stop()
	}
	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = publicServer.Shutdown(shutdownContext)
	_ = internalServer.Shutdown(shutdownContext)
	proxy.close()
}

func bridgeHealthcheck(endpoint string) error {
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Get(endpoint)
	if err != nil {
		return fmt.Errorf("bridge health request: %w", err)
	}
	defer response.Body.Close()
	read, readErr := io.Copy(io.Discard, io.LimitReader(response.Body, 1025))
	if readErr != nil {
		return fmt.Errorf("read bridge health response: %w", readErr)
	}
	if read > 1024 {
		return errors.New("bridge health response is oversized")
	}
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("bridge health returned HTTP %d", response.StatusCode)
	}
	return nil
}

func loadConfiguration() (configuration, error) {
	gameID, err := positiveEnvironmentInteger("RSCTF_GAME_ID")
	if err != nil {
		return configuration{}, err
	}
	challengeID, err := positiveEnvironmentInteger("RSCTF_CHALLENGE_ID")
	if err != nil {
		return configuration{}, err
	}
	targetID := strings.TrimSpace(os.Getenv("MINIONS_TARGET_ID"))
	if !canonicalHash(targetID) {
		return configuration{}, errors.New("MINIONS_TARGET_ID must be a full container identity")
	}
	upstreamUDP, err := net.ResolveUDPAddr("udp", strings.TrimSpace(os.Getenv("MINIONS_UPSTREAM")))
	if err != nil || upstreamUDP.Port == 0 || upstreamUDP.IP == nil {
		return configuration{}, errors.New("MINIONS_UPSTREAM must be a numeric UDP address with a port")
	}
	healthURL, err := parseHTTPURL("MINIONS_HEALTH_URL", true)
	if err != nil {
		return configuration{}, err
	}
	rsctf, err := parseHTTPURL("RSCTF_URL", false)
	if err != nil {
		return configuration{}, err
	}
	publicRemote := environmentDefault("MINIONS_PUBLIC_REMOTE", "tcp.1pc.tf:4532")
	if !validRemote(publicRemote) {
		return configuration{}, errors.New("MINIONS_PUBLIC_REMOTE must be a host and UDP port")
	}
	stateDir := strings.TrimSpace(os.Getenv("MINIONS_STATE_DIR"))
	if stateDir == "" || !strings.HasPrefix(stateDir, "/") || strings.ContainsAny(stateDir, "\r\n") {
		return configuration{}, errors.New("MINIONS_STATE_DIR must be an absolute directory")
	}
	secretPath := environmentDefault("MINIONS_SECRET_FILE", "/run/secrets/bridge.json")
	raw, err := os.ReadFile(secretPath)
	if err != nil {
		return configuration{}, fmt.Errorf("read secret file: %w", err)
	}
	var secrets secretFile
	if err := decodeExact(raw, &secrets); err != nil {
		return configuration{}, fmt.Errorf("parse secret file: %w", err)
	}
	if !strings.HasPrefix(secrets.ObserverSecret, "koth_api_") || len(secrets.ObserverSecret) < 32 {
		return configuration{}, errors.New("observer secret is invalid")
	}
	if len(secrets.ReportToken) < 32 || strings.ContainsAny(secrets.ReportToken, "\r\n") {
		return configuration{}, errors.New("report token is invalid")
	}
	if len(secrets.Teams) < 2 || len(secrets.Teams) > 8 {
		return configuration{}, errors.New("between two and eight arena teams are required")
	}
	names, uuids, keys := map[string]bool{}, map[string]bool{}, map[string]bool{}
	scoringTeams := 0
	for _, team := range secrets.Teams {
		if team.Name == "" || len(team.Name) > 64 || strings.ContainsAny(team.Name, "\r\n") ||
			team.ArenaUUID == "" || len(team.ArenaUUID) > 128 || strings.ContainsAny(team.ArenaUUID, "\r\n") ||
			len(team.ArenaKey) < 32 || len(team.ArenaKey) > 256 || strings.ContainsAny(team.ArenaKey, "\r\n") ||
			names[team.Name] || uuids[team.ArenaUUID] || keys[team.ArenaKey] {
			return configuration{}, errors.New("arena team credentials are invalid or duplicated")
		}
		names[team.Name], uuids[team.ArenaUUID], keys[team.ArenaKey] = true, true, true
		if team.ScoringEligible {
			scoringTeams++
		}
	}
	if scoringTeams == 0 {
		return configuration{}, errors.New("at least one scoring-eligible arena team is required")
	}
	return configuration{
		GameID: gameID, ChallengeID: challengeID, TargetID: targetID,
		UpstreamUDP: upstreamUDP, HealthURL: healthURL, RSCTF: rsctf,
		PublicRemote: publicRemote, StateDir: stateDir, Secrets: secrets,
	}, nil
}

func transport() *http.Transport {
	return &http.Transport{
		Proxy: http.ProxyFromEnvironment, MaxIdleConns: 32, MaxIdleConnsPerHost: 16,
		IdleConnTimeout: 45 * time.Second, ResponseHeaderTimeout: 8 * time.Second,
		DisableCompression: true,
	}
}

func secureServer(address string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr: address, Handler: securityHeaders(handler), ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout: 15 * time.Second, WriteTimeout: 15 * time.Second,
		IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 * 1024,
	}
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}

func (b *bridge) landing(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=300")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"name": "Minions in 32K", "protocol": "minions/0", "transport": "QUIC/UDP",
		"remote": b.cfg.PublicRemote, "players": len(b.cfg.Secrets.Teams),
		"source": "https://github.com/project-sekai-ctf/sekaictf-2026/tree/main/game/minions-in-16k/challenge",
	})
}

func (b *bridge) createSession(w http.ResponseWriter, r *http.Request) {
	if body, err := readLimited(w, r, 1); err != nil || len(body) != 0 {
		writeProblem(w, http.StatusBadRequest, "session request must have an empty body")
		return
	}
	token, ok := bearerCapability(r.Header.Get("Authorization"))
	if !ok {
		writeProblem(w, http.StatusUnauthorized, "invalid event capability")
		return
	}
	identity, status, err := b.authenticate(r.Context(), token)
	if err != nil {
		if status == 0 || status >= 500 {
			writeProblem(w, http.StatusServiceUnavailable, "identity service unavailable")
		} else {
			writeProblem(w, http.StatusUnauthorized, "invalid event capability")
		}
		return
	}
	team, found := b.teamByName(identity.TeamName)
	if !found {
		writeProblem(w, http.StatusForbidden, "this team has no arena slot")
		return
	}
	if !team.ScoringEligible {
		writeProblem(w, http.StatusForbidden, "this arena slot is exhibition-only")
		return
	}
	now := time.Now().UTC()
	if err := b.rememberSession(team.Name, liveSession{TokenHash: identity.TeamID, LastSeen: now}); err != nil {
		log.Printf("arena identity persistence failed team=%q: %v", team.Name, err)
		writeProblem(w, http.StatusServiceUnavailable, "arena identity could not be retained")
		return
	}
	if err := b.ensureArena(r.Context()); err != nil {
		var retry *arenaRetryError
		if errors.As(err, &retry) {
			writeArenaRetryProblem(w, time.Now().UTC(), retry.retryAt)
			return
		}
		writeProblem(w, http.StatusServiceUnavailable, "arena could not start")
		return
	}
	arenaStatus, err := b.fetchArenaStatus(r.Context())
	if err != nil || arenaStatus.Status != "running" {
		writeProblem(w, http.StatusServiceUnavailable, "arena did not enter a pinned running state")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Pragma", "no-cache")
	_ = json.NewEncoder(w).Encode(arenaSession{
		Protocol: "minions/0", Remote: b.cfg.PublicRemote, Name: team.Name,
		Key: team.ArenaKey, CertHash: arenaStatus.CertHash,
	})
	log.Printf("arena credential issued team=%q", team.Name)
}

func (b *bridge) ensureArena(ctx context.Context) error {
	b.arenaMu.Lock()
	defer b.arenaMu.Unlock()
	status, err := b.fetchArenaStatus(ctx)
	if err != nil {
		return err
	}
	if status.Status == "idle" {
		current, err := b.fetchContext(ctx)
		if err != nil {
			var upstream *upstreamHTTPError
			if errors.As(err, &upstream) && upstream.status == http.StatusConflict {
				return &arenaRetryError{retryAt: time.Now().UTC().Add(arenaRetryReady)}
			}
			return err
		}
		now := time.Now().UTC()
		cycleEndsAt := time.UnixMilli(current.CycleEndsAt).UTC()
		if !arenaWindowAllowsStart(now, cycleEndsAt) {
			retryAt := cycleEndsAt.Add(arenaRetryReady)
			if !retryAt.After(now) {
				retryAt = now.Add(arenaRetryReady)
			}
			return &arenaRetryError{retryAt: retryAt}
		}
		timer := time.NewTimer(readyWindow)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
		}
	}
	payload := arenaStartRequest{Players: make([]arenaStartPlayer, 0, len(b.cfg.Secrets.Teams))}
	for _, team := range b.cfg.Secrets.Teams {
		payload.Players = append(payload.Players, arenaStartPlayer{
			UUID: team.ArenaUUID, Key: team.ArenaKey, Name: team.Name, Bot: !team.ScoringEligible,
		})
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	endpoint := b.cfg.HealthURL.ResolveReference(&url.URL{Path: "/internal/start"})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+b.cfg.Secrets.ReportToken)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "rsctf-minions-bridge/2")
	response, err := b.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, 8*1024+1))
	if err != nil || len(responseBody) > 8*1024 {
		return fmt.Errorf("arena start returned HTTP %d", response.StatusCode)
	}
	if response.StatusCode == http.StatusConflict {
		return &arenaRetryError{retryAt: time.Now().UTC().Add(arenaRetryReady)}
	}
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusCreated {
		return &upstreamHTTPError{service: "arena start", status: response.StatusCode}
	}
	return nil
}

func arenaWindowAllowsStart(now, cycleEndsAt time.Time) bool {
	required := arenaStaging + time.Duration(scoringLegCount)*scoringLegLength + arenaStartMargin
	return !now.Add(required).After(cycleEndsAt)
}

func (b *bridge) fetchArenaStatus(ctx context.Context) (arenaRuntimeStatus, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, b.cfg.HealthURL.String(), nil)
	if err != nil {
		return arenaRuntimeStatus{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "rsctf-minions-bridge/2")
	response, err := b.client.Do(request)
	if err != nil {
		return arenaRuntimeStatus{}, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 8*1024+1))
	if err != nil || len(raw) > 8*1024 || response.StatusCode != http.StatusOK {
		return arenaRuntimeStatus{}, fmt.Errorf("arena status returned HTTP %d", response.StatusCode)
	}
	var status arenaRuntimeStatus
	if decodeExact(raw, &status) != nil {
		return arenaRuntimeStatus{}, errors.New("arena status is invalid")
	}
	expectedNPCs := 0
	for _, team := range b.cfg.Secrets.Teams {
		if !team.ScoringEligible {
			expectedNPCs++
		}
	}
	idleRosterValid := status.Status == "idle" &&
		((status.Players == 0 && status.NPCs == 0) ||
			(status.Players == len(b.cfg.Secrets.Teams) && status.NPCs == expectedNPCs))
	runningRosterValid := status.Status == "running" &&
		status.Players == len(b.cfg.Secrets.Teams) && status.NPCs == expectedNPCs
	if status.Name != "Minions in 32K" ||
		status.Protocol != "minions/0" || status.ProtocolVersion != 13 || status.RenderWidth != 256 || status.RenderHeight != 128 ||
		!canonicalHash(status.CertHash) || status.Build == "" || len(status.Build) > 128 ||
		(!idleRosterValid && !runningRosterValid) ||
		(status.Status != "idle" && status.Status != "running") ||
		(status.Status == "running" && (!validMatchID(status.MatchID) || status.ExpectedEndUnixMS <= 0)) {
		return arenaRuntimeStatus{}, errors.New("arena status is invalid")
	}
	return status, nil
}

func (b *bridge) authenticate(ctx context.Context, token string) (capabilityIdentity, int, error) {
	body, _ := json.Marshal(map[string]any{"token": token, "gameId": b.cfg.GameID, "challengeId": b.cfg.ChallengeID})
	endpoint := b.cfg.RSCTF.ResolveReference(&url.URL{Path: "/api/v1/koth/capability/authenticate"})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return capabilityIdentity{}, 0, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "rsctf-minions-bridge/2")
	response, err := b.client.Do(request)
	if err != nil {
		return capabilityIdentity{}, 0, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxJSONReply+1))
	if err != nil || len(raw) > maxJSONReply {
		return capabilityIdentity{}, response.StatusCode, errors.New("invalid identity response")
	}
	if response.StatusCode != http.StatusOK {
		return capabilityIdentity{}, response.StatusCode, errors.New("identity rejected")
	}
	var identity capabilityIdentity
	if decodeExact(raw, &identity) != nil || !canonicalHash(identity.TeamID) || identity.TeamName == "" || len(identity.TeamName) > 64 {
		return capabilityIdentity{}, response.StatusCode, errors.New("invalid identity response")
	}
	return identity, response.StatusCode, nil
}

func (b *bridge) acceptReport(w http.ResponseWriter, r *http.Request) {
	provided, ok := bearerToken(r.Header.Get("Authorization"))
	if !ok || !sameSecret(provided, b.cfg.Secrets.ReportToken) {
		writeProblem(w, http.StatusUnauthorized, "invalid match reporter")
		return
	}
	body, err := readLimited(w, r, maxReportBody)
	if err != nil {
		writeProblem(w, http.StatusBadRequest, "invalid match report")
		return
	}
	var report matchReport
	if decodeExact(body, &report) != nil || b.validateReport(report, time.Now().UTC()) != nil {
		writeProblem(w, http.StatusBadRequest, "invalid match report")
		return
	}
	digest := sha256.Sum256(body)
	now := time.Now().UTC()

	b.mu.Lock()
	idempotent, err := b.storeReportLocked(report, digest, now)
	if err == nil {
		err = b.persistStateLocked()
	}
	b.mu.Unlock()
	if err != nil {
		if errors.Is(err, errStatePersistence) {
			log.Printf("match state persistence failed id=%q sequence=%d: %v", report.MatchID, report.Sequence, err)
			writeProblem(w, http.StatusServiceUnavailable, "match state could not be retained")
		} else {
			writeProblem(w, http.StatusConflict, err.Error())
		}
		return
	}
	if !idempotent {
		log.Printf("match report accepted id=%q kind=%s sequence=%d", report.MatchID, report.Kind, report.Sequence)
	}
	w.WriteHeader(http.StatusNoContent)
}

func (b *bridge) storeReportLocked(report matchReport, digest [32]byte, now time.Time) (bool, error) {
	if b.matches == nil {
		b.matches = make(map[string]*matchSequence)
	}
	state := b.matches[report.MatchID]
	if report.Kind == "leg" {
		if state == nil {
			if report.Sequence != 1 {
				return false, errors.New("match reports must arrive in sequence")
			}
			state = &matchSequence{LegCount: report.LegCount, LegKeys: make(map[uint32]string)}
			b.matches[report.MatchID] = state
		}
		if state.LegCount != report.LegCount || state.Final != nil {
			return false, errors.New("match report sequence was already finalized")
		}
		if key, exists := state.LegKeys[report.Sequence]; exists {
			if b.waves[key].Digest != digest {
				return false, errors.New("match leg was already finalized with different evidence")
			}
			return true, nil
		}
		if report.Sequence != uint32(len(state.LegKeys))+1 {
			return false, errors.New("match reports must arrive in sequence")
		}
		if report.Sequence > 1 {
			previous := b.waves[state.LegKeys[report.Sequence-1]].Result
			if previous.EndedUnixMS != report.StartedUnixMS {
				return false, errors.New("match leg windows must be contiguous")
			}
		}
		if err := b.validateLegPrefixLocked(state, report); err != nil {
			return false, err
		}
		identities := make(map[string]string)
		for _, player := range report.Players {
			team, scoringEligible := b.teamByName(player.Name)
			if !scoringEligible || !team.ScoringEligible {
				continue
			}
			if session, exists := b.sessions[player.Name]; exists &&
				now.Sub(session.LastSeen) <= activeGrace {
				identities[player.Name] = session.TokenHash
			}
		}
		idHash := sha256.Sum256([]byte(report.MatchID))
		waveID := fmt.Sprintf("m32-%s-%s-l%02d", shortID(b.cfg.TargetID), hex.EncodeToString(idHash[:8]), report.Sequence)
		key := fmt.Sprintf("%s:%d", report.MatchID, report.Sequence)
		endedAt := time.UnixMilli(report.EndedUnixMS).UTC()
		b.waves[key] = finalizedWave{ID: waveID, EndedAt: endedAt, Result: report, Identities: identities, Digest: digest}
		state.LegKeys[report.Sequence] = key
		state.LastEndedAt = endedAt
	} else {
		if state == nil || uint32(len(state.LegKeys)) != report.LegCount {
			return false, errors.New("final report requires every scoring leg")
		}
		if state.Final != nil {
			if state.FinalDigest != digest {
				return false, errors.New("match reconciliation was already finalized with different evidence")
			}
			return true, nil
		}
		if err := b.validateReconciliationLocked(state, report); err != nil {
			return false, err
		}
		copy := report
		state.Final = &copy
		state.FinalDigest = digest
		state.LastEndedAt = time.UnixMilli(report.EndedUnixMS).UTC()
	}
	b.purgeExpiredLocked(now)
	return false, nil
}

func (b *bridge) validateReconciliationLocked(state *matchSequence, report matchReport) error {
	if _, retained := b.replays[report.MatchID]; !retained {
		return errors.New("final report requires retained replay evidence")
	}
	first := b.waves[state.LegKeys[1]].Result
	last := b.waves[state.LegKeys[state.LegCount]].Result
	if report.StartedUnixMS != first.StartedUnixMS || report.EndedUnixMS != last.EndedUnixMS {
		return errors.New("final report does not cover the scoring legs")
	}
	totals := make(map[string]reportPlayer, len(report.Players))
	for sequence := uint32(1); sequence <= state.LegCount; sequence++ {
		leg := b.waves[state.LegKeys[sequence]].Result
		for _, player := range leg.Players {
			total := totals[player.TeamUUID]
			total.TeamUUID, total.Name = player.TeamUUID, player.Name
			total.Kills += player.Kills
			total.ConfirmedKills += player.ConfirmedKills
			total.Deaths += player.Deaths
			total.Takeovers += player.Takeovers
			total.IntegrityStatus = mergeIntegrityStatus(total.IntegrityStatus, player.IntegrityStatus)
			totals[player.TeamUUID] = total
		}
	}
	for _, player := range report.Players {
		total := totals[player.TeamUUID]
		if total.Name != player.Name || total.Kills != player.Kills || total.ConfirmedKills != player.ConfirmedKills || total.Deaths != player.Deaths || total.Takeovers != player.Takeovers || total.IntegrityStatus != player.IntegrityStatus {
			return errors.New("final report does not reconcile with the scoring legs")
		}
	}
	return nil
}

func (b *bridge) purgeExpiredLocked(now time.Time) {
	for name, session := range b.sessions {
		if now.Sub(session.LastSeen) > activeGrace {
			delete(b.sessions, name)
		}
	}
	for matchID, state := range b.matches {
		if state.LastEndedAt.IsZero() || now.Sub(state.LastEndedAt) <= waveRetention {
			continue
		}
		for _, key := range state.LegKeys {
			delete(b.waves, key)
		}
		delete(b.matches, matchID)
	}
}

func (b *bridge) validateReport(report matchReport, now time.Time) error {
	if !validMatchID(report.MatchID) || len(report.Players) != len(b.cfg.Secrets.Teams) ||
		report.StartedUnixMS <= 0 || report.EndedUnixMS < report.StartedUnixMS ||
		report.DurationMS != report.EndedUnixMS-report.StartedUnixMS ||
		report.EndedUnixMS > now.Add(30*time.Second).UnixMilli() || report.EndedUnixMS < now.Add(-30*time.Minute).UnixMilli() {
		return errors.New("invalid match metadata")
	}
	legMilliseconds := int64(scoringLegLength / time.Millisecond)
	matchMilliseconds := legMilliseconds * int64(scoringLegCount)
	if report.LegCount != scoringLegCount ||
		(report.Kind == "leg" && (report.Sequence < 1 || report.Sequence > scoringLegCount || report.DurationMS != legMilliseconds)) ||
		(report.Kind == "final" && (report.Sequence != scoringLegCount+1 || report.DurationMS != matchMilliseconds)) ||
		(report.Kind != "leg" && report.Kind != "final") {
		return errors.New("invalid scoring sequence")
	}
	expected := make(map[string]teamSecret, len(b.cfg.Secrets.Teams))
	for _, team := range b.cfg.Secrets.Teams {
		expected[team.ArenaUUID] = team
	}
	counterMultiplier := uint32(1)
	if report.Kind == "final" {
		counterMultiplier = scoringLegCount
	}
	maximumDeaths := maxLegDeaths * counterMultiplier
	maximumTakeovers := maxLegTakeovers * counterMultiplier
	seen := make(map[string]bool, len(report.Players))
	var totalKills, totalDeaths uint64
	for _, player := range report.Players {
		team, exists := expected[player.TeamUUID]
		if !exists || seen[player.TeamUUID] || player.Name != team.Name || player.Rank < 1 ||
			player.Rank > uint32(len(report.Players)) || player.Deaths > maximumDeaths ||
			player.Takeovers > maximumTakeovers ||
			!validIntegrityStatus(player.IntegrityStatus) ||
			(report.Kind == "final" && player.ConfirmedKills > player.Kills) {
			return errors.New("invalid player result")
		}
		seen[player.TeamUUID] = true
		totalKills += uint64(player.Kills)
		totalDeaths += uint64(player.Deaths)
	}
	// Every authoritative frag has exactly one corresponding death; scenario
	// hazards may add deaths without a killer. This invariant gives a tight,
	// field-independent ceiling and rejects forged counter inflation.
	if totalKills > totalDeaths {
		return errors.New("kill counters do not reconcile with deaths")
	}
	ranked := append([]reportPlayer(nil), report.Players...)
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].Connected != ranked[j].Connected {
			return ranked[i].Connected
		}
		if ranked[i].ConfirmedKills != ranked[j].ConfirmedKills {
			return ranked[i].ConfirmedKills > ranked[j].ConfirmedKills
		}
		if ranked[i].Kills != ranked[j].Kills {
			return ranked[i].Kills > ranked[j].Kills
		}
		if ranked[i].Deaths != ranked[j].Deaths {
			return ranked[i].Deaths < ranked[j].Deaths
		}
		return ranked[i].TeamUUID < ranked[j].TeamUUID
	})
	var rank uint32
	var previous *reportPlayer
	for index := range ranked {
		current := &ranked[index]
		if previous == nil || current.Connected != previous.Connected || current.ConfirmedKills != previous.ConfirmedKills || current.Kills != previous.Kills || current.Deaths != previous.Deaths {
			rank++
		}
		if current.Rank != rank {
			return errors.New("inconsistent player rank")
		}
		previous = current
	}
	return nil
}

func (b *bridge) observe(ctx context.Context) {
	var lastContext, lastDigest string
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
		current, err := b.fetchContext(ctx)
		if err != nil {
			log.Printf("referee waiting: %v", err)
			continue
		}
		body, waveCount, teamCount, err := b.buildObservation(current)
		if err != nil {
			log.Printf("referee waiting: %v", err)
			continue
		}
		digestBytes := sha256.Sum256(body)
		digest := hex.EncodeToString(digestBytes[:])
		if current.Context == lastContext && digest == lastDigest {
			continue
		}
		accepted, err := b.submitObservation(ctx, body)
		if err != nil {
			log.Printf("referee waiting: %v", err)
			continue
		}
		if !accepted.Accepted || accepted.RoundNumber != current.RoundNumber || accepted.CycleNumber != current.CycleNumber || accepted.SubmittedWaves != waveCount || accepted.SubmittedTeams != teamCount || accepted.RecognizedTeams != teamCount {
			log.Printf("referee waiting: incomplete RSCTF acknowledgement")
			continue
		}
		lastContext, lastDigest = current.Context, digest
		log.Printf("snapshot accepted round=%d cycle=%d waves=%d teams=%d", accepted.RoundNumber, accepted.CycleNumber, waveCount, teamCount)
	}
}

func (b *bridge) fetchContext(ctx context.Context) (observerContext, error) {
	path := fmt.Sprintf("/api/v1/koth/games/%d/challenges/%d/context", b.cfg.GameID, b.cfg.ChallengeID)
	endpoint := b.cfg.RSCTF.ResolveReference(&url.URL{Path: path})
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return observerContext{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "rsctf-minions-referee/2")
	response, err := b.client.Do(request)
	if err != nil {
		return observerContext{}, errors.New("RSCTF context unavailable")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxJSONReply+1))
	if err != nil || len(raw) > maxJSONReply {
		return observerContext{}, errors.New("RSCTF context response is invalid")
	}
	if response.StatusCode != http.StatusOK {
		return observerContext{}, &upstreamHTTPError{service: "RSCTF context", status: response.StatusCode}
	}
	var value observerContext
	if decodeExact(raw, &value) != nil || !canonicalHash(value.Context) || value.CycleNumber <= 0 ||
		value.ResetAttempt <= 0 || value.RoundNumber <= 0 || value.WaveWindowStartsAt <= 0 ||
		value.WaveWindowStartsAt >= value.WaveWindowEndsAt || value.CycleEndsAt < value.WaveWindowEndsAt ||
		value.GeneratedAt <= 0 {
		return observerContext{}, errors.New("RSCTF context is invalid")
	}
	if len(value.ObjectiveIDs) != 0 && !equalStrings(value.ObjectiveIDs, objectiveIDs) {
		return observerContext{}, errors.New("objective schema changed")
	}
	for _, tokenHash := range value.EligibleTokenHashes {
		if !canonicalHash(tokenHash) {
			return observerContext{}, errors.New("eligible roster is invalid")
		}
	}
	return value, nil
}

func (b *bridge) buildObservation(current observerContext) ([]byte, int, int, error) {
	eligible := make(map[string]bool, len(current.EligibleTokenHashes))
	for _, tokenHash := range current.EligibleTokenHashes {
		if eligible[tokenHash] {
			return nil, 0, 0, errors.New("eligible roster contains duplicates")
		}
		eligible[tokenHash] = true
	}
	b.mu.Lock()
	waves := make([]finalizedWave, 0, len(b.waves))
	for _, wave := range b.waves {
		ended := wave.EndedAt.UnixMilli()
		if ended >= current.WaveWindowStartsAt && ended < current.WaveWindowEndsAt {
			waves = append(waves, wave)
		}
	}
	b.mu.Unlock()
	sort.Slice(waves, func(i, j int) bool {
		if waves[i].EndedAt.Equal(waves[j].EndedAt) {
			return waves[i].ID < waves[j].ID
		}
		return waves[i].EndedAt.Before(waves[j].EndedAt)
	})
	if len(waves) > 64 {
		return nil, 0, 0, errors.New("match window contains too many waves")
	}

	out := observation{Context: current.Context, ObjectiveIDs: objectiveIDs, Waves: make([]waveEvidence, 0, len(waves))}
	uniqueTeams := make(map[string]bool)
	for _, wave := range waves {
		scoringNames := make(map[string]bool, len(wave.Identities))
		for name, tokenHash := range wave.Identities {
			if eligible[tokenHash] {
				scoringNames[name] = true
			}
		}
		possible, scores, crownName := scoreMatch(wave.Result, scoringNames)
		teams := make([]teamEvidence, 0, len(wave.Identities))
		for _, player := range wave.Result.Players {
			tokenHash := wave.Identities[player.Name]
			if tokenHash == "" || !eligible[tokenHash] {
				continue
			}
			earned := scores[player.Name]
			if earned > possible {
				earned = possible
			}
			activity := int64(0)
			if player.Connected && scoringIntegrity(player.IntegrityStatus) {
				activity = 1
			} else {
				earned = 0
			}
			teams = append(teams, teamEvidence{
				TokenHash: tokenHash, Activity: evidenceRatio{Earned: activity, Possible: 1},
				Objectives: []evidenceRatio{{Earned: earned, Possible: possible}},
				IsCrown:    player.Name == crownName,
			})
			uniqueTeams[tokenHash] = true
		}
		if len(teams) == 0 {
			continue
		}
		sort.Slice(teams, func(i, j int) bool { return teams[i].TokenHash < teams[j].TokenHash })
		out.Waves = append(out.Waves, waveEvidence{WaveID: wave.ID, EndedAt: wave.EndedAt.UnixMilli(), Teams: teams})
	}
	body, err := json.Marshal(out)
	return body, len(out.Waves), len(uniqueTeams), err
}

func scoreMatch(report matchReport, scoringNames map[string]bool) (int64, map[string]int64, string) {
	scores := make(map[string]int64, len(report.Players))
	possible := int64(1)
	for _, player := range report.Players {
		if !scoringNames[player.Name] || !player.Connected || !scoringIntegrity(player.IntegrityStatus) || player.ConfirmedKills == 0 {
			scores[player.Name] = 0
			continue
		}
		score := int64(player.ConfirmedKills)
		scores[player.Name] = score
		if score > possible {
			possible = score
		}
	}
	// Base evidence is relative confirmed performance: confirmed / best
	// confirmed. Crown is a separate engagement bonus. Total frags and then
	// fewer deaths resolve a confirmed-score tie; an exact tie has no Crown.
	candidates := make([]reportPlayer, 0, len(report.Players))
	for _, player := range report.Players {
		if scoringNames[player.Name] && player.Connected && scoringIntegrity(player.IntegrityStatus) && player.ConfirmedKills > 0 {
			candidates = append(candidates, player)
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].ConfirmedKills != candidates[j].ConfirmedKills {
			return candidates[i].ConfirmedKills > candidates[j].ConfirmedKills
		}
		if candidates[i].Kills != candidates[j].Kills {
			return candidates[i].Kills > candidates[j].Kills
		}
		if candidates[i].Deaths != candidates[j].Deaths {
			return candidates[i].Deaths < candidates[j].Deaths
		}
		return candidates[i].Name < candidates[j].Name
	})
	crownName := ""
	if len(candidates) > 0 && (len(candidates) == 1 || candidates[0].ConfirmedKills != candidates[1].ConfirmedKills || candidates[0].Kills != candidates[1].Kills || candidates[0].Deaths != candidates[1].Deaths) {
		crownName = candidates[0].Name
	}
	return possible, scores, crownName
}

func (b *bridge) submitObservation(ctx context.Context, body []byte) (acceptedObservation, error) {
	b.mu.Lock()
	timestamp := time.Now().UnixMilli()
	if timestamp <= b.lastTimestamp {
		timestamp = b.lastTimestamp + 1
	}
	b.lastTimestamp = timestamp
	b.mu.Unlock()
	message := []byte(fmt.Sprintf("%d.%d.%d.", timestamp, b.cfg.GameID, b.cfg.ChallengeID))
	message = append(message, body...)
	mac := hmac.New(sha256.New, []byte(b.cfg.Secrets.ObserverSecret))
	_, _ = mac.Write(message)
	signature := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	path := fmt.Sprintf("/api/v1/koth/games/%d/challenges/%d/observations", b.cfg.GameID, b.cfg.ChallengeID)
	endpoint := b.cfg.RSCTF.ResolveReference(&url.URL{Path: path})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return acceptedObservation{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-RSCTF-Timestamp", strconv.FormatInt(timestamp, 10))
	request.Header.Set("X-RSCTF-Signature", signature)
	request.Header.Set("User-Agent", "rsctf-minions-referee/2")
	response, err := b.client.Do(request)
	if err != nil {
		return acceptedObservation{}, errors.New("RSCTF observation endpoint unavailable")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxJSONReply+1))
	if err != nil || len(raw) > maxJSONReply || response.StatusCode != http.StatusOK {
		return acceptedObservation{}, fmt.Errorf("RSCTF observation returned HTTP %d", response.StatusCode)
	}
	var accepted acceptedObservation
	if decodeExact(raw, &accepted) != nil {
		return acceptedObservation{}, errors.New("RSCTF acknowledgement is invalid")
	}
	return accepted, nil
}

func (b *bridge) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if b.proxy == nil || !b.proxy.healthy() || !b.dependencyHealthy(ctx, b.cfg.HealthURL) || !b.dependencyHealthy(ctx, b.cfg.RSCTF.ResolveReference(&url.URL{Path: "/healthz"})) {
		writeProblem(w, http.StatusServiceUnavailable, "bridge dependency unavailable")
		return
	}
	sessions, rejected := b.proxy.metrics()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok", "build": bridgeBuild, "udpSessions": sessions, "udpAdmissionsRejected": rejected,
	})
}

func (b *bridge) dependencyHealthy(ctx context.Context, endpoint *url.URL) bool {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return false
	}
	request.Header.Set("Accept", "text/plain, application/json")
	request.Header.Set("User-Agent", "rsctf-minions-health/2")
	response, err := b.client.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1025))
	return err == nil && len(body) <= 1024 && response.StatusCode == http.StatusOK
}

func (b *bridge) teamByName(name string) (teamSecret, bool) {
	for _, team := range b.cfg.Secrets.Teams {
		if team.Name == name {
			return team, true
		}
	}
	return teamSecret{}, false
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
		perIP:           make(map[string]*admissionBucket),
		global:          newAdmissionBucket(now, newSessionRate, newSessionBurst),
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
		n, client, err := p.public.ReadFromUDP(buffer)
		if err != nil {
			return err
		}
		if n == 0 {
			continue
		}
		session, err := p.session(client)
		if err != nil {
			continue
		}
		if _, err := session.upstream.Write(buffer[:n]); err != nil {
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
	if len(p.sessions) >= p.maximumSessions || perIPCount >= p.maximumPerIP ||
		!p.admitNewSessionLocked(clientIP, now) {
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
		n, err := session.upstream.Read(buffer)
		if err != nil {
			p.drop(key, session)
			return
		}
		if n > 0 {
			if _, err := p.public.WriteToUDP(buffer[:n], session.client); err != nil {
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

func readLimited(w http.ResponseWriter, r *http.Request, limit int64) ([]byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	defer r.Body.Close()
	return io.ReadAll(r.Body)
}

func writeProblem(w http.ResponseWriter, status int, title string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"title": title, "status": status})
}

func writeArenaRetryProblem(w http.ResponseWriter, now, retryAt time.Time) {
	remaining := retryAt.Sub(now)
	if remaining < time.Second {
		remaining = time.Second
	}
	retrySeconds := int64((remaining + time.Second - 1) / time.Second)
	w.Header().Set("Retry-After", strconv.FormatInt(retrySeconds, 10))
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusConflict)
	_ = json.NewEncoder(w).Encode(arenaRetryProblem{
		Title:         "Crown reset protection is active",
		Detail:        "The current cycle is too close to its reset to safely start a complete five-leg match.",
		Status:        http.StatusConflict,
		Code:          "arena_window_pending",
		RetryAtUnixMS: retryAt.UnixMilli(),
	})
}

func decodeExact(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return errors.New("multiple JSON values")
	}
	return nil
}

func bearerCapability(header string) (string, bool) {
	token, ok := bearerToken(header)
	if !ok || len(token) < 13 || len(token) > 133 || !strings.HasPrefix(token, "koth_") {
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

func bearerToken(header string) (string, bool) {
	if !strings.HasPrefix(header, "Bearer ") {
		return "", false
	}
	token := strings.TrimPrefix(header, "Bearer ")
	return token, token != "" && token == strings.TrimSpace(token) && !strings.ContainsAny(token, " \t\r\n")
}

func sameSecret(left, right string) bool {
	leftHash, rightHash := sha256.Sum256([]byte(left)), sha256.Sum256([]byte(right))
	return hmac.Equal(leftHash[:], rightHash[:])
}

func validMatchID(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, character := range value {
		if !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') &&
			!(character >= '0' && character <= '9') && character != '-' && character != '_' && character != '.' {
			return false
		}
	}
	return true
}

func validRemote(value string) bool {
	address, err := net.ResolveUDPAddr("udp", value)
	return err == nil && address.Port > 0
}

func positiveEnvironmentInteger(name string) (int, error) {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return value, nil
}

func parseHTTPURL(name string, allowPath bool) (*url.URL, error) {
	value, err := url.Parse(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value.Scheme != "http" || value.Host == "" || value.RawQuery != "" || value.Fragment != "" ||
		value.User != nil || (!allowPath && value.Path != "") || (allowPath && value.Path != "/healthz") {
		return nil, fmt.Errorf("%s is not a valid internal HTTP URL", name)
	}
	return value, nil
}

func environmentDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func canonicalHash(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')) {
			return false
		}
	}
	return true
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func shortID(value string) string {
	if len(value) > 12 {
		return value[:12]
	}
	return value
}
