package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	serverPath    = "/usr/local/bin/minions-server"
	botPath       = "/usr/local/bin/minions-headless"
	replayPath    = "/tmp/minions-replays"
	readyPath     = "/tmp/minions-arena-ready"
	defaultGrace  = 30
	defaultMatch  = 300
	maxPlayers    = 8
	qualityCache  = 15 * time.Second
	qualityRetry  = 2 * time.Second
	qualityPort   = 45433
	qualityWTPort = 45434
	maxStartBody  = 8 * 1024
)

var supervisorBuild = "dev"

var errRosterConflict = errors.New("arena roster changed while a match is running")

type config struct {
	reportToken  string
	certHash     string
	certDER      string
	keyDER       string
	publicRemote string
	managed      *managedReporterConfig
	graceSecs    int
	matchSecs    int
	roster       *arenaRoster
}

type arenaRoster struct {
	players           string
	botKeys           string
	botCount          int
	playerCount       int
	fingerprint       [32]byte
	participants      []startPlayer
	credentialByToken map[string]arenaCredential
	tokenByName       map[string]string
}

type startPlayer struct {
	UUID      string `json:"uuid"`
	Key       string `json:"key"`
	Name      string `json:"name"`
	Bot       bool   `json:"bot"`
	TokenHash string `json:"tokenHash,omitempty"`
}

type arenaCredential struct {
	Name string
	Key  string
}

type serverPlayer struct {
	UUID string `json:"uuid"`
	Key  string `json:"key"`
	Name string `json:"name"`
}

type startRequest struct {
	Players []startPlayer `json:"players"`
}

type arena struct {
	cfg      config
	reporter *managedReporter

	mu          sync.Mutex
	server      *exec.Cmd
	bot         *exec.Cmd
	matchID     string
	expectedEnd time.Time
	startedAt   time.Time
	lastError   string

	qualityMu     sync.Mutex
	qualityAt     time.Time
	qualityMode   string
	qualityPassed bool
}

type status struct {
	Name              string `json:"name"`
	Status            string `json:"status"`
	Protocol          string `json:"protocol"`
	ProtocolVersion   int    `json:"protocolVersion"`
	RenderWidth       int    `json:"renderWidth"`
	RenderHeight      int    `json:"renderHeight"`
	MatchID           string `json:"matchId,omitempty"`
	ExpectedEndUnixMS int64  `json:"expectedEndUnixMs,omitempty"`
	Players           int    `json:"players"`
	NPCs              int    `json:"npcs"`
	CertHash          string `json:"certHash"`
	Build             string `json:"build"`
}

type qualityStatus struct {
	Status          string `json:"status"`
	Probe           string `json:"probe"`
	Mode            string `json:"mode"`
	ProtocolVersion int    `json:"protocolVersion"`
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := supervisorHealthcheck("http://127.0.0.1:8080/healthz"); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	cfg, err := loadConfig()
	if err != nil {
		log.Fatalf("configuration: %v", err)
	}
	if err := os.MkdirAll(replayPath, 0o700); err != nil {
		log.Fatalf("replay directory: %v", err)
	}
	a := &arena{cfg: cfg}
	if cfg.managed != nil {
		a.reporter = newManagedReporter(*cfg.managed, cfg.reportToken, a)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)
	mux.HandleFunc("GET /qualityz", a.quality)
	mux.HandleFunc("POST /api/v1/arena-session", a.createSession)
	mux.HandleFunc("POST /internal/start", a.start)
	mux.HandleFunc("POST /internal/match-report", a.acceptReport)
	mux.HandleFunc("POST /internal/replay", a.acceptReplay)
	server := &http.Server{
		Addr: ":8080", Handler: securityHeaders(mux), ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second,
		IdleTimeout: 15 * time.Second, MaxHeaderBytes: 8 * 1024,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if a.reporter != nil {
		go a.reporter.run(ctx)
	}
	errorsChannel := make(chan error, 1)
	go func() { errorsChannel <- server.ListenAndServe() }()
	log.Printf("Minions in 32K arena supervisor %s ready", supervisorBuild)
	select {
	case <-ctx.Done():
	case err := <-errorsChannel:
		if !errors.Is(err, http.ErrServerClosed) {
			log.Printf("HTTP server stopped: %v", err)
		}
	}
	a.stop()
	shutdownContext, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdownContext)
}

