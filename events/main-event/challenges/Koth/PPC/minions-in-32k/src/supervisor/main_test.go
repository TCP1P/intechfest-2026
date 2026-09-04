package main

import (
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"
)

func testRoster(t *testing.T) arenaRoster {
	t.Helper()
	players := make([]startPlayer, 0, 8)
	players = append(players, startPlayer{UUID: "alpha", Key: strings.Repeat("a", 48), Name: "alpha"})
	for index := 1; index < 8; index++ {
		players = append(players, startPlayer{
			UUID: "npc-" + string(rune('0'+index)), Key: strings.Repeat(string(rune('a'+index)), 48),
			Name: "NPC_" + string(rune('0'+index)), Bot: true,
		})
	}
	roster, err := buildRoster(players)
	if err != nil {
		t.Fatal(err)
	}
	return roster
}

func TestHealthAdvertisesOfficialArenaAnd32KFramebuffer(t *testing.T) {
	roster := testRoster(t)
	a := &arena{cfg: config{
		reportToken: strings.Repeat("s", 48), certHash: strings.Repeat("a", 64), roster: &roster,
	}}
	recorder := httptest.NewRecorder()
	a.health(recorder, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"protocol":"minions/0"`) ||
		!strings.Contains(recorder.Body.String(), `"protocolVersion":13`) ||
		!strings.Contains(recorder.Body.String(), `"renderWidth":256`) || !strings.Contains(recorder.Body.String(), `"renderHeight":128`) ||
		!strings.Contains(recorder.Body.String(), `"players":8`) || !strings.Contains(recorder.Body.String(), `"npcs":7`) ||
		!strings.Contains(recorder.Body.String(), `"certHash":"`+strings.Repeat("a", 64)+`"`) {
		t.Fatalf("unexpected health response: %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestStartRejectsMissingOrWrongControllerSecret(t *testing.T) {
	a := &arena{cfg: config{reportToken: strings.Repeat("s", 48)}}
	for _, authorization := range []string{"", "Bearer wrong"} {
		request := httptest.NewRequest(http.MethodPost, "/internal/start", nil)
		request.Header.Set("Authorization", authorization)
		recorder := httptest.NewRecorder()
		a.start(recorder, request)
		if recorder.Code != http.StatusUnauthorized {
			t.Fatalf("authorization %q: status=%d", authorization, recorder.Code)
		}
	}
}

func TestBoundedIntegerAndBearerParsing(t *testing.T) {
	t.Setenv("TEST_INTEGER", "17")
	if value, err := boundedInteger("TEST_INTEGER", 1, 20, 5); err != nil || value != 17 {
		t.Fatalf("value=%d error=%v", value, err)
	}
	t.Setenv("TEST_INTEGER", "21")
	if _, err := boundedInteger("TEST_INTEGER", 1, 20, 5); err == nil {
		t.Fatal("out-of-range value was accepted")
	}
	if token, ok := bearerToken("Bearer secret"); !ok || token != "secret" {
		t.Fatal("canonical bearer rejected")
	}
	if _, ok := bearerToken("Bearer has space"); ok {
		t.Fatal("ambiguous bearer accepted")
	}
}

func TestBotRosterRequiresDistinctArenaKeysAndOneHuman(t *testing.T) {
	players := `[{"key":"human"},{"key":"npc-1"},{"key":"npc-2"}]`
	if count, err := validateBotRoster(players, "npc-1,npc-2", 2); err != nil || count != 3 {
		t.Fatalf("count=%d error=%v", count, err)
	}
	for _, test := range []struct {
		keys  string
		count int
	}{
		{"npc-1,missing", 2},
		{"npc-1,npc-1", 2},
		{"npc-1,npc-2,human", 3},
		{"npc-1", 2},
	} {
		if _, err := validateBotRoster(players, test.keys, test.count); err == nil {
			t.Fatalf("invalid bot roster accepted: keys=%q count=%d", test.keys, test.count)
		}
	}
}

func TestSupervisorStartsEveryBackfillBotAsBalancedNPC(t *testing.T) {
	roster := arenaRoster{botCount: 7, botKeys: "one,two,three,four,five,six,seven"}
	arguments := strings.Join(botArguments(config{certHash: strings.Repeat("a", 64)}, roster), " ")
	if !strings.Contains(arguments, "--count 7 --npc-count 7") || strings.Contains(arguments, "--relay-count") {
		t.Fatalf("unexpected bot arguments: %s", arguments)
	}
}

func TestIdleQualityProbeCannotPublishOrReuseCompetitionIdentity(t *testing.T) {
	environment := qualityProbeEnvironment([]string{
		"PATH=/usr/bin",
		"PLAYER_TOKENS=competition-roster",
		"PLAYER_TOKENS_FILE=/run/competition-roster",
		"MATCH_ID=competition-match",
		"REPORT_URL=https://collector.example/report",
		"REPLAY_UPLOAD_URL=https://collector.example/replay",
		"REPORT_TOKEN=competition-secret",
		"MINIONS_SCORING_LEG_SECS=60",
		"MINIONS_READY_FILE=/tmp/competition-ready",
		"WT_CERT_DER_B64=certificate",
	}, `[{"uuid":"quality","key":"quality-key","name":"Quality"}]`, "/tmp/quality-ready", "fresh-cert", "fresh-key")
	joined := strings.Join(environment, "\n")
	for _, secret := range []string{
		"competition-roster", "competition-match", "collector.example",
		"competition-secret", "/tmp/competition-ready",
	} {
		if strings.Contains(joined, secret) {
			t.Fatalf("quality environment retained competition value %q", secret)
		}
	}
	if slices.Contains(environment, "WT_CERT_DER_B64=certificate") ||
		!slices.Contains(environment, "WT_CERT_DER_B64=fresh-cert") ||
		!slices.Contains(environment, "WT_KEY_DER_B64=fresh-key") ||
		!slices.Contains(environment, "MATCH_ID=minions-quality-probe") ||
		!slices.Contains(environment, "MINIONS_READY_FILE=/tmp/quality-ready") {
		t.Fatalf("quality environment is incomplete: %#v", environment)
	}
}

func TestRuntimeSeedDerivesStableControllerSecret(t *testing.T) {
	seed := "flag{" + strings.Repeat("a", 48) + "}"
	first, err := deriveReportToken(seed)
	if err != nil {
		t.Fatal(err)
	}
	second, err := deriveReportToken(seed)
	if err != nil || first != second || len(first) != 64 {
		t.Fatalf("first=%q second=%q error=%v", first, second, err)
	}
	if _, err := deriveReportToken("short"); err == nil {
		t.Fatal("short runtime seed was accepted")
	}
}

func TestGeneratedTLSIdentityIsPinnedAndShortLived(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	certBase64, keyBase64, err := generateTLSIdentity(now)
	if err != nil {
		t.Fatal(err)
	}
	certDER, err := base64.StdEncoding.DecodeString(certBase64)
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := x509.ParseCertificate(certDER)
	if err != nil {
		t.Fatal(err)
	}
	if certificate.NotAfter.After(now.Add(14*24*time.Hour)) || certificate.NotAfter.Before(now.Add(12*24*time.Hour)) {
		t.Fatalf("unexpected validity: %s", certificate.NotAfter)
	}
	if _, err := x509.ParsePKCS8PrivateKey(mustDecodeBase64(t, keyBase64)); err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(certDER)
	if len(hash) != sha256.Size {
		t.Fatal("certificate pin could not be calculated")
	}
}

func TestStartRosterIsStrictAndDoesNotNeedImageEnvironment(t *testing.T) {
	players := []startPlayer{
		{UUID: "human", Key: strings.Repeat("h", 48), Name: "Human"},
		{UUID: "npc", Key: strings.Repeat("n", 48), Name: "NPC_1", Bot: true},
	}
	body, err := json.Marshal(startRequest{Players: players})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/internal/start", strings.NewReader(string(body)))
	roster, err := readStartRoster(httptest.NewRecorder(), request)
	if err != nil || roster.playerCount != 2 || roster.botCount != 1 {
		t.Fatalf("roster=%#v error=%v", roster, err)
	}
	players[1].Key = players[0].Key
	if _, err := buildRoster(players); err == nil {
		t.Fatal("duplicate arena credential was accepted")
	}
}

func mustDecodeBase64(t *testing.T, value string) []byte {
	t.Helper()
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		t.Fatal(err)
	}
	return decoded
}
