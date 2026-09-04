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
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	managedMaxResponse = 256 * 1024
	arenaRetryReady    = 5 * time.Second
	arenaStaging       = 30 * time.Second
	arenaStartMargin   = 10 * time.Second
)

var errManagedRoundGap = errors.New("rsctf scoring context is not active")

type managedReporterConfig struct {
	GameID         int
	ChallengeID    int
	PlatformURL    *url.URL
	ContextURL     *url.URL
	ObservationURL *url.URL
	Secret         string
}

type managedReporter struct {
	cfg         managedReporterConfig
	reportToken string
	arena       *arena
	client      *http.Client

	mu            sync.Mutex
	waves         map[string]finalizedWave
	matches       map[string]*matchSequence
	replays       map[string]replayArtifact
	lastContext   string
	lastDigest    [32]byte
	hasDigest     bool
	lastTimestamp int64
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

type reportingContext struct {
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

type arenaRetryError struct {
	retryAt time.Time
}

func (e *arenaRetryError) Error() string {
	return "the current Crown cycle cannot fit a complete match"
}

type arenaRetryProblem struct {
	Title         string `json:"title"`
	Detail        string `json:"detail"`
	Status        int    `json:"status"`
	Code          string `json:"code"`
	RetryAtUnixMS int64  `json:"retryAtUnixMs"`
}

type upstreamHTTPError struct {
	service string
	status  int
}

func (e *upstreamHTTPError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", e.service, e.status)
}

func loadManagedReporterConfig() (*managedReporterConfig, error) {
	secret := strings.TrimSpace(os.Getenv("RSCTF_KOTH_REPORTER_SECRET"))
	managedNames := []string{
		"RSCTF_KOTH_GAME_ID", "RSCTF_KOTH_CHALLENGE_ID", "RSCTF_KOTH_PLATFORM_URL",
		"RSCTF_KOTH_CONTEXT_URL", "RSCTF_KOTH_OBSERVATION_URL",
	}
	if secret == "" {
		for _, name := range managedNames {
			if strings.TrimSpace(os.Getenv(name)) != "" {
				return nil, errors.New("managed KotH reporter environment is incomplete")
			}
		}
		return nil, nil
	}
	if !strings.HasPrefix(secret, "koth_target_") || len(secret) < 48 || len(secret) > 128 || strings.ContainsAny(secret, " \t\r\n") {
		return nil, errors.New("RSCTF_KOTH_REPORTER_SECRET is invalid")
	}
	gameID, err := positiveEnvironmentInteger("RSCTF_KOTH_GAME_ID")
	if err != nil {
		return nil, err
	}
	challengeID, err := positiveEnvironmentInteger("RSCTF_KOTH_CHALLENGE_ID")
	if err != nil {
		return nil, err
	}
	platform, err := parseManagedURL("RSCTF_KOTH_PLATFORM_URL", true)
	if err != nil {
		return nil, err
	}
	contextURL, err := parseManagedURL("RSCTF_KOTH_CONTEXT_URL", false)
	if err != nil {
		return nil, err
	}
	observationURL, err := parseManagedURL("RSCTF_KOTH_OBSERVATION_URL", false)
	if err != nil {
		return nil, err
	}
	return &managedReporterConfig{
		GameID: gameID, ChallengeID: challengeID, PlatformURL: platform,
		ContextURL: contextURL, ObservationURL: observationURL, Secret: secret,
	}, nil
}

func parseManagedURL(name string, originOnly bool) (*url.URL, error) {
	value, err := url.Parse(strings.TrimSpace(os.Getenv(name)))
	if err != nil || (value.Scheme != "http" && value.Scheme != "https") || value.Host == "" ||
		value.User != nil || value.RawQuery != "" || value.Fragment != "" ||
		(originOnly && value.Path != "" && value.Path != "/") || (!originOnly && value.Path == "") {
		return nil, fmt.Errorf("%s is not a valid managed HTTP endpoint", name)
	}
	return value, nil
}

func positiveEnvironmentInteger(name string) (int, error) {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return value, nil
}

func validRemote(value string) bool {
	host, portText, err := net.SplitHostPort(strings.TrimSpace(value))
	if err != nil || !validRemoteHost(host) {
		return false
	}
	port, err := strconv.Atoi(portText)
	return err == nil && port > 0 && port <= 65535
}

func validRemoteHost(host string) bool {
	if net.ParseIP(host) != nil {
		return true
	}
	if host == "" || len(host) > 253 {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if label == "" || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, character := range label {
			if !(character >= 'a' && character <= 'z') &&
				!(character >= 'A' && character <= 'Z') &&
				!(character >= '0' && character <= '9') && character != '-' {
				return false
			}
		}
	}
	return true
}

func newManagedReporter(cfg managedReporterConfig, reportToken string, game *arena) *managedReporter {
	return &managedReporter{
		cfg: cfg, reportToken: reportToken, arena: game,
		client: &http.Client{
			Transport: &http.Transport{
				Proxy: http.ProxyFromEnvironment, MaxIdleConns: 32, MaxIdleConnsPerHost: 16,
				IdleConnTimeout: 45 * time.Second, ResponseHeaderTimeout: 8 * time.Second,
				DisableCompression: true,
			},
			Timeout: 12 * time.Second,
		},
		waves: make(map[string]finalizedWave), matches: make(map[string]*matchSequence),
		replays: make(map[string]replayArtifact),
	}
}

func (a *arena) createSession(w http.ResponseWriter, request *http.Request) {
	if body, err := readLimited(w, request, 1); err != nil || len(body) != 0 {
		problem(w, http.StatusBadRequest, "session request must have an empty body")
		return
	}
	if a.reporter == nil {
		problem(w, http.StatusServiceUnavailable, "managed arena identity is not active")
		return
	}
	capability, ok := bearerCapability(request.Header.Get("Authorization"))
	if !ok {
		problem(w, http.StatusUnauthorized, "invalid event capability")
		return
	}
	identity, statusCode, err := a.reporter.authenticate(request.Context(), capability)
	if err != nil {
		if statusCode == 0 || statusCode >= 500 {
			problem(w, http.StatusServiceUnavailable, "identity service unavailable")
		} else {
			problem(w, http.StatusUnauthorized, "invalid event capability")
		}
		return
	}
	credential, found := a.credentialForToken(identity.TeamID)
	if !found {
		current, err := a.reporter.fetchContext(request.Context())
		if err != nil {
			if errors.Is(err, errManagedRoundGap) {
				writeArenaRetryProblem(w, time.Now().UTC(), time.Now().UTC().Add(arenaRetryReady))
				return
			}
			problem(w, http.StatusServiceUnavailable, "scoring context unavailable")
			return
		}
		now := time.Now().UTC()
		cycleEnd := time.UnixMilli(current.CycleEndsAt).UTC()
		if !arenaWindowAllowsStart(now, cycleEnd) {
			writeArenaRetryProblem(w, now, cycleEnd.Add(arenaRetryReady))
			return
		}
		roster, err := buildManagedRoster(current, a.reporter.cfg.Secret)
		if err != nil {
			log.Printf("managed arena roster rejected: %v", err)
			problem(w, http.StatusServiceUnavailable, "arena roster unavailable")
			return
		}
		credential, found = roster.credentialByToken[identity.TeamID]
		if !found {
			problem(w, http.StatusForbidden, "this team has no arena slot")
			return
		}
		if _, _, err := a.ensureStarted(&roster); err != nil {
			log.Printf("managed arena start failed: %v", err)
			problem(w, http.StatusServiceUnavailable, "arena could not start")
			return
		}
	} else if _, _, err := a.ensureStarted(nil); err != nil {
		problem(w, http.StatusServiceUnavailable, "arena could not resume")
		return
	}

	a.mu.Lock()
	certHash := a.cfg.certHash
	a.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(arenaSession{
		Protocol: "minions/0", Remote: a.cfg.publicRemote, Name: credential.Name,
		Key: credential.Key, CertHash: certHash,
	})
}

func (a *arena) credentialForToken(tokenHash string) (arenaCredential, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.cfg.roster == nil {
		return arenaCredential{}, false
	}
	credential, found := a.cfg.roster.credentialByToken[tokenHash]
	return credential, found
}

func buildManagedRoster(current reportingContext, secret string) (arenaRoster, error) {
	if !validReportingContext(current) || len(current.EligibleTokenHashes) > maxPlayers {
		return arenaRoster{}, errors.New("managed roster exceeds the eight-player arena")
	}
	tokens := append([]string(nil), current.EligibleTokenHashes...)
	sort.Strings(tokens)
	players := make([]startPlayer, 0, maxPlayers)
	for _, token := range tokens {
		players = append(players, startPlayer{
			UUID: "team-" + token[:32], Name: "Team_" + token[:12],
			Key: managedCredential(secret, "player-key", token), TokenHash: token,
		})
	}
	for index := len(players) + 1; index <= maxPlayers; index++ {
		identity := strconv.Itoa(index)
		players = append(players, startPlayer{
			UUID: "npc-" + identity, Name: "NPC_" + identity,
			Key: managedCredential(secret, "npc-key", identity), Bot: true,
		})
	}
	return buildRoster(players)
}

func managedCredential(secret, domain, identity string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte("minions-in-32k/" + domain + "/v1\x00" + identity))
	return hex.EncodeToString(mac.Sum(nil))
}

func arenaWindowAllowsStart(now, cycleEnd time.Time) bool {
	required := arenaStaging + time.Duration(scoringLegCount)*scoringLegLength + arenaStartMargin
	return !now.Add(required).After(cycleEnd)
}

func (r *managedReporter) authenticate(ctx context.Context, capability string) (capabilityIdentity, int, error) {
	body, _ := json.Marshal(map[string]any{
		"token": capability, "gameId": r.cfg.GameID, "challengeId": r.cfg.ChallengeID,
	})
	endpoint := r.cfg.PlatformURL.ResolveReference(&url.URL{Path: "/api/v1/koth/capability/authenticate"})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return capabilityIdentity{}, 0, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "minions-managed-target/1")
	response, err := r.client.Do(request)
	if err != nil {
		return capabilityIdentity{}, 0, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, managedMaxResponse+1))
		return capabilityIdentity{}, response.StatusCode, errors.New("identity rejected")
	}
	var identity capabilityIdentity
	if err := decodeBounded(response.Body, &identity); err != nil || !canonicalHash(identity.TeamID) ||
		identity.TeamName == "" || len(identity.TeamName) > 64 || hasControl(identity.TeamName) {
		return capabilityIdentity{}, response.StatusCode, errors.New("identity response is invalid")
	}
	return identity, response.StatusCode, nil
}