func loadConfig() (config, error) {
	managed, err := loadManagedReporterConfig()
	if err != nil {
		return config{}, err
	}
	reportToken := strings.TrimSpace(os.Getenv("REPORT_TOKEN"))
	if reportToken == "" {
		seed := os.Getenv("RSCTF_FLAG")
		if managed != nil {
			seed = managed.Secret
		}
		reportToken, err = deriveReportToken(seed)
		if err != nil && managed == nil && strings.TrimSpace(os.Getenv("RSCTF_FLAG")) == "" {
			reportToken, err = randomReportToken()
		}
		if err != nil {
			return config{}, err
		}
	}
	if len(reportToken) < 32 || strings.ContainsAny(reportToken, " \t\r\n") {
		return config{}, errors.New("REPORT_TOKEN is invalid")
	}
	certDER, keyDER, certHash, err := loadTLSIdentity()
	if err != nil {
		return config{}, err
	}
	playerTokens := strings.TrimSpace(os.Getenv("PLAYER_TOKENS"))
	var roster *arenaRoster
	if playerTokens != "" {
		botCount, err := boundedInteger("MINIONS_BOT_COUNT", 0, 7, 0)
		if err != nil {
			return config{}, err
		}
		value, err := legacyRoster(playerTokens, strings.TrimSpace(os.Getenv("MINIONS_BOT_KEYS")), botCount)
		if err != nil {
			return config{}, err
		}
		roster = &value
	}
	graceSecs, err := boundedInteger("MINIONS_GRACE_SECS", 5, 120, defaultGrace)
	if err != nil {
		return config{}, err
	}
	matchSecs, err := boundedInteger("MINIONS_MATCH_SECS", 30, 1800, defaultMatch)
	if err != nil {
		return config{}, err
	}
	publicRemote := environmentDefault("MINIONS_PUBLIC_REMOTE", "tcp.1pc.tf:4532")
	if !validRemote(publicRemote) {
		return config{}, errors.New("MINIONS_PUBLIC_REMOTE must be a host and UDP port")
	}
	return config{
		reportToken: reportToken, certHash: certHash, certDER: certDER, keyDER: keyDER,
		publicRemote: publicRemote,
		managed:      managed,
		graceSecs:    graceSecs, matchSecs: matchSecs,
		roster: roster,
	}, nil
}

func environmentDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func randomReportToken() (string, error) {
	value := make([]byte, 32)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate arena controller secret: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func deriveReportToken(flag string) (string, error) {
	flag = strings.TrimSpace(flag)
	if len(flag) < 32 || len(flag) > 512 || strings.ContainsAny(flag, "\r\n") {
		return "", errors.New("RSCTF_FLAG cannot derive the arena controller secret")
	}
	mac := hmac.New(sha256.New, []byte(flag))
	_, _ = mac.Write([]byte("minions-in-32k/report-token/v1"))
	return hex.EncodeToString(mac.Sum(nil)), nil
}

func loadTLSIdentity() (string, string, string, error) {
	certDER := strings.TrimSpace(os.Getenv("WT_CERT_DER_B64"))
	keyDER := strings.TrimSpace(os.Getenv("WT_KEY_DER_B64"))
	if (certDER == "") != (keyDER == "") {
		return "", "", "", errors.New("WT certificate and key must be supplied together")
	}
	if certDER == "" {
		var err error
		certDER, keyDER, err = generateTLSIdentity(time.Now().UTC())
		if err != nil {
			return "", "", "", fmt.Errorf("generate arena TLS identity: %w", err)
		}
	}
	decoded, err := base64.StdEncoding.DecodeString(certDER)
	if err != nil {
		return "", "", "", errors.New("WT_CERT_DER_B64 is invalid")
	}
	if _, err := x509.ParseCertificate(decoded); err != nil {
		return "", "", "", errors.New("WT_CERT_DER_B64 is invalid")
	}
	if decodedKey, err := base64.StdEncoding.DecodeString(keyDER); err != nil {
		return "", "", "", errors.New("WT_KEY_DER_B64 is invalid")
	} else if _, err := x509.ParsePKCS8PrivateKey(decodedKey); err != nil {
		return "", "", "", errors.New("WT_KEY_DER_B64 is invalid")
	}
	hash := sha256.Sum256(decoded)
	return certDER, keyDER, hex.EncodeToString(hash[:]), nil
}

func generateTLSIdentity(now time.Time) (string, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", "", err
	}
	serialLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return "", "", err
	}
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "localhost"},
		NotBefore:    now.Add(-5 * time.Minute), NotAfter: now.Add(13 * 24 * time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:    []string{"localhost"},
	}
	certificate, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return "", "", err
	}
	privateKey, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", "", err
	}
	return base64.StdEncoding.EncodeToString(certificate), base64.StdEncoding.EncodeToString(privateKey), nil
}

func validateBotRoster(rawPlayers, rawBotKeys string, botCount int) (int, error) {
	var players []struct {
		Key string `json:"key"`
	}
	if err := json.Unmarshal([]byte(rawPlayers), &players); err != nil || len(players) < 1 || len(players) > maxPlayers {
		return 0, fmt.Errorf("PLAYER_TOKENS must contain between 1 and %d players", maxPlayers)
	}
	playerKeys := make(map[string]bool, len(players))
	for _, player := range players {
		if player.Key == "" || playerKeys[player.Key] {
			return 0, errors.New("PLAYER_TOKENS contains an invalid or duplicate key")
		}
		playerKeys[player.Key] = true
	}
	if botCount >= len(players) {
		return 0, errors.New("the arena must retain at least one human slot")
	}
	if botCount == 0 {
		if rawBotKeys != "" {
			return 0, errors.New("MINIONS_BOT_KEYS must be empty when MINIONS_BOT_COUNT is zero")
		}
		return len(players), nil
	}
	botKeys := strings.Split(rawBotKeys, ",")
	if len(botKeys) != botCount {
		return 0, errors.New("MINIONS_BOT_KEYS does not match MINIONS_BOT_COUNT")
	}
	seen := make(map[string]bool, len(botKeys))
	for _, key := range botKeys {
		if key == "" || !playerKeys[key] || seen[key] {
			return 0, errors.New("MINIONS_BOT_KEYS contains an unknown or duplicate key")
		}
		seen[key] = true
	}
	return len(players), nil
}

func legacyRoster(rawPlayers, rawBotKeys string, botCount int) (arenaRoster, error) {
	playerCount, err := validateBotRoster(rawPlayers, rawBotKeys, botCount)
	if err != nil {
		return arenaRoster{}, err
	}
	var players []serverPlayer
	if err := json.Unmarshal([]byte(rawPlayers), &players); err != nil {
		return arenaRoster{}, errors.New("PLAYER_TOKENS is invalid")
	}
	botSet := make(map[string]bool, botCount)
	if rawBotKeys != "" {
		for _, key := range strings.Split(rawBotKeys, ",") {
			botSet[key] = true
		}
	}
	requestPlayers := make([]startPlayer, 0, len(players))
	for _, player := range players {
		requestPlayers = append(requestPlayers, startPlayer{
			UUID: player.UUID, Key: player.Key, Name: player.Name, Bot: botSet[player.Key],
		})
	}
	roster, err := buildRoster(requestPlayers)
	if err != nil {
		return arenaRoster{}, err
	}
	if roster.playerCount != playerCount || roster.botCount != botCount {
		return arenaRoster{}, errors.New("legacy arena roster is inconsistent")
	}
	return roster, nil
}

