package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

const (
	testReportToken = "report-token-that-is-longer-than-thirty-two-bytes"
	testCertHash    = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)

func testTeams() []teamSecret {
	return []teamSecret{
		{Name: "alpha", ArenaUUID: "team-alpha", ArenaKey: strings.Repeat("a", 48), ScoringEligible: true},
		{Name: "beta", ArenaUUID: "team-beta", ArenaKey: strings.Repeat("b", 48), ScoringEligible: true},
		{Name: "gamma", ArenaUUID: "team-gamma", ArenaKey: strings.Repeat("c", 48), ScoringEligible: true},
		{Name: "delta", ArenaUUID: "team-delta", ArenaKey: strings.Repeat("d", 48), ScoringEligible: true},
	}
}

func testBridge(t *testing.T, rsctf *httptest.Server) *bridge {
	t.Helper()
	rsctfURL, err := url.Parse(rsctf.URL)
	if err != nil {
		t.Fatal(err)
	}
	arenaServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/healthz" {
			_ = json.NewEncoder(w).Encode(arenaRuntimeStatus{
				Name: "Minions in 32K", Status: "running", Protocol: "minions/0",
				ProtocolVersion: 13,
				RenderWidth:     256, RenderHeight: 128, MatchID: "test-match-running",
				ExpectedEndUnixMS: time.Now().Add(time.Minute).UnixMilli(), Players: 4,
				CertHash: testCertHash, Build: "test",
			})
			return
		}
		if r.Method != http.MethodPost || r.URL.Path != "/internal/start" || r.Header.Get("Authorization") != "Bearer "+testReportToken {
			http.Error(w, "bad arena request", http.StatusUnauthorized)
			return
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("start content type = %q", r.Header.Get("Content-Type"))
			http.Error(w, "bad arena request", http.StatusBadRequest)
			return
		}
		var start arenaStartRequest
		if err := json.NewDecoder(r.Body).Decode(&start); err != nil || len(start.Players) != len(testTeams()) {
			t.Errorf("invalid start roster: %#v error=%v", start, err)
			http.Error(w, "bad arena request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"running"}`))
	}))
	t.Cleanup(arenaServer.Close)
	healthURL, err := url.Parse(arenaServer.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	return &bridge{
		cfg: configuration{
			GameID: 7, ChallengeID: 9, TargetID: strings.Repeat("a", 64),
			RSCTF: rsctfURL, HealthURL: healthURL, PublicRemote: "tcp.1pc.tf:4532",
			Secrets: secretFile{
				ObserverSecret: "koth_api_observer-secret-long-enough",
				ReportToken:    testReportToken, Teams: testTeams(),
			},
		},
		client:   &http.Client{Timeout: 2 * time.Second},
		sessions: make(map[string]liveSession), waves: make(map[string]finalizedWave),
		matches: make(map[string]*matchSequence), replays: make(map[string]replayArtifact),
	}
}

func TestBridgeHealthcheckRequiresBoundedHTTP200(t *testing.T) {
	tests := []struct {
		name    string
		status  int
		body    string
		wantErr bool
	}{
		{name: "healthy", status: http.StatusOK, body: `{"status":"ok"}`},
		{name: "unhealthy", status: http.StatusServiceUnavailable, body: `{"status":"unhealthy"}`, wantErr: true},
		{name: "oversized", status: http.StatusOK, body: strings.Repeat("x", 1025), wantErr: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()
			if err := bridgeHealthcheck(server.URL); (err != nil) != test.wantErr {
				t.Fatalf("bridgeHealthcheck() error = %v, wantErr %v", err, test.wantErr)
			}
		})
	}
}

func TestNewArenaMustFitBeforePristineResetButRunningArenaCanReconnect(t *testing.T) {
	now := time.Unix(1_000_000, 0).UTC()
	required := arenaStaging + time.Duration(scoringLegCount)*scoringLegLength + arenaStartMargin
	if arenaWindowAllowsStart(now, now.Add(required-time.Millisecond)) {
		t.Fatal("arena started without enough time to finalize")
	}
	if !arenaWindowAllowsStart(now, now.Add(required)) {
		t.Fatal("arena with the exact safe budget was rejected")
	}
	if !arenaWindowAllowsStart(now, now.Add(required+time.Minute)) {
		t.Fatal("arena with excess cycle time was rejected")
	}
	// ensureArena bypasses the new-start fence when /healthz reports running;
	// testBridge uses that response so its session exchange remains a reconnect
	// regression even though no observer context endpoint is installed.
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	if err := testBridge(t, empty).ensureArena(context.Background()); err != nil {
		t.Fatalf("running arena reconnect was rejected: %v", err)
	}
}