func (r *managedReporter) fetchContext(ctx context.Context) (reportingContext, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, r.cfg.ContextURL.String(), nil)
	if err != nil {
		return reportingContext{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "minions-managed-target/1")
	response, err := r.client.Do(request)
	if err != nil {
		return reportingContext{}, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusConflict {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, managedMaxResponse+1))
		return reportingContext{}, errManagedRoundGap
	}
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, managedMaxResponse+1))
		return reportingContext{}, &upstreamHTTPError{service: "rsctf context", status: response.StatusCode}
	}
	var current reportingContext
	if err := decodeBounded(response.Body, &current); err != nil || !validReportingContext(current) {
		return reportingContext{}, errors.New("rsctf context is invalid")
	}
	return current, nil
}

func validReportingContext(value reportingContext) bool {
	if value.APIVersion != "v1" || !canonicalHash(value.Context) || value.CycleNumber <= 0 || value.ResetAttempt <= 0 ||
		value.RoundNumber <= 0 || value.CycleEndsAt <= 0 || value.WaveWindowStartsAt <= 0 ||
		value.WaveWindowStartsAt >= value.WaveWindowEndsAt || value.CycleEndsAt < value.WaveWindowEndsAt ||
		value.GeneratedAt <= 0 || len(value.EligibleTokenHashes) < 1 || len(value.EligibleTokenHashes) > 2_000 {
		return false
	}
	seen := make(map[string]bool, len(value.EligibleTokenHashes))
	for _, token := range value.EligibleTokenHashes {
		if !canonicalHash(token) || seen[token] {
			return false
		}
		seen[token] = true
	}
	if value.ObjectiveSchemaHash != nil && !canonicalHash(*value.ObjectiveSchemaHash) {
		return false
	}
	return len(value.ObjectiveIDs) == 0 || (len(value.ObjectiveIDs) == 1 && value.ObjectiveIDs[0] == objectiveIDs[0])
}