func buildRoster(players []startPlayer) (arenaRoster, error) {
	if len(players) < 1 || len(players) > maxPlayers {
		return arenaRoster{}, fmt.Errorf("arena roster must contain between 1 and %d players", maxPlayers)
	}
	names := make(map[string]bool, len(players))
	uuids := make(map[string]bool, len(players))
	keys := make(map[string]bool, len(players))
	serverPlayers := make([]serverPlayer, 0, len(players))
	botKeys := make([]string, 0, len(players)-1)
	credentials := make(map[string]arenaCredential, len(players))
	tokensByName := make(map[string]string, len(players))
	tokenHashes := make(map[string]bool, len(players))
	for _, player := range players {
		if player.Name == "" || len(player.Name) > 64 || hasControl(player.Name) || names[player.Name] ||
			player.UUID == "" || len(player.UUID) > 128 || hasControl(player.UUID) || uuids[player.UUID] ||
			len(player.Key) < 32 || len(player.Key) > 256 || hasControl(player.Key) || keys[player.Key] ||
			(player.Bot && player.TokenHash != "") ||
			(player.TokenHash != "" && (!canonicalHash(player.TokenHash) || tokenHashes[player.TokenHash])) {
			return arenaRoster{}, errors.New("arena roster contains invalid or duplicate credentials")
		}
		names[player.Name], uuids[player.UUID], keys[player.Key] = true, true, true
		if player.TokenHash != "" {
			tokenHashes[player.TokenHash] = true
			credentials[player.TokenHash] = arenaCredential{Name: player.Name, Key: player.Key}
			tokensByName[player.Name] = player.TokenHash
		}
		serverPlayers = append(serverPlayers, serverPlayer{UUID: player.UUID, Key: player.Key, Name: player.Name})
		if player.Bot {
			botKeys = append(botKeys, player.Key)
		}
	}
	if len(botKeys) >= len(players) {
		return arenaRoster{}, errors.New("the arena must retain at least one human slot")
	}
	raw, err := json.Marshal(serverPlayers)
	if err != nil {
		return arenaRoster{}, err
	}
	joinedBotKeys := strings.Join(botKeys, ",")
	fingerprintInput, err := json.Marshal(players)
	if err != nil {
		return arenaRoster{}, err
	}
	fingerprint := sha256.Sum256(append(fingerprintInput, append([]byte{'\n'}, []byte(joinedBotKeys)...)...))
	return arenaRoster{
		players: string(raw), botKeys: joinedBotKeys, botCount: len(botKeys),
		playerCount: len(players), fingerprint: fingerprint,
		participants: append([]startPlayer(nil), players...), credentialByToken: credentials,
		tokenByName: tokensByName,
	}, nil
}

func hasControl(value string) bool {
	for _, character := range value {
		if character < 0x20 || character == 0x7f {
			return true
		}
	}
	return false
}

func readStartRoster(w http.ResponseWriter, r *http.Request) (*arenaRoster, error) {
	if r.Body == nil || r.Body == http.NoBody || r.ContentLength == 0 {
		return nil, nil
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxStartBody)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var request startRequest
	if err := decoder.Decode(&request); err != nil {
		return nil, errors.New("invalid arena roster")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("invalid arena roster")
	}
	roster, err := buildRoster(request.Players)
	if err != nil {
		return nil, err
	}
	return &roster, nil
}

func boundedInteger(name string, minimum, maximum, fallback int) (int, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value < minimum || value > maximum {
		return 0, fmt.Errorf("%s must be between %d and %d", name, minimum, maximum)
	}
	return value, nil
}