func TestArenaHealthRosterContract(t *testing.T) {
	teams := testTeams()
	for index := 1; index < len(teams); index++ {
		teams[index].ScoringEligible = false
	}
	tests := []struct {
		name    string
		body    string
		wantErr bool
	}{
		{
			name: "matching human and NPC roster",
			body: `{"name":"Minions in 32K","status":"idle","protocol":"minions/0","protocolVersion":13,` +
				`"renderWidth":256,"renderHeight":128,"players":4,"npcs":3,"certHash":"` + testCertHash + `","build":"test"}`,
		},
		{
			name: "unconfigured idle roster",
			body: `{"name":"Minions in 32K","status":"idle","protocol":"minions/0","protocolVersion":13,` +
				`"renderWidth":256,"renderHeight":128,"players":0,"npcs":0,"certHash":"` + testCertHash + `","build":"test"}`,
		},
		{
			name: "missing live certificate pin",
			body: `{"name":"Minions in 32K","status":"idle","protocol":"minions/0","protocolVersion":13,` +
				`"renderWidth":256,"renderHeight":128,"players":0,"npcs":0,"build":"test"}`,
			wantErr: true,
		},
		{
			name: "stale protocol revision",
			body: `{"name":"Minions in 32K","status":"idle","protocol":"minions/0","protocolVersion":12,` +
				`"renderWidth":256,"renderHeight":128,"players":4,"npcs":3,"certHash":"` + testCertHash + `","build":"test"}`,
			wantErr: true,
		},
		{
			name: "mismatched NPC count",
			body: `{"name":"Minions in 32K","status":"idle","protocol":"minions/0","protocolVersion":13,` +
				`"renderWidth":256,"renderHeight":128,"players":4,"npcs":2,"certHash":"` + testCertHash + `","build":"test"}`,
			wantErr: true,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			arena := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(test.body))
			}))
			defer arena.Close()
			healthURL, err := url.Parse(arena.URL)
			if err != nil {
				t.Fatal(err)
			}
			b := &bridge{
				cfg:    configuration{HealthURL: healthURL, Secrets: secretFile{Teams: teams}},
				client: &http.Client{Timeout: 2 * time.Second},
			}
			_, err = b.fetchArenaStatus(context.Background())
			if (err != nil) != test.wantErr {
				t.Fatalf("fetchArenaStatus() error = %v, wantErr = %v", err, test.wantErr)
			}
		})
	}
}

func TestSessionDoesNotStartIdleArenaThatCannotFinishBeforeReset(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Millisecond)
	required := arenaStaging + time.Duration(scoringLegCount)*scoringLegLength + arenaStartMargin
	tokenHash := strings.Repeat("a", 64)
	rsctf := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/koth/capability/authenticate":
			_ = json.NewEncoder(w).Encode(capabilityIdentity{TeamID: tokenHash, TeamName: "alpha"})
		case "/api/v1/koth/games/7/challenges/9/context":
			_ = json.NewEncoder(w).Encode(observerContext{
				APIVersion: "v1", Context: strings.Repeat("e", 64), CycleNumber: 2,
				ResetAttempt: 1, RoundNumber: 8,
				CycleEndsAt:         now.Add(required - time.Millisecond).UnixMilli(),
				WaveWindowStartsAt:  now.Add(-time.Minute).UnixMilli(),
				WaveWindowEndsAt:    now.Add(time.Minute).UnixMilli(),
				EligibleTokenHashes: []string{tokenHash}, GeneratedAt: now.UnixMilli(),
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer rsctf.Close()
	started := false
	arena := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/healthz" {
			_ = json.NewEncoder(w).Encode(arenaRuntimeStatus{
				Name: "Minions in 32K", Status: "idle", Protocol: "minions/0",
				ProtocolVersion: 13,
				RenderWidth:     256, RenderHeight: 128, Players: 4,
				CertHash: testCertHash, Build: "test",
			})
			return
		}
		if r.Method == http.MethodPost && r.URL.Path == "/internal/start" {
			started = true
		}
		http.NotFound(w, r)
	}))
	defer arena.Close()
	rsctfURL, _ := url.Parse(rsctf.URL)
	healthURL, _ := url.Parse(arena.URL + "/healthz")
	b := &bridge{
		cfg: configuration{
			GameID: 7, ChallengeID: 9, TargetID: strings.Repeat("a", 64),
			RSCTF: rsctfURL, HealthURL: healthURL, PublicRemote: "tcp.1pc.tf:4532",
			Secrets: secretFile{ObserverSecret: "observer-secret", ReportToken: testReportToken,
				Teams: testTeams()},
		},
		client: &http.Client{Timeout: 2 * time.Second}, sessions: make(map[string]liveSession),
		waves: make(map[string]finalizedWave), matches: make(map[string]*matchSequence),
	}
	request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
	request.Header.Set("Authorization", "Bearer koth_valid-looking-token")
	recorder := httptest.NewRecorder()
	b.createSession(recorder, request)
	if recorder.Code != http.StatusConflict {
		t.Fatalf("status=%d retry-after=%q body=%s", recorder.Code, recorder.Header().Get("Retry-After"), recorder.Body.String())
	}
	var problem arenaRetryProblem
	if err := json.NewDecoder(recorder.Body).Decode(&problem); err != nil {
		t.Fatalf("decode retry problem: %v", err)
	}
	wantRetryAt := now.Add(required - time.Millisecond + arenaRetryReady).UnixMilli()
	retrySeconds, err := strconv.Atoi(recorder.Header().Get("Retry-After"))
	if err != nil || retrySeconds < int(required/time.Second) || retrySeconds > int(required/time.Second)+6 {
		t.Fatalf("retry-after=%q body=%#v", recorder.Header().Get("Retry-After"), problem)
	}
	if problem.Status != http.StatusConflict || problem.Code != "arena_window_pending" ||
		problem.RetryAtUnixMS != wantRetryAt || problem.Title == "" || problem.Detail == "" {
		t.Fatalf("unexpected retry problem: %#v", problem)
	}
	if started {
		t.Fatal("unsafe arena start invoked the runtime")
	}
	if b.sessions["alpha"].TokenHash != tokenHash {
		t.Fatal("authenticated ready identity was not retained for the next safe start")
	}
}