func (r *managedReporter) run(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		if err := r.report(ctx); err != nil && !errors.Is(err, errManagedRoundGap) {
			log.Printf("managed KotH reporter waiting: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (r *managedReporter) report(ctx context.Context) error {
	current, err := r.fetchContext(ctx)
	if err != nil {
		return err
	}
	body, waveCount, teamCount, err := r.buildObservation(current)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(body)
	r.mu.Lock()
	unchanged := r.hasDigest && r.lastContext == current.Context && r.lastDigest == digest
	r.mu.Unlock()
	if unchanged {
		return nil
	}
	accepted, err := r.submit(ctx, body)
	if err != nil {
		return err
	}
	if !accepted.Accepted || accepted.CycleNumber != current.CycleNumber ||
		accepted.ResetAttempt != current.ResetAttempt || accepted.RoundNumber != current.RoundNumber ||
		accepted.SubmittedWaves != waveCount || accepted.SubmittedTeams != teamCount ||
		accepted.RecognizedTeams != teamCount {
		return errors.New("rsctf did not acknowledge the complete Minions snapshot")
	}
	r.mu.Lock()
	r.lastContext, r.lastDigest, r.hasDigest = current.Context, digest, true
	r.mu.Unlock()
	log.Printf("managed KotH snapshot accepted round=%d cycle=%d waves=%d teams=%d", accepted.RoundNumber, accepted.CycleNumber, waveCount, teamCount)
	return nil
}

func (r *managedReporter) submit(ctx context.Context, body []byte) (acceptedObservation, error) {
	r.mu.Lock()
	timestamp := time.Now().UnixMilli()
	if timestamp <= r.lastTimestamp {
		timestamp = r.lastTimestamp + 1
	}
	r.lastTimestamp = timestamp
	r.mu.Unlock()
	message := []byte(fmt.Sprintf("%d.%d.%d.", timestamp, r.cfg.GameID, r.cfg.ChallengeID))
	mac := hmac.New(sha256.New, []byte(r.cfg.Secret))
	_, _ = mac.Write(message)
	_, _ = mac.Write(body)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, r.cfg.ObservationURL.String(), bytes.NewReader(body))
	if err != nil {
		return acceptedObservation{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-RSCTF-Timestamp", strconv.FormatInt(timestamp, 10))
	request.Header.Set("X-RSCTF-Signature", "sha256="+hex.EncodeToString(mac.Sum(nil)))
	request.Header.Set("User-Agent", "minions-managed-target/1")
	response, err := r.client.Do(request)
	if err != nil {
		return acceptedObservation{}, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusConflict {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, managedMaxResponse+1))
		return acceptedObservation{}, errManagedRoundGap
	}
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, managedMaxResponse+1))
		return acceptedObservation{}, fmt.Errorf("rsctf observation returned HTTP %d", response.StatusCode)
	}
	var accepted acceptedObservation
	if err := decodeBounded(response.Body, &accepted); err != nil {
		return acceptedObservation{}, errors.New("rsctf acknowledgement is invalid")
	}
	return accepted, nil
}

