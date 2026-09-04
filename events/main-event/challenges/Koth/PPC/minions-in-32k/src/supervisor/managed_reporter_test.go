package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os/exec"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testManagedSecret = "koth_target_0123456789abcdef0123456789abcdef0123456789abcdef"

func managedContext(now time.Time, tokens ...string) reportingContext {
	return reportingContext{
		APIVersion: "v1", Context: strings.Repeat("e", 64), CycleNumber: 3,
		ResetAttempt: 2, RoundNumber: 9, CycleEndsAt: now.Add(20 * time.Minute).UnixMilli(),
		WaveWindowStartsAt:  now.Add(-2 * time.Minute).UnixMilli(),
		WaveWindowEndsAt:    now.Add(2 * time.Minute).UnixMilli(),
		EligibleTokenHashes: tokens, ObjectiveIDs: objectiveIDs, GeneratedAt: now.UnixMilli(),
	}
}

func TestManagedRosterIsDeterministicAndBindsOnlyEligibleHashes(t *testing.T) {
	now := time.Now().UTC()
	firstToken := strings.Repeat("a", 64)
	secondToken := strings.Repeat("b", 64)
	first, err := buildManagedRoster(managedContext(now, secondToken, firstToken), testManagedSecret)
	if err != nil {
		t.Fatal(err)
	}
	second, err := buildManagedRoster(managedContext(now, firstToken, secondToken), testManagedSecret)
	if err != nil {
		t.Fatal(err)
	}
	if first.fingerprint != second.fingerprint || first.playerCount != 8 || first.botCount != 6 {
		t.Fatalf("first=%+v second=%+v", first, second)
	}
	for _, token := range []string{firstToken, secondToken} {
		credential, found := first.credentialByToken[token]
		if !found || credential.Name != "Team_"+token[:12] || len(credential.Key) != 64 {
			t.Fatalf("missing deterministic credential for %s: %+v", token, credential)
		}
	}
	if strings.Contains(first.players, firstToken) || strings.Contains(first.players, secondToken) ||
		strings.Contains(first.players, testManagedSecret) {
		t.Fatal("platform identity or reporter secret leaked into the child roster")
	}
}

func TestManagedReporterConsumesRawRSCTFResponsesAndSignsSnapshot(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Millisecond)
	token := strings.Repeat("a", 64)
	var signed bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch request.Method + " " + request.URL.Path {
		case "GET /context":
			_ = json.NewEncoder(w).Encode(managedContext(now, token))
		case "POST /observations":
			signed = strings.HasPrefix(request.Header.Get("X-RSCTF-Signature"), "sha256=") &&
				request.Header.Get("X-RSCTF-Timestamp") != ""
			_ = json.NewEncoder(w).Encode(acceptedObservation{
				Accepted: true, CycleNumber: 3, ResetAttempt: 2, RoundNumber: 9,
				AcceptedAt: now.UnixMilli(),
			})
		default:
			http.NotFound(w, request)
		}
	}))
	defer server.Close()
	platform, _ := url.Parse(server.URL)
	contextURL, _ := url.Parse(server.URL + "/context")
	observationURL, _ := url.Parse(server.URL + "/observations")
	game := &arena{}
	reporter := newManagedReporter(managedReporterConfig{
		GameID: 7, ChallengeID: 11, PlatformURL: platform, ContextURL: contextURL,
		ObservationURL: observationURL, Secret: testManagedSecret,
	}, strings.Repeat("r", 64), game)
	game.reporter = reporter
	if err := reporter.report(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !signed {
		t.Fatal("snapshot did not carry the managed HMAC headers")
	}
	if cached, ok := reporter.recentSessionContext(time.Now().UTC()); !ok ||
		len(cached.EligibleTokenHashes) != 1 || cached.EligibleTokenHashes[0] != token {
		t.Fatalf("validated reporting context was not cached for sessions: ok=%v context=%+v", ok, cached)
	}
}

func TestSessionContextReusesRecentValidatedReporterResult(t *testing.T) {
	now := time.Now().UTC()
	token := strings.Repeat("a", 64)
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(w, "busy", http.StatusTooManyRequests)
	}))
	defer server.Close()
	contextURL, _ := url.Parse(server.URL + "/context")
	reporter := newManagedReporter(managedReporterConfig{
		ContextURL: contextURL, Secret: testManagedSecret,
	}, strings.Repeat("r", 64), &arena{})
	reporter.rememberSessionContext(managedContext(now, token), now)

	current, err := reporter.contextForSession(context.Background(), now.Add(sessionContextTTL))
	if err != nil || len(current.EligibleTokenHashes) != 1 || current.EligibleTokenHashes[0] != token {
		t.Fatalf("recent context was not reused: context=%+v error=%v", current, err)
	}
	if requests.Load() != 0 {
		t.Fatalf("recent context made %d unnecessary upstream requests", requests.Load())
	}
	if _, err := reporter.contextForSession(context.Background(), now.Add(sessionContextTTL+time.Millisecond)); err == nil {
		t.Fatal("stale context bypassed the upstream admission boundary")
	}
	if requests.Load() != 1 {
		t.Fatalf("stale context made %d upstream requests, want 1", requests.Load())
	}
}