func (a *arena) health(w http.ResponseWriter, _ *http.Request) {
	a.mu.Lock()
	players, npcs := 0, 0
	if a.cfg.roster != nil {
		players, npcs = a.cfg.roster.playerCount, a.cfg.roster.botCount
	}
	value := status{
		Name: "Minions in 32K", Status: "idle", Protocol: "minions/0",
		ProtocolVersion: 13,
		RenderWidth:     256, RenderHeight: 128,
		Players: players, NPCs: npcs, CertHash: a.cfg.certHash, Build: supervisorBuild,
	}
	if a.server != nil {
		value.Status = "running"
		value.MatchID = a.matchID
		value.ExpectedEndUnixMS = a.expectedEnd.UnixMilli()
	}
	a.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(value)
}

func (a *arena) quality(w http.ResponseWriter, _ *http.Request) {
	a.qualityMu.Lock()
	defer a.qualityMu.Unlock()
	mode := a.qualityModeNow()
	cacheDuration := qualityCache
	if !a.qualityPassed {
		cacheDuration = qualityRetry
	}
	if mode != a.qualityMode || time.Since(a.qualityAt) >= cacheDuration {
		probeError := a.runQualityProbe(mode)
		// A real match can begin or finish while an idle/running probe is in
		// flight. Bind the response to the current mode instead of reporting a
		// false outage for a healthy state transition.
		if currentMode := a.qualityModeNow(); currentMode != mode {
			mode = currentMode
			probeError = a.runQualityProbe(mode)
		}
		a.qualityPassed = probeError == nil
		a.qualityAt = time.Now()
		a.qualityMode = mode
	}
	if !a.qualityPassed {
		problem(w, http.StatusServiceUnavailable, "authoritative gameplay probe failed")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(qualityStatus{
		Status: "ok", Probe: "pinned-quic-invalid-key", Mode: mode,
		ProtocolVersion: 13,
	})
}

func (a *arena) qualityModeNow() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.server != nil {
		return "running"
	}
	return "idle"
}

func (a *arena) runQualityProbe(mode string) error {
	if mode == "idle" {
		return a.runIdleQualityProbe()
	}
	a.mu.Lock()
	serverRunning := a.server != nil
	botCount := 0
	if a.cfg.roster != nil {
		botCount = a.cfg.roster.botCount
	}
	botsRunning := botCount == 0 || a.bot != nil
	warming := !a.startedAt.IsZero() && time.Since(a.startedAt) < 5*time.Second
	lastError := a.lastError
	a.mu.Unlock()
	if !serverRunning {
		return errors.New("authoritative server stopped before its probe")
	}
	if !warming && (!botsRunning || lastError != "") {
		return errors.New("arena NPC process is unavailable")
	}
	return a.runPinnedJoinProbe(fmt.Sprintf("127.0.0.1:%d", 4433))
}

func (a *arena) runPinnedJoinProbe(remote string) error {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	command := exec.CommandContext(
		ctx,
		botPath,
		"--readiness-probe",
		"--remote", remote,
		"--keys", "invalid-quality-"+hex.EncodeToString(random),
		"--cert-hash", a.cfg.certHash,
	)
	if err := command.Run(); err != nil {
		return errors.New("pinned gameplay join probe failed")
	}
	return nil
}