func TestArenaRetryProblemRoundsRetryAfterUp(t *testing.T) {
	now := time.Unix(1_000_000, 0).UTC()
	recorder := httptest.NewRecorder()
	writeArenaRetryProblem(recorder, now, now.Add(2*time.Second+time.Millisecond))

	if recorder.Code != http.StatusConflict || recorder.Header().Get("Retry-After") != "3" ||
		recorder.Header().Get("Cache-Control") != "no-store" ||
		recorder.Header().Get("Content-Type") != "application/problem+json" {
		t.Fatalf("headers=%#v status=%d", recorder.Header(), recorder.Code)
	}
	var problem arenaRetryProblem
	if err := json.NewDecoder(recorder.Body).Decode(&problem); err != nil {
		t.Fatal(err)
	}
	if problem.Code != "arena_window_pending" || problem.RetryAtUnixMS != now.Add(2*time.Second+time.Millisecond).UnixMilli() {
		t.Fatalf("unexpected problem: %#v", problem)
	}
}

func TestContextResetConflictBecomesShortAutomaticRetry(t *testing.T) {
	rsctf := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "resetting", http.StatusConflict)
	}))
	defer rsctf.Close()
	arena := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/healthz" {
			_ = json.NewEncoder(w).Encode(arenaRuntimeStatus{
				Name: "Minions in 32K", Status: "idle", Protocol: "minions/0",
				ProtocolVersion: 13,
				RenderWidth:     256, RenderHeight: 128, Players: 4,
				CertHash: testCertHash, Build: "test",
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer arena.Close()
	rsctfURL, _ := url.Parse(rsctf.URL)
	healthURL, _ := url.Parse(arena.URL + "/healthz")
	b := &bridge{
		cfg: configuration{
			GameID: 7, ChallengeID: 9, RSCTF: rsctfURL, HealthURL: healthURL,
			Secrets: secretFile{Teams: testTeams()},
		},
		client: &http.Client{Timeout: 2 * time.Second},
	}

	before := time.Now().UTC()
	err := b.ensureArena(context.Background())
	var retry *arenaRetryError
	if !errors.As(err, &retry) {
		t.Fatalf("expected arena retry, got %v", err)
	}
	if retry.retryAt.Before(before.Add(arenaRetryReady-time.Second)) ||
		retry.retryAt.After(before.Add(arenaRetryReady+time.Second)) {
		t.Fatalf("unexpected retry time: %s", retry.retryAt)
	}
}

func validReport(now time.Time) matchReport {
	ended := now.UTC().Truncate(time.Millisecond).UnixMilli()
	return matchReport{
		MatchID: "target-a-round-1", Kind: "leg", Sequence: 1, LegCount: scoringLegCount,
		StartedUnixMS: ended - int64(scoringLegLength/time.Millisecond),
		EndedUnixMS:   ended, DurationMS: int64(scoringLegLength / time.Millisecond),
		Players: []reportPlayer{
			{TeamUUID: "team-alpha", Name: "alpha", Rank: 1, Kills: 4, ConfirmedKills: 3, Deaths: 2, Connected: true, IntegrityStatus: integrityClean},
			{TeamUUID: "team-beta", Name: "beta", Rank: 2, Kills: 2, ConfirmedKills: 1, Deaths: 3, Connected: true, IntegrityStatus: integrityClean},
			{TeamUUID: "team-gamma", Name: "gamma", Rank: 2, Kills: 2, ConfirmedKills: 1, Deaths: 3, Connected: true, IntegrityStatus: integrityClean},
			{TeamUUID: "team-delta", Name: "delta", Rank: 3, Kills: 0, Deaths: 0, Connected: false, IntegrityStatus: integrityClean},
		},
	}
}

func postReport(b *bridge, report matchReport) *httptest.ResponseRecorder {
	body, _ := json.Marshal(report)
	request := httptest.NewRequest(http.MethodPost, "/internal/match-report", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+testReportToken)
	recorder := httptest.NewRecorder()
	b.acceptReport(recorder, request)
	return recorder
}

func reportAtSequence(first matchReport, sequence uint32) matchReport {
	report := first
	report.Sequence = sequence
	legMilliseconds := int64(scoringLegLength / time.Millisecond)
	report.StartedUnixMS = first.StartedUnixMS + int64(sequence-1)*legMilliseconds
	report.EndedUnixMS = report.StartedUnixMS + legMilliseconds
	return report
}

func TestSessionExchangesCapabilityForPinnedArenaCredential(t *testing.T) {
	const capability = "koth_never-forward-this-capability"
	const tokenHash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	var authenticated string
	rsctf := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		authenticated, _ = body["token"].(string)
		if body["gameId"] != float64(7) || body["challengeId"] != float64(9) {
			t.Errorf("unexpected authentication scope: %#v", body)
		}
		_ = json.NewEncoder(w).Encode(capabilityIdentity{TeamID: tokenHash, TeamName: "alpha"})
	}))
	defer rsctf.Close()
	b := testBridge(t, rsctf)

	request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
	request.Header.Set("Authorization", "Bearer "+capability)
	recorder := httptest.NewRecorder()
	b.createSession(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	if authenticated != capability {
		t.Fatalf("RSCTF received %q", authenticated)
	}
	var response arenaSession
	if err := json.NewDecoder(recorder.Body).Decode(&response); err != nil {
		t.Fatal(err)
	}
	if response.Key != strings.Repeat("a", 48) || response.CertHash != testCertHash || response.Protocol != "minions/0" || response.Remote != "tcp.1pc.tf:4532" {
		t.Fatalf("unexpected session: %#v", response)
	}
	if strings.Contains(recorder.Body.String(), capability) || recorder.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("capability leaked or credential response was cacheable")
	}
	b.mu.Lock()
	session, found := b.sessions["alpha"]
	b.mu.Unlock()
	if !found || session.TokenHash != tokenHash {
		t.Fatalf("session identity was not retained: %#v", session)
	}
}

