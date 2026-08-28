package reporter

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"rythme.local/server/internal/arena"
)

func TestReporterConsumesRawRSCTFSuccessBodies(t *testing.T) {
	token := strings.Repeat("01", 32)
	secret := "koth_target_" + strings.Repeat("s", 48)
	var timestamps []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		switch request.Method + " " + request.URL.Path {
		case "GET /context":
			_ = json.NewEncoder(w).Encode(reportingContext{
				APIVersion: "v1", Context: token, CycleNumber: 4, ResetAttempt: 2, RoundNumber: 7,
				CycleEndsAt: 3_000, GeneratedAt: 1_500,
				WaveWindowStartsAt: 1_000, WaveWindowEndsAt: 2_000,
				EligibleTokenHashes: []string{token},
			})
		case "POST /observations":
			body, err := io.ReadAll(request.Body)
			timestamp := request.Header.Get("X-RSCTF-Timestamp")
			mac := hmac.New(sha256.New, []byte(secret))
			_, _ = mac.Write([]byte(timestamp + ".1.2."))
			_, _ = mac.Write(body)
			wantSignature := "sha256=" + hex.EncodeToString(mac.Sum(nil))
			if err != nil || !strings.Contains(string(body), `"context":"`+token+`"`) ||
				request.Header.Get("X-RSCTF-Signature") != wantSignature {
				t.Fatalf("invalid observation request body=%q error=%v", body, err)
			}
			timestamps = append(timestamps, timestamp)
			_ = json.NewEncoder(w).Encode(acceptedObservation{
				Accepted: true, CycleNumber: 4, ResetAttempt: 2, RoundNumber: 7,
				SubmittedWaves: 0, SubmittedTeams: 0, RecognizedTeams: 0,
			})
		default:
			http.NotFound(w, request)
		}
	}))
	defer server.Close()

	reporter := &Reporter{cfg: Config{
		GameID: 1, ChallengeID: 2, ContextURL: server.URL + "/context",
		ObservationURL: server.URL + "/observations", Secret: secret,
		Client: server.Client(), Logger: log.New(io.Discard, "", 0),
	}}
	current, err := reporter.fetchContext(context.Background())
	if err != nil || current.Context != token {
		t.Fatalf("context=%+v error=%v", current, err)
	}
	body, _, err := buildObservation(current, arena.ScoringWindowView{})
	if err != nil {
		t.Fatal(err)
	}
	accepted, err := reporter.submit(context.Background(), body)
	if err != nil || !accepted.Accepted || accepted.RoundNumber != 7 {
		t.Fatal(err)
	}
	if _, err := reporter.submit(context.Background(), body); err != nil {
		t.Fatal(err)
	}
	if len(timestamps) != 2 || timestamps[1] <= timestamps[0] {
		t.Fatalf("timestamps are not monotonic: %v", timestamps)
	}
}

func TestReporterTreatsContextAndSubmissionConflictsAsRoundGaps(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		http.Error(w, "inactive", http.StatusConflict)
	}))
	defer server.Close()
	reporter := &Reporter{cfg: Config{
		GameID: 1, ChallengeID: 2, ContextURL: server.URL,
		ObservationURL: server.URL, Secret: "koth_target_" + strings.Repeat("s", 48),
		Client: server.Client(),
	}}
	if _, err := reporter.fetchContext(context.Background()); !errors.Is(err, errRoundGap) {
		t.Fatalf("context conflict error=%v", err)
	}
	if _, err := reporter.submit(context.Background(), []byte(`{}`)); !errors.Is(err, errRoundGap) {
		t.Fatalf("submission conflict error=%v", err)
	}
}

func TestBuildObservationNormalizesEligibleScores(t *testing.T) {
	winner := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	other := "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
	unknown := "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	context := reportingContext{
		APIVersion:          "v1",
		Context:             winner,
		CycleNumber:         1,
		ResetAttempt:        1,
		RoundNumber:         1,
		CycleEndsAt:         4_000,
		WaveWindowStartsAt:  1_000,
		WaveWindowEndsAt:    3_000,
		GeneratedAt:         1_500,
		EligibleTokenHashes: []string{winner, other},
	}
	scoring := arena.ScoringWindowView{Waves: []arena.ScoringWaveView{{
		WaveID: "heat-2", EndedAtUnixMS: 2_000, CrownOwnerID: winner,
		Results: []arena.ScoringResultView{
			{TeamID: other, Score: 5},
			{TeamID: unknown, Score: 999},
			{TeamID: winner, Score: 10, IsCrown: true},
		},
	}}}
	body, teams, err := buildObservation(context, scoring)
	if err != nil {
		t.Fatal(err)
	}
	if teams != 2 {
		t.Fatalf("unexpected recognized team count %d", teams)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if len(value.Waves) != 1 || len(value.Waves[0].Teams) != 2 || value.Waves[0].Teams[0].TokenHash != winner {
		t.Fatalf("unexpected normalized observation %+v", value)
	}
	if value.Waves[0].Teams[0].Objectives[0] != (ratio{Earned: 10, Possible: 10}) || !value.Waves[0].Teams[0].IsCrown {
		t.Fatalf("winner evidence is wrong: %+v", value.Waves[0].Teams[0])
	}
}

func TestBuildObservationRecomputesCrownForTheCurrentEligibleRoster(t *testing.T) {
	winner := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	other := "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
	context := reportingContext{
		APIVersion:          "v1",
		Context:             winner,
		CycleNumber:         1,
		ResetAttempt:        1,
		RoundNumber:         1,
		CycleEndsAt:         4_000,
		WaveWindowStartsAt:  1_000,
		WaveWindowEndsAt:    3_000,
		GeneratedAt:         1_500,
		EligibleTokenHashes: []string{winner, other},
	}
	scoring := arena.ScoringWindowView{Waves: []arena.ScoringWaveView{{
		WaveID: "heat-2", EndedAtUnixMS: 2_000, CrownOwnerID: other,
		Results: []arena.ScoringResultView{
			{TeamID: winner, Score: 10},
			{TeamID: other, Score: 5, IsCrown: true},
		},
	}}}
	body, _, err := buildObservation(context, scoring)
	if err != nil {
		t.Fatal(err)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	if len(value.Waves) != 1 || len(value.Waves[0].Teams) != 2 ||
		!value.Waves[0].Teams[0].IsCrown || value.Waves[0].Teams[1].IsCrown {
		t.Fatalf("Crown was not recomputed from eligible scores: %+v", value.Waves)
	}
}

func TestDecodeBoundedRejectsOversizedWhitespaceAndTrailingJSON(t *testing.T) {
	valid := []byte(`{"accepted":true}`)
	oversized := append(append([]byte(nil), valid...), bytes.Repeat([]byte(" "), maxResponseBody+1-len(valid))...)
	var accepted acceptedObservation
	if err := decodeBounded(bytes.NewReader(oversized), &accepted); err == nil {
		t.Fatal("oversized response was accepted")
	}
	if err := decodeBounded(strings.NewReader(`{"accepted":true}{}`), &accepted); err == nil {
		t.Fatal("trailing JSON was accepted")
	}
}