func (a *arena) runIdleQualityProbe() error {
	directory, err := os.MkdirTemp("/tmp", "minions-quality-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(directory)
	ready := filepath.Join(directory, "ready")
	replays := filepath.Join(directory, "replays")
	if err := os.Mkdir(replays, 0o700); err != nil {
		return err
	}
	validKeyBytes := make([]byte, 16)
	if _, err := rand.Read(validKeyBytes); err != nil {
		return err
	}
	validKey := "quality-valid-" + hex.EncodeToString(validKeyBytes)
	players, err := json.Marshal([]map[string]string{{
		"uuid": "quality-probe", "key": validKey, "name": "QualityProbe",
	}})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	command := exec.CommandContext(
		ctx,
		serverPath,
		"--bind", fmt.Sprintf("127.0.0.1:%d", qualityPort),
		"--wt-bind", fmt.Sprintf("127.0.0.1:%d", qualityWTPort),
		"--replay-dir", replays,
		"--grace-secs", "30",
		"--match-secs", "30",
		"--seed", "1364283729",
	)
	command.Env = qualityProbeEnvironment(
		os.Environ(), string(players), ready, a.cfg.certDER, a.cfg.keyDER,
	)
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		return err
	}
	waited := false
	defer func() {
		terminate(command)
		if !waited {
			_ = command.Wait()
		}
	}()
	if err := waitForReadyPath(ready, 5*time.Second); err != nil {
		return err
	}
	if err := a.runPinnedJoinProbe(fmt.Sprintf("127.0.0.1:%d", qualityPort)); err != nil {
		return err
	}
	terminate(command)
	_ = command.Wait()
	waited = true
	return nil
}

func qualityProbeEnvironment(base []string, players, ready, certDER, keyDER string) []string {
	blocked := map[string]bool{
		"PLAYER_TOKENS": true, "PLAYER_TOKENS_FILE": true, "MATCH_ID": true,
		"REPORT_URL": true, "REPLAY_UPLOAD_URL": true, "REPORT_TOKEN": true,
		"MINIONS_SCORING_LEG_SECS": true, "MINIONS_READY_FILE": true,
		"WT_CERT_DER_B64": true, "WT_KEY_DER_B64": true,
		"RSCTF_FLAG": true,
	}
	filtered := make([]string, 0, len(base)+5)
	for _, entry := range base {
		name, _, found := strings.Cut(entry, "=")
		if found && blocked[name] {
			continue
		}
		filtered = append(filtered, entry)
	}
	return append(
		filtered,
		"PLAYER_TOKENS="+players,
		"MATCH_ID=minions-quality-probe",
		"MINIONS_READY_FILE="+ready,
		"WT_CERT_DER_B64="+certDER,
		"WT_KEY_DER_B64="+keyDER,
	)
}

func (a *arena) start(w http.ResponseWriter, r *http.Request) {
	token, ok := bearerToken(r.Header.Get("Authorization"))
	if !ok || !sameSecret(token, a.cfg.reportToken) {
		problem(w, http.StatusUnauthorized, "invalid arena controller")
		return
	}
	roster, err := readStartRoster(w, r)
	if err != nil {
		problem(w, http.StatusBadRequest, "invalid arena roster")
		return
	}
	value, started, err := a.ensureStarted(roster)
	if err != nil {
		log.Printf("arena start failed: %v", err)
		if errors.Is(err, errRosterConflict) {
			problem(w, http.StatusConflict, "arena roster changed during an active match")
			return
		}
		problem(w, http.StatusServiceUnavailable, "arena could not start")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if started {
		w.WriteHeader(http.StatusCreated)
	}
	_ = json.NewEncoder(w).Encode(value)
}

func (a *arena) ensureStarted(roster *arenaRoster) (status, bool, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if roster != nil {
		if a.server != nil && a.cfg.roster != nil && roster.fingerprint != a.cfg.roster.fingerprint {
			return status{}, false, errRosterConflict
		}
		a.cfg.roster = roster
	}
	if a.cfg.roster == nil {
		return status{}, false, errors.New("arena roster is not configured")
	}
	if a.server != nil {
		return a.currentStatusLocked(), false, nil
	}
	matchID, err := newMatchID()
	if err != nil {
		return status{}, false, err
	}
	seed := strconv.FormatUint(uint64(time.Now().UnixNano()), 10)
	_ = os.Remove(readyPath)
	command := exec.Command(
		serverPath,
		"--bind", "0.0.0.0:4433",
		"--wt-bind", "127.0.0.1:4434",
		"--replay-dir", replayPath,
		"--grace-secs", strconv.Itoa(a.cfg.graceSecs),
		"--match-secs", strconv.Itoa(a.cfg.matchSecs),
		"--seed", seed,
	)
	command.Env = competitionEnvironment(os.Environ(), a.cfg, *a.cfg.roster, matchID)
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		a.lastError = err.Error()
		return status{}, false, err
	}
	a.server = command
	a.matchID = matchID
	a.startedAt = time.Now()
	a.expectedEnd = time.Now().Add(time.Duration(a.cfg.graceSecs+a.cfg.matchSecs) * time.Second)
	a.lastError = ""
	go a.waitForServer(matchID, command)
	if err := waitForReady(5 * time.Second); err != nil {
		terminate(command)
		a.lastError = err.Error()
		return status{}, false, err
	}
	if a.cfg.roster.botCount > 0 {
		go a.startBots(matchID)
	}
	log.Printf("arena started match=%s players=%d npcs=%d grace=%ds duration=%ds", matchID, a.cfg.roster.playerCount, a.cfg.roster.botCount, a.cfg.graceSecs, a.cfg.matchSecs)
	return a.currentStatusLocked(), true, nil
}