func TestSessionRejectsUnknownSlotAndMalformedAuthorization(t *testing.T) {
	const tokenHash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	rsctf := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(capabilityIdentity{TeamID: tokenHash, TeamName: "not-in-arena"})
	}))
	defer rsctf.Close()
	b := testBridge(t, rsctf)

	for _, test := range []struct {
		authorization string
		want          int
	}{
		{"", http.StatusUnauthorized},
		{"Bearer has spaces", http.StatusUnauthorized},
		{"Bearer koth_valid-looking-token", http.StatusForbidden},
	} {
		request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
		request.Header.Set("Authorization", test.authorization)
		recorder := httptest.NewRecorder()
		b.createSession(recorder, request)
		if recorder.Code != test.want {
			t.Fatalf("authorization %q: status=%d want=%d", test.authorization, recorder.Code, test.want)
		}
	}
}

func TestReportRequiresBearerAndRetainsRecentTeamIdentityForZeroRows(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC()
	b.sessions["alpha"] = liveSession{TokenHash: strings.Repeat("a", 64), LastSeen: now}
	b.sessions["beta"] = liveSession{TokenHash: strings.Repeat("b", 64), LastSeen: now.Add(-activeGrace - time.Second)}
	b.sessions["delta"] = liveSession{TokenHash: strings.Repeat("d", 64), LastSeen: now}
	report := validReport(now)
	body, _ := json.Marshal(report)

	unauthorized := httptest.NewRecorder()
	b.acceptReport(unauthorized, httptest.NewRequest(http.MethodPost, "/internal/match-report", bytes.NewReader(body)))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status = %d", unauthorized.Code)
	}

	request := httptest.NewRequest(http.MethodPost, "/internal/match-report", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+testReportToken)
	recorder := httptest.NewRecorder()
	b.acceptReport(recorder, request)
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	b.mu.Lock()
	wave := b.waves[report.MatchID+":1"]
	b.mu.Unlock()
	if len(wave.Identities) != 2 || wave.Identities["alpha"] != strings.Repeat("a", 64) || wave.Identities["delta"] != strings.Repeat("d", 64) {
		t.Fatalf("identities = %#v", wave.Identities)
	}

	request = httptest.NewRequest(http.MethodPost, "/internal/match-report", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+testReportToken)
	recorder = httptest.NewRecorder()
	b.acceptReport(recorder, request)
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("idempotent replay status = %d", recorder.Code)
	}

	report.Players[0].Takeovers = 1
	changed, _ := json.Marshal(report)
	request = httptest.NewRequest(http.MethodPost, "/internal/match-report", bytes.NewReader(changed))
	request.Header.Set("Authorization", "Bearer "+testReportToken)
	recorder = httptest.NewRecorder()
	b.acceptReport(recorder, request)
	if recorder.Code != http.StatusConflict {
		t.Fatalf("conflicting replay status = %d", recorder.Code)
	}
}

func TestReportRejectsForgedRankingAndRoster(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC()

	report := validReport(now)
	report.Players[1].Rank = 1
	if b.validateReport(report, now) == nil {
		t.Fatal("forged rank was accepted")
	}
	report = validReport(now)
	report.Players[1].Name = "alpha"
	if b.validateReport(report, now) == nil {
		t.Fatal("mismatched registered name was accepted")
	}
	report = validReport(now)
	report.DurationMS++
	if b.validateReport(report, now) == nil {
		t.Fatal("inconsistent duration was accepted")
	}
}

