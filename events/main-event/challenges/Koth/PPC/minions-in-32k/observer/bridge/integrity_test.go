package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestInflatedFirstLegConfirmationPrefixIsRejected(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	report := validReport(time.Now().UTC())
	report.Players[0].ConfirmedKills = 50_000
	recorder := postReport(b, report)
	if recorder.Code != http.StatusConflict ||
		!strings.Contains(recorder.Body.String(), "prefix exceeds") {
		t.Fatalf("inflated leg status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestCumulativePrefixAllowsAConfirmationFromPreviousLeg(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	first := validReport(time.Now().UTC().Add(-time.Minute))
	first.Players[0].Kills = 4
	first.Players[0].ConfirmedKills = 2
	if recorder := postReport(b, first); recorder.Code != http.StatusNoContent {
		t.Fatalf("first leg status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	second := reportAtSequence(first, 2)
	second.Players[0].Kills = 2
	second.Players[0].ConfirmedKills = 3
	if recorder := postReport(b, second); recorder.Code != http.StatusNoContent {
		t.Fatalf("cross-boundary leg status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestAutomatedCombatIsPrivatelyExcludedFromEvidence(t *testing.T) {
	report := matchReport{Players: []reportPlayer{
		{Name: "human", Connected: true, ConfirmedKills: 3, IntegrityStatus: integrityClean},
		{Name: "bot", Connected: true, ConfirmedKills: 50, IntegrityStatus: integrityAutomatedCombat},
	}}
	possible, scores, crown := scoreMatch(report, map[string]bool{"human": true, "bot": true})
	if possible != 3 || scores["human"] != 3 || scores["bot"] != 0 || crown != "human" {
		t.Fatalf("possible=%d scores=%#v crown=%q", possible, scores, crown)
	}
}

func TestAutomatedCombatStatusDoesNotLeakIntoObserverEvidence(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	now := time.Now().UTC().Truncate(time.Millisecond)
	humanHash := strings.Repeat("a", 64)
	botHash := strings.Repeat("b", 64)
	b.waves["integrity"] = finalizedWave{
		ID: "m32-integrity", EndedAt: now,
		Result: matchReport{Players: []reportPlayer{
			{Name: "alpha", Connected: true, ConfirmedKills: 3, IntegrityStatus: integrityClean},
			{Name: "beta", Connected: true, ConfirmedKills: 50, IntegrityStatus: integrityAutomatedCombat},
		}},
		Identities: map[string]string{"alpha": humanHash, "beta": botHash},
	}
	body, _, _, err := b.buildObservation(observerContext{
		Context: strings.Repeat("e", 64), RoundNumber: 2,
		WaveWindowStartsAt:  now.Add(-time.Second).UnixMilli(),
		WaveWindowEndsAt:    now.Add(time.Second).UnixMilli(),
		EligibleTokenHashes: []string{humanHash, botHash},
	})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), integrityAutomatedCombat) {
		t.Fatalf("private classification leaked into observer JSON: %s", body)
	}
	var value observation
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	for _, team := range value.Waves[0].Teams {
		switch team.TokenHash {
		case humanHash:
			if team.Activity.Earned != 1 || team.Objectives[0] != (evidenceRatio{Earned: 3, Possible: 3}) || !team.IsCrown {
				t.Fatalf("human evidence=%#v", team)
			}
		case botHash:
			if team.Activity.Earned != 0 || team.Objectives[0] != (evidenceRatio{Earned: 0, Possible: 3}) || team.IsCrown {
				t.Fatalf("automated evidence=%#v", team)
			}
		}
	}
}

func TestReportRequiresExplicitIntegrityStatus(t *testing.T) {
	empty := httptest.NewServer(http.NotFoundHandler())
	defer empty.Close()
	b := testBridge(t, empty)
	report := validReport(time.Now().UTC())
	report.Players[0].IntegrityStatus = ""
	if b.validateReport(report, time.Now().UTC()) == nil {
		t.Fatal("report without integrity status was accepted")
	}
}