func (a *arena) currentStatusLocked() status {
	players, npcs := 0, 0
	if a.cfg.roster != nil {
		players, npcs = a.cfg.roster.playerCount, a.cfg.roster.botCount
	}
	return status{
		Name: "Minions in 32K", Status: "running", Protocol: "minions/0",
		ProtocolVersion: 13,
		RenderWidth:     256, RenderHeight: 128, MatchID: a.matchID,
		ExpectedEndUnixMS: a.expectedEnd.UnixMilli(),
		Players:           players, NPCs: npcs,
		CertHash: a.cfg.certHash, Build: supervisorBuild,
	}
}

func (a *arena) startBots(matchID string) {
	a.mu.Lock()
	if a.server == nil || a.matchID != matchID {
		a.mu.Unlock()
		return
	}
	if a.cfg.roster == nil {
		a.lastError = "arena roster disappeared"
		a.mu.Unlock()
		return
	}
	command := exec.Command(botPath, botArguments(a.cfg, *a.cfg.roster)...)
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		a.lastError = err.Error()
		a.mu.Unlock()
		log.Printf("bot start failed match=%s: %v", matchID, err)
		return
	}
	a.bot = command
	a.mu.Unlock()
	log.Printf("bots joined match=%s count=%d", matchID, a.cfg.roster.botCount)
	go a.waitForBots(matchID, command)
}

func botArguments(cfg config, roster arenaRoster) []string {
	return []string{
		"--remote", "127.0.0.1:4433",
		"--count", strconv.Itoa(roster.botCount),
		"--npc-count", strconv.Itoa(roster.botCount),
		"--keys", roster.botKeys,
		"--cert-hash", cfg.certHash,
	}
}

func competitionEnvironment(base []string, cfg config, roster arenaRoster, matchID string) []string {
	blocked := map[string]bool{
		"PLAYER_TOKENS": true, "PLAYER_TOKENS_FILE": true, "MATCH_ID": true,
		"REPORT_TOKEN": true, "WT_CERT_DER_B64": true, "WT_KEY_DER_B64": true,
		"MINIONS_CERT_HASH": true, "MINIONS_BOT_KEYS": true, "MINIONS_BOT_COUNT": true,
		"MINIONS_READY_FILE": true,
		"RSCTF_FLAG":         true,
		"RSCTF_KOTH_GAME_ID": true, "RSCTF_KOTH_CHALLENGE_ID": true,
		"RSCTF_KOTH_PLATFORM_URL": true, "RSCTF_KOTH_CONTEXT_URL": true,
		"RSCTF_KOTH_OBSERVATION_URL": true, "RSCTF_KOTH_REPORTER_SECRET": true,
	}
	filtered := make([]string, 0, len(base)+10)
	for _, entry := range base {
		name, _, found := strings.Cut(entry, "=")
		if found && blocked[name] {
			continue
		}
		filtered = append(filtered, entry)
	}
	return append(filtered,
		"PLAYER_TOKENS="+roster.players,
		"MATCH_ID="+matchID,
		"REPORT_TOKEN="+cfg.reportToken,
		"REPORT_URL=http://127.0.0.1:8080/internal/match-report",
		"REPLAY_UPLOAD_URL=http://127.0.0.1:8080/internal/replay",
		"WT_CERT_DER_B64="+cfg.certDER,
		"WT_KEY_DER_B64="+cfg.keyDER,
		"MINIONS_CERT_HASH="+cfg.certHash,
		"MINIONS_BOT_KEYS="+roster.botKeys,
		"MINIONS_BOT_COUNT="+strconv.Itoa(roster.botCount),
		"MINIONS_READY_FILE="+readyPath,
	)
}