func TestSessionKeepsCapabilityAtTheSupervisorBoundary(t *testing.T) {
	const capability = "koth_opaque-session-capability"
	token := strings.Repeat("a", 64)
	var received string
	platformServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		received = request.Header.Get("User-Agent")
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil || body["token"] != capability {
			t.Fatalf("unexpected capability request: %#v error=%v", body, err)
		}
		_ = json.NewEncoder(w).Encode(capabilityIdentity{TeamID: token, TeamName: "Real Team Name"})
	}))
	defer platformServer.Close()
	platform, _ := url.Parse(platformServer.URL)
	roster, err := buildManagedRoster(managedContext(time.Now().UTC(), token), testManagedSecret)
	if err != nil {
		t.Fatal(err)
	}
	game := &arena{cfg: config{
		certHash: strings.Repeat("c", 64), roster: &roster,
	}}
	reporter := newManagedReporter(managedReporterConfig{
		GameID: 7, ChallengeID: 11, PlatformURL: platform, Secret: testManagedSecret,
	}, strings.Repeat("r", 64), game)
	game.reporter = reporter
	game.server = &exec.Cmd{}
	game.matchID = "m32-existing"
	game.expectedEnd = time.Now().Add(time.Minute)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
	request.Host = "10.13.41.3:8080"
	request.Header.Set("Authorization", "Bearer "+capability)
	recorder := httptest.NewRecorder()
	game.createSession(recorder, request)
	if recorder.Code != http.StatusOK || received != "minions-managed-target/1" {
		t.Fatalf("status=%d user-agent=%q body=%s", recorder.Code, received, recorder.Body.String())
	}
	var session arenaSession
	if err := json.NewDecoder(recorder.Body).Decode(&session); err != nil {
		t.Fatal(err)
	}
	if session.Name != "Team_"+token[:12] || session.Remote != "10.13.41.3:8080" ||
		session.Key != managedCredential(testManagedSecret, "player-key", token) {
		t.Fatalf("unexpected session: %+v", session)
	}
}

func TestDirectArenaRemoteAcceptsOnlyPrivateIPv4Targets(t *testing.T) {
	for _, host := range []string{
		"127.0.0.1:8080",
		"10.13.41.3:8080",
		"172.16.4.8:8080",
		"192.168.2.9:8080",
	} {
		remote, ok := directArenaRemote(host)
		if !ok || remote != strings.Split(host, ":")[0]+":8080" {
			t.Fatalf("host=%q remote=%q ok=%v", host, remote, ok)
		}
	}
	for _, host := range []string{"example.com:8080", "8.8.8.8:8080", "[::1]:8080"} {
		if remote, ok := directArenaRemote(host); ok {
			t.Fatalf("host=%q unexpectedly resolved to %q", host, remote)
		}
	}
}

func TestManagedScoringRejectsForgedRankAndExcludesNPCs(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Millisecond)
	firstToken := strings.Repeat("a", 64)
	secondToken := strings.Repeat("b", 64)
	roster, err := buildManagedRoster(managedContext(now, firstToken, secondToken), testManagedSecret)
	if err != nil {
		t.Fatal(err)
	}
	game := &arena{cfg: config{roster: &roster}}
	reporter := newManagedReporter(managedReporterConfig{Secret: testManagedSecret}, strings.Repeat("r", 64), game)
	game.reporter = reporter
	report := validManagedReport(now, roster)
	if err := reporter.validateReport(report, now); err != nil {
		t.Fatalf("valid report rejected: %v", err)
	}
	forged := report
	forged.Players = append([]reportPlayer(nil), report.Players...)
	forged.Players[1].Rank = 1
	if reporter.validateReport(forged, now) == nil {
		t.Fatal("forged dense rank was accepted")
	}
	report.Players[2].Kills = 30
	report.Players[2].ConfirmedKills = 30
	report.Players[2].Deaths = 30
	reporter.waves["match:1"] = finalizedWave{
		ID: "m32-test-l01", EndedAt: now, Result: report, Identities: roster.tokenByName,
	}
	body, waves, teams, err := reporter.buildObservation(managedContext(now, firstToken, secondToken))
	if err != nil {
		t.Fatal(err)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if waves != 1 || teams != 2 || len(value.Waves[0].Teams) != 2 {
		t.Fatalf("waves=%d teams=%d observation=%+v", waves, teams, value)
	}
	for _, evidence := range value.Waves[0].Teams {
		if evidence.Objectives[0].Possible != 3 {
			t.Fatalf("NPC changed the scoring ceiling: %+v", evidence)
		}
	}
}