func decodeBounded(reader io.Reader, target any) error {
	limited := &io.LimitedReader{R: reader, N: managedMaxResponse + 1}
	decoder := json.NewDecoder(limited)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("response contains trailing JSON")
	}
	if limited.N == 0 {
		return errors.New("response exceeds the size limit")
	}
	return nil
}

func readLimited(w http.ResponseWriter, request *http.Request, limit int64) ([]byte, error) {
	request.Body = http.MaxBytesReader(w, request.Body, limit)
	defer request.Body.Close()
	return io.ReadAll(request.Body)
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

func writeArenaRetryProblem(w http.ResponseWriter, now, retryAt time.Time) {
	if !retryAt.After(now) {
		retryAt = now.Add(arenaRetryReady)
	}
	remaining := retryAt.Sub(now)
	retrySeconds := int64((remaining + time.Second - 1) / time.Second)
	w.Header().Set("Retry-After", strconv.FormatInt(retrySeconds, 10))
	w.Header().Set("Content-Type", "application/problem+json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusConflict)
	_ = json.NewEncoder(w).Encode(arenaRetryProblem{
		Title:  "Crown reset protection is active",
		Detail: "The current cycle is too close to its reset to safely start a complete five-leg match.",
		Status: http.StatusConflict, Code: "arena_window_pending", RetryAtUnixMS: retryAt.UnixMilli(),
	})
}