func TestReportSequenceIsOrderedIdempotentAndFinallyReconciled(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	first := validReport(time.Now().UTC().Add(-4 * time.Minute))
	if recorder := postReport(b, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("first leg: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if recorder := postReport(b, reportAtSequence(first, 3)); recorder.Code != http.StatusConflict {
		t.Fatalf("out-of-order leg: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	for sequence := uint32(2); sequence <= scoringLegCount; sequence++ {
		if recorder := postReport(b, reportAtSequence(first, sequence)); recorder.Code != http.StatusNoContent {
			t.Fatalf("leg %d: status=%d body=%s", sequence, recorder.Code, recorder.Body.String())
		}
	}
	final := first
	final.Kind = "final"
	final.Sequence = scoringLegCount + 1
	final.StartedUnixMS = first.StartedUnixMS
	final.EndedUnixMS = first.StartedUnixMS + int64(scoringLegCount)*int64(scoringLegLength/time.Millisecond)
	final.DurationMS = final.EndedUnixMS - final.StartedUnixMS
	for index := range final.Players {
		final.Players[index].Kills *= scoringLegCount
		final.Players[index].ConfirmedKills *= scoringLegCount
		final.Players[index].Deaths *= scoringLegCount
		final.Players[index].Takeovers *= scoringLegCount
	}
	if recorder := postReport(b, final); recorder.Code != http.StatusConflict ||
		!strings.Contains(recorder.Body.String(), "retained replay") {
		t.Fatalf("final without replay: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	b.replays[final.MatchID] = replayArtifact{
		SHA256: strings.Repeat("f", 64), Size: 1, ReceivedAt: time.Now().UTC(),
	}
	if recorder := postReport(b, final); recorder.Code != http.StatusNoContent {
		t.Fatalf("final report: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if recorder := postReport(b, final); recorder.Code != http.StatusNoContent {
		t.Fatalf("idempotent final: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if len(b.waves) != int(scoringLegCount) {
		t.Fatalf("final reconciliation became a scoring wave: waves=%d", len(b.waves))
	}
	final.Players[0].Takeovers++
	if recorder := postReport(b, final); recorder.Code != http.StatusConflict {
		t.Fatalf("conflicting final: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestOrderedPrefixReplayRebuildsStateAfterBridgeRestart(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	first := validReport(time.Now().UTC().Add(-time.Minute))

	beforeRestart := testBridge(t, empty)
	if recorder := postReport(beforeRestart, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("pre-restart first leg: status=%d body=%s", recorder.Code, recorder.Body.String())
	}

	afterRestart := testBridge(t, empty)
	if recorder := postReport(afterRestart, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("replayed first leg: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if recorder := postReport(afterRestart, reportAtSequence(first, 2)); recorder.Code != http.StatusNoContent {
		t.Fatalf("post-restart second leg: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if len(afterRestart.matches[first.MatchID].LegKeys) != 2 || len(afterRestart.waves) != 2 {
		t.Fatalf("replayed prefix did not reconstruct state: matches=%#v waves=%d", afterRestart.matches, len(afterRestart.waves))
	}
}

func TestFinalReportRejectsMissingLegAndCounterMismatch(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	first := validReport(time.Now().UTC().Add(-4 * time.Minute))
	if recorder := postReport(b, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("first leg: status=%d", recorder.Code)
	}
	final := first
	final.Kind = "final"
	final.Sequence = scoringLegCount + 1
	final.EndedUnixMS = first.StartedUnixMS + int64(scoringLegCount)*int64(scoringLegLength/time.Millisecond)
	final.DurationMS = final.EndedUnixMS - final.StartedUnixMS
	if recorder := postReport(b, final); recorder.Code != http.StatusConflict {
		t.Fatalf("incomplete final: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	for sequence := uint32(2); sequence <= scoringLegCount; sequence++ {
		if recorder := postReport(b, reportAtSequence(first, sequence)); recorder.Code != http.StatusNoContent {
			t.Fatalf("leg %d: status=%d", sequence, recorder.Code)
		}
	}
	b.replays[final.MatchID] = replayArtifact{
		SHA256: strings.Repeat("f", 64), Size: 1, ReceivedAt: time.Now().UTC(),
	}
	if recorder := postReport(b, final); recorder.Code != http.StatusConflict {
		t.Fatalf("mismatched final: status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestObservationNormalizesAgainstAllPlayersAndAwardsOneCrown(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC().Truncate(time.Millisecond)
	report := matchReport{Players: []reportPlayer{
		{TeamUUID: "team-alpha", Name: "alpha", Rank: 1, Kills: 150, ConfirmedKills: 150, Connected: true},
		{TeamUUID: "team-beta", Name: "beta", Rank: 2, Kills: 20, ConfirmedKills: 20, Connected: true},
		{TeamUUID: "team-gamma", Name: "gamma", Rank: 3, Kills: 5, ConfirmedKills: 5, Connected: true},
		{TeamUUID: "team-delta", Name: "delta", Rank: 4, Kills: 1, ConfirmedKills: 1, Connected: true},
	}}
	b.waves["match"] = finalizedWave{
		ID: "m32-wave", EndedAt: now, Result: report,
		Identities: map[string]string{
			"alpha": strings.Repeat("a", 64), "beta": strings.Repeat("b", 64),
			"gamma": strings.Repeat("c", 64), "delta": strings.Repeat("d", 64),
		},
	}
	current := observerContext{
		Context: strings.Repeat("e", 64), RoundNumber: 2,
		WaveWindowStartsAt: now.Add(-time.Second).UnixMilli(), WaveWindowEndsAt: now.Add(time.Second).UnixMilli(),
		EligibleTokenHashes: []string{strings.Repeat("a", 64), strings.Repeat("b", 64), strings.Repeat("c", 64), strings.Repeat("d", 64)},
	}
	body, waveCount, teamCount, err := b.buildObservation(current)
	if err != nil {
		t.Fatal(err)
	}
	if waveCount != 1 || teamCount != 4 {
		t.Fatalf("waves=%d teams=%d", waveCount, teamCount)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	crowns := 0
	wantEarned := map[string]int64{
		strings.Repeat("a", 64): 150,
		strings.Repeat("b", 64): 20,
		strings.Repeat("c", 64): 5,
		strings.Repeat("d", 64): 1,
	}
	for _, team := range value.Waves[0].Teams {
		if team.Objectives[0].Possible != 150 || team.Objectives[0].Earned != wantEarned[team.TokenHash] {
			t.Fatalf("possible = %d", team.Objectives[0].Possible)
		}
		if team.IsCrown {
			crowns++
			if team.TokenHash != strings.Repeat("a", 64) {
				t.Fatalf("wrong crown: %s", team.TokenHash)
			}
		}
	}
	if crowns != 1 {
		t.Fatalf("crowns = %d", crowns)
	}
}

func TestTiedFirstPlaceHasNoCrown(t *testing.T) {
	report := matchReport{Players: []reportPlayer{
		{Name: "alpha", Rank: 1, Kills: 10, ConfirmedKills: 8, Deaths: 2, Connected: true},
		{Name: "beta", Rank: 1, Kills: 10, ConfirmedKills: 8, Deaths: 2, Connected: true},
		{Name: "gamma", Rank: 2, Kills: 3, ConfirmedKills: 2, Deaths: 1, Connected: true},
	}}
	possible, scores, crown := scoreMatch(report, map[string]bool{
		"alpha": true, "beta": true, "gamma": true,
	})
	if possible <= 0 || scores["alpha"] != scores["beta"] || crown != "" {
		t.Fatalf("possible=%d scores=%#v crown=%q", possible, scores, crown)
	}
}

func TestRawFragsBreakAConfirmedScoreTieForCrownOnly(t *testing.T) {
	report := matchReport{Players: []reportPlayer{
		{Name: "alpha", Kills: 8, ConfirmedKills: 5, Deaths: 2, Connected: true},
		{Name: "beta", Kills: 7, ConfirmedKills: 5, Deaths: 1, Connected: true},
	}}
	possible, scores, crown := scoreMatch(report, map[string]bool{"alpha": true, "beta": true})
	if possible != 5 || scores["alpha"] != 5 || scores["beta"] != 5 || crown != "alpha" {
		t.Fatalf("possible=%d scores=%#v crown=%q", possible, scores, crown)
	}
}

func TestZeroConfirmationsNeverEarnTiebreakPoints(t *testing.T) {
	report := matchReport{Players: []reportPlayer{
		{Name: "alpha", Rank: 1, Kills: 1, ConfirmedKills: 1, Deaths: 4, Connected: true},
		{Name: "beta", Rank: 2, Kills: 0, Deaths: 0, Connected: true},
		{Name: "gamma", Rank: 3, Kills: 0, Deaths: 9, Connected: true},
	}}
	possible, scores, crown := scoreMatch(report, map[string]bool{
		"alpha": true, "beta": true, "gamma": true,
	})
	if scores["beta"] != 0 || scores["gamma"] != 0 || scores["alpha"] != 1 || possible != 1 || crown != "alpha" {
		t.Fatalf("possible=%d scores=%#v crown=%q", possible, scores, crown)
	}
}

func TestMissedLegIsExplicitZeroEvidence(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC().Truncate(time.Millisecond)
	report := matchReport{Players: []reportPlayer{
		{Name: "alpha", Rank: 1, Kills: 4, ConfirmedKills: 3, Connected: true},
		{Name: "beta", Rank: 2, Kills: 3, ConfirmedKills: 2, Connected: false},
	}}
	b.waves["match"] = finalizedWave{
		ID: "m32-wave", EndedAt: now, Result: report,
		Identities: map[string]string{"alpha": strings.Repeat("a", 64), "beta": strings.Repeat("b", 64)},
	}
	current := observerContext{
		Context: strings.Repeat("e", 64), RoundNumber: 2,
		WaveWindowStartsAt: now.Add(-time.Second).UnixMilli(), WaveWindowEndsAt: now.Add(time.Second).UnixMilli(),
		EligibleTokenHashes: []string{strings.Repeat("a", 64), strings.Repeat("b", 64)},
	}
	body, _, _, err := b.buildObservation(current)
	if err != nil {
		t.Fatal(err)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	for _, team := range value.Waves[0].Teams {
		if team.TokenHash == strings.Repeat("b", 64) && (team.Activity.Earned != 0 || team.Objectives[0].Earned != 0) {
			t.Fatalf("missed participant received evidence: %#v", team)
		}
	}
}

func TestExhibitionBotCannotSetTheNormalizationCeilingOrCrown(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	b.cfg.Secrets.Teams[1].ScoringEligible = false
	now := time.Now().UTC().Truncate(time.Millisecond)
	b.waves["match"] = finalizedWave{
		ID: "m32-wave", EndedAt: now,
		Result: matchReport{Players: []reportPlayer{
			{Name: "alpha", Kills: 4, ConfirmedKills: 3, Deaths: 2, Connected: true},
			{Name: "beta", Kills: 20, ConfirmedKills: 20, Deaths: 0, Connected: true},
		}},
		Identities: map[string]string{"alpha": strings.Repeat("a", 64)},
	}
	current := observerContext{
		Context: strings.Repeat("e", 64), RoundNumber: 2,
		WaveWindowStartsAt:  now.Add(-time.Second).UnixMilli(),
		WaveWindowEndsAt:    now.Add(time.Second).UnixMilli(),
		EligibleTokenHashes: []string{strings.Repeat("a", 64)},
	}
	body, waveCount, teamCount, err := b.buildObservation(current)
	if err != nil {
		t.Fatal(err)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if waveCount != 1 || teamCount != 1 || len(value.Waves[0].Teams) != 1 {
		t.Fatalf("waves=%d teams=%d evidence=%#v", waveCount, teamCount, value.Waves)
	}
	evidence := value.Waves[0].Teams[0]
	if evidence.Objectives[0] != (evidenceRatio{Earned: 3, Possible: 3}) || !evidence.IsCrown {
		t.Fatalf("exhibition bot influenced evidence: %#v", evidence)
	}
}

func TestEightSlotReportAcceptsSevenExhibitionNPCsWithoutScoringThem(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	b.cfg.Secrets.Teams = []teamSecret{{
		Name: "alpha", ArenaUUID: "team-alpha", ArenaKey: strings.Repeat("a", 48), ScoringEligible: true,
	}}
	players := []reportPlayer{{
		TeamUUID: "npc-1", Name: "NPC_1", Rank: 1, Kills: 7, ConfirmedKills: 7, Connected: true,
		IntegrityStatus: integrityClean,
	}, {
		TeamUUID: "team-alpha", Name: "alpha", Rank: 2, Kills: 4, ConfirmedKills: 3, Deaths: 2, Connected: true,
		IntegrityStatus: integrityClean,
	}}
	for index := 1; index <= 7; index++ {
		b.cfg.Secrets.Teams = append(b.cfg.Secrets.Teams, teamSecret{
			Name: fmt.Sprintf("NPC_%d", index), ArenaUUID: fmt.Sprintf("npc-%d", index),
			ArenaKey: strings.Repeat(string(rune('a'+index)), 48), ScoringEligible: false,
		})
		if index > 1 {
			players = append(players, reportPlayer{
				TeamUUID: fmt.Sprintf("npc-%d", index), Name: fmt.Sprintf("NPC_%d", index),
				Rank: 3, Deaths: 2, Connected: true, IntegrityStatus: integrityClean,
			})
		}
	}
	now := time.Now().UTC().Truncate(time.Millisecond)
	report := matchReport{
		MatchID: "eight-slot-npc-match", Kind: "leg", Sequence: 1, LegCount: scoringLegCount,
		StartedUnixMS: now.Add(-scoringLegLength).UnixMilli(), EndedUnixMS: now.UnixMilli(),
		DurationMS: int64(scoringLegLength / time.Millisecond), Players: players,
	}
	if err := b.validateReport(report, now); err != nil {
		t.Fatalf("eight-slot report rejected: %v", err)
	}
	possible, scores, crown := scoreMatch(report, map[string]bool{"alpha": true})
	if possible != 3 || scores["alpha"] != 3 || scores["NPC_1"] != 0 || crown != "alpha" {
		t.Fatalf("NPCs influenced scoring: possible=%d scores=%#v crown=%q", possible, scores, crown)
	}
}

func TestFixedNativeScoreCannotBeChangedByAZeroKillDeathGriefer(t *testing.T) {
	base := matchReport{Players: []reportPlayer{
		{Name: "alpha", Kills: 5, ConfirmedKills: 4, Deaths: 1, Connected: true},
		{Name: "beta", Kills: 4, ConfirmedKills: 3, Deaths: 0, Connected: true},
	}}
	names := map[string]bool{"alpha": true, "beta": true, "griefer": true}
	possibleBefore, scoresBefore, crownBefore := scoreMatch(base, names)
	base.Players = append(base.Players, reportPlayer{
		Name: "griefer", Kills: 0, Deaths: maxLegDeaths, Connected: true,
	})
	possibleAfter, scoresAfter, crownAfter := scoreMatch(base, names)
	if possibleBefore != possibleAfter || scoresBefore["alpha"] != scoresAfter["alpha"] ||
		scoresBefore["beta"] != scoresAfter["beta"] || crownBefore != crownAfter ||
		scoresAfter["griefer"] != 0 {
		t.Fatalf("field composition changed fixed scoring: before=%d/%#v/%q after=%d/%#v/%q",
			possibleBefore, scoresBefore, crownBefore, possibleAfter, scoresAfter, crownAfter)
	}
}

func TestReportRejectsImpossiblePhysicalCounters(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC()

	report := validReport(now)
	report.Players[0].Deaths = maxLegDeaths + 1
	if b.validateReport(report, now) == nil {
		t.Fatal("impossible death count was accepted")
	}
	report = validReport(now)
	report.Players[0].Kills++
	if b.validateReport(report, now) == nil {
		t.Fatal("kill total greater than death total was accepted")
	}
	report = validReport(now)
	report.Kind = "final"
	report.Sequence = scoringLegCount + 1
	report.StartedUnixMS = report.EndedUnixMS - int64(scoringLegCount)*int64(scoringLegLength/time.Millisecond)
	report.DurationMS = report.EndedUnixMS - report.StartedUnixMS
	report.Players[0].ConfirmedKills = report.Players[0].Kills + 1
	if b.validateReport(report, now) == nil {
		t.Fatal("cumulative confirmed kills greater than frags were accepted")
	}
}

func TestLegAllowsConfirmationOfFragFromPreviousLeg(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC()
	report := validReport(now)
	// A banana dropped just before a leg boundary remains live for 12 seconds.
	// Its confirmation belongs to this leg even though its frag belonged to
	// the preceding leg. The cumulative final report still enforces C <= K.
	report.Players[0].Kills = 2
	report.Players[0].ConfirmedKills = 3
	if err := b.validateReport(report, now); err != nil {
		t.Fatalf("cross-boundary confirmation was rejected: %v", err)
	}
}

func TestIdentityAndReportStateSurviveBridgeRestart(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	directory := t.TempDir()
	now := time.Now().UTC().Truncate(time.Millisecond)
	before := testBridge(t, empty)
	before.cfg.StateDir = directory
	if err := before.loadPersistentState(now); err != nil {
		t.Fatal(err)
	}
	if err := before.rememberSession("alpha", liveSession{
		TokenHash: strings.Repeat("a", 64), LastSeen: now,
	}); err != nil {
		t.Fatal(err)
	}
	first := validReport(now)
	if recorder := postReport(before, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("report status=%d body=%s", recorder.Code, recorder.Body.String())
	}

	after := testBridge(t, empty)
	after.cfg.StateDir = directory
	if err := after.loadPersistentState(now.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	if after.sessions["alpha"].TokenHash != strings.Repeat("a", 64) ||
		after.waves[first.MatchID+":1"].Identities["alpha"] != strings.Repeat("a", 64) ||
		len(after.matches[first.MatchID].LegKeys) != 1 {
		t.Fatalf("restart lost scoring state: sessions=%#v waves=%#v matches=%#v",
			after.sessions, after.waves, after.matches)
	}
	info, err := os.Stat(after.statePath())
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("state file mode=%v", info.Mode().Perm())
	}
}

func TestReplayUploadIsAuthenticatedBoundedAndDurable(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	b.cfg.StateDir = t.TempDir()
	if err := b.loadPersistentState(time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(
		http.MethodPost, "/internal/replay?match_id=match-replay-1", strings.NewReader("official replay bytes"),
	)
	request.Header.Set("Authorization", "Bearer "+testReportToken)
	request.Header.Set("Content-Type", "application/octet-stream")
	recorder := httptest.NewRecorder()
	b.acceptReplay(recorder, request)
	if recorder.Code != http.StatusNoContent || !canonicalHash(recorder.Header().Get("X-Content-SHA256")) {
		t.Fatalf("status=%d headers=%v body=%s", recorder.Code, recorder.Header(), recorder.Body.String())
	}
	artifact := b.replays["match-replay-1"]
	stored, err := os.ReadFile(b.replayPath("match-replay-1"))
	if err != nil || string(stored) != "official replay bytes" || artifact.Size != int64(len(stored)) {
		t.Fatalf("stored=%q artifact=%#v error=%v", stored, artifact, err)
	}
	info, err := os.Stat(b.replayPath("match-replay-1"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("replay mode=%v", info.Mode().Perm())
	}

	unauthorized := httptest.NewRecorder()
	b.acceptReplay(unauthorized, httptest.NewRequest(
		http.MethodPost, "/internal/replay?match_id=other", strings.NewReader("x"),
	))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status=%d", unauthorized.Code)
	}
}

func TestUDPProxyRelaysBothDirections(t *testing.T) {
	echo, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer echo.Close()
	go func() {
		buffer := make([]byte, 2048)
		for {
			n, peer, err := echo.ReadFromUDP(buffer)
			if err != nil {
				return
			}
			_, _ = echo.WriteToUDP(buffer[:n], peer)
		}
	}()

	proxy, err := newUDPProxy("127.0.0.1:0", echo.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.serve(ctx) }()
	client, err := net.DialUDP("udp", nil, proxy.public.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	_ = client.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := client.Write([]byte("encrypted-quic-datagram")); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 64)
	n, err := client.Read(buffer)
	if err != nil {
		t.Fatal(err)
	}
	if string(buffer[:n]) != "encrypted-quic-datagram" {
		t.Fatalf("reply = %q", buffer[:n])
	}
}

func TestUDPAdmissionNeverEvictsAnActiveSessionAtCapacity(t *testing.T) {
	echo, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer echo.Close()
	proxy, err := newUDPProxy("127.0.0.1:0", echo.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.close()
	proxy.maximumSessions = 2
	proxy.maximumPerIP = 2
	first, err := proxy.session(&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 41001})
	if err != nil {
		t.Fatal(err)
	}
	second, err := proxy.session(&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 41002})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := proxy.session(&net.UDPAddr{IP: net.ParseIP("127.0.0.2"), Port: 41003}); !errors.Is(err, errProxyAdmission) {
		t.Fatalf("capacity admission error=%v", err)
	}
	if proxy.sessions["127.0.0.1:41001"] != first || proxy.sessions["127.0.0.1:41002"] != second {
		t.Fatal("active UDP sessions were evicted at capacity")
	}
}

func TestCapabilityAndBearerParsersAreStrict(t *testing.T) {
	if token, ok := bearerCapability("Bearer koth_abcdefgh-123456"); !ok || token != "koth_abcdefgh-123456" {
		t.Fatal("canonical capability rejected")
	}
	for _, invalid := range []string{"koth_abcdefgh-123456", "Bearer koth_has/slash", "Bearer  koth_double-space", "Bearer koth_has space"} {
		if _, ok := bearerCapability(invalid); ok {
			t.Fatalf("accepted %q", invalid)
		}
	}
}