func TestManagedScoringLeavesExactObjectiveTiesUncrowned(t *testing.T) {
	report := matchReport{Players: []reportPlayer{
		{Name: "Alpha", Connected: true, IntegrityStatus: integrityClean, ConfirmedKills: 4, Kills: 9},
		{Name: "Bravo", Connected: true, IntegrityStatus: integrityClean, ConfirmedKills: 4, Kills: 5},
	}}
	possible, scores, crown := scoreMatch(report, map[string]bool{"Alpha": true, "Bravo": true})
	if possible != 4 || scores["Alpha"] != 4 || scores["Bravo"] != 4 || crown != "" {
		t.Fatalf("possible=%d scores=%v crown=%q", possible, scores, crown)
	}
}

func TestManagedScoringRetainsEmptyFinalizedWaveAfterCapabilityRotation(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Millisecond)
	oldToken := strings.Repeat("a", 64)
	newToken := strings.Repeat("b", 64)
	reporter := newManagedReporter(managedReporterConfig{Secret: testManagedSecret}, strings.Repeat("r", 64), &arena{})
	reporter.waves["match:1"] = finalizedWave{
		ID: "m32-rotated-l01", EndedAt: now,
		Result: matchReport{Players: []reportPlayer{{
			Name: "Team_a", Connected: true, IntegrityStatus: integrityClean, ConfirmedKills: 1,
		}}},
		Identities: map[string]string{"Team_a": oldToken},
	}
	body, waves, teams, err := reporter.buildObservation(managedContext(now, newToken))
	if err != nil {
		t.Fatal(err)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if waves != 1 || teams != 0 || len(value.Waves) != 1 || len(value.Waves[0].Teams) != 0 {
		t.Fatalf("waves=%d teams=%d observation=%+v", waves, teams, value)
	}
}

func validManagedReport(now time.Time, roster arenaRoster) matchReport {
	players := make([]reportPlayer, 0, len(roster.participants))
	for index, participant := range roster.participants {
		player := reportPlayer{
			TeamUUID: participant.UUID, Name: participant.Name, Rank: 3,
			Connected: false, IntegrityStatus: integrityClean,
		}
		if index == 0 {
			player.Rank, player.Kills, player.ConfirmedKills, player.Deaths, player.Connected = 1, 3, 3, 3, true
		} else if index == 1 {
			player.Rank, player.Kills, player.ConfirmedKills, player.Deaths, player.Connected = 2, 1, 1, 1, true
		}
		players = append(players, player)
	}
	ended := now.UnixMilli()
	return matchReport{
		MatchID: "managed-match", Kind: "leg", Sequence: 1, LegCount: scoringLegCount,
		StartedUnixMS: ended - int64(scoringLegLength/time.Millisecond), EndedUnixMS: ended,
		DurationMS: int64(scoringLegLength / time.Millisecond), Players: players,
	}
}

func TestCompetitionChildCannotInheritManagedReporterCredential(t *testing.T) {
	roster := testRoster(t)
	environment := competitionEnvironment([]string{
		"PATH=/usr/bin", "RSCTF_KOTH_GAME_ID=7", "RSCTF_KOTH_CHALLENGE_ID=11",
		"RSCTF_KOTH_PLATFORM_URL=http://rsctf:8080",
		"RSCTF_KOTH_CONTEXT_URL=http://rsctf:8080/context",
		"RSCTF_KOTH_OBSERVATION_URL=http://rsctf:8080/observations",
		"RSCTF_KOTH_REPORTER_SECRET=" + testManagedSecret,
	}, config{reportToken: strings.Repeat("r", 64)}, roster, "managed-match")
	joined := strings.Join(environment, "\n")
	for _, forbidden := range []string{"RSCTF_KOTH_", testManagedSecret, "http://rsctf:8080"} {
		if strings.Contains(joined, forbidden) {
			t.Fatalf("child inherited managed value %q", forbidden)
		}
	}
	for _, required := range []string{
		"REPORT_URL=http://127.0.0.1:8080/internal/match-report",
		"REPLAY_UPLOAD_URL=http://127.0.0.1:8080/internal/replay",
	} {
		if !slices.Contains(environment, required) {
			t.Fatalf("child is missing %q: %#v", required, environment)
		}
	}
}

func TestManagedDecodeRejectsOversizedWhitespaceAndTrailingJSON(t *testing.T) {
	valid := []byte(`{"accepted":true}`)
	oversized := append(append([]byte(nil), valid...), bytes.Repeat([]byte(" "), managedMaxResponse+1-len(valid))...)
	var accepted acceptedObservation
	if err := decodeBounded(bytes.NewReader(oversized), &accepted); err == nil {
		t.Fatal("oversized response was accepted")
	}
	if err := decodeBounded(strings.NewReader(`{"accepted":true}{}`), &accepted); err == nil {
		t.Fatal("trailing JSON was accepted")
	}
}