func (a *arena) waitForServer(matchID string, command *exec.Cmd) {
	err := command.Wait()
	a.mu.Lock()
	if a.server == command && a.matchID == matchID {
		a.server = nil
		a.matchID = ""
		a.startedAt = time.Time{}
		a.expectedEnd = time.Time{}
		if err != nil {
			a.lastError = err.Error()
		}
		if a.bot != nil {
			terminate(a.bot)
			a.bot = nil
		}
	}
	a.mu.Unlock()
	if err != nil {
		log.Printf("arena stopped match=%s error=%v", matchID, err)
	} else {
		log.Printf("arena finalized match=%s", matchID)
	}
	removeReplays()
}

func (a *arena) waitForBots(matchID string, command *exec.Cmd) {
	err := command.Wait()
	a.mu.Lock()
	if a.bot == command {
		a.bot = nil
		if err != nil && a.matchID == matchID {
			a.lastError = err.Error()
		}
	}
	a.mu.Unlock()
	if err != nil {
		log.Printf("bots stopped match=%s error=%v", matchID, err)
	}
}

func (a *arena) stop() {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.bot != nil {
		terminate(a.bot)
		a.bot = nil
	}
	if a.server != nil {
		terminate(a.server)
		a.server = nil
		a.startedAt = time.Time{}
	}
}

func terminate(command *exec.Cmd) {
	if command.Process == nil {
		return
	}
	_ = syscall.Kill(-command.Process.Pid, syscall.SIGTERM)
}

func removeReplays() {
	_ = os.Remove(readyPath)
	entries, err := os.ReadDir(replayPath)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			_ = os.Remove(replayPath + "/" + entry.Name())
		}
	}
}

func waitForReady(timeout time.Duration) error {
	return waitForReadyPath(readyPath, timeout)
}

func waitForReadyPath(path string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		body, err := os.ReadFile(path)
		if err == nil && string(body) == "ready\n" {
			return nil
		}
		time.Sleep(25 * time.Millisecond)
	}
	return errors.New("authoritative server did not become ready")
}

func newMatchID() (string, error) {
	random := make([]byte, 8)
	if _, err := rand.Read(random); err != nil {
		return "", err
	}
	return fmt.Sprintf("m32-%d-%s", time.Now().Unix(), hex.EncodeToString(random)), nil
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

func supervisorHealthcheck(endpoint string) error {
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Get(endpoint)
	if err != nil {
		return fmt.Errorf("supervisor health request: %w", err)
	}
	defer response.Body.Close()
	read, readErr := io.Copy(io.Discard, io.LimitReader(response.Body, 2049))
	if readErr != nil {
		return fmt.Errorf("read supervisor health response: %w", readErr)
	}
	if read > 2048 {
		return errors.New("supervisor health response is oversized")
	}
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("supervisor health returned HTTP %d", response.StatusCode)
	}
	return nil
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(w, r)
	})
}

func problem(w http.ResponseWriter, statusCode int, title string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(map[string]any{"title": title, "status": statusCode})
}
