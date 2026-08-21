package arena

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestPersistentArenaRestoresFinalizedLedgerCrownAndSeries(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "arena.json")
	game, err := NewPersistent(testConfig(701), testPool(), statePath)
	if err != nil {
		t.Fatal(err)
	}
	session, err := game.CreateLocalSession("durable-team", "Durable Crew")
	if err != nil {
		t.Fatal(err)
	}

	settlement, final := completeEligibleLeg(t, game, session, time.Now().UTC())
	if final.CrownOwnerID != session.TeamID || !settlement.Eligible {
		t.Fatalf("test run did not produce durable Crown evidence: %+v", final)
	}
	info, err := os.Stat(statePath)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("state permissions are %o, want 600", got)
	}

	restored, err := NewPersistent(testConfig(702), testPool(), statePath)
	if err != nil {
		t.Fatal(err)
	}
	view := restored.View(time.Now().UTC())
	if view.Heat.Index != final.Heat.Index+1 {
		t.Fatalf("restored heat index is %d, want %d", view.Heat.Index, final.Heat.Index+1)
	}
	if view.CrownOwnerID != session.TeamID || view.CrownOwnerName != session.TeamName || view.CrownSinceUnixMS == 0 {
		t.Fatalf("restored Crown is incomplete: %+v", view)
	}
	if len(view.Series.Standings) != 1 || view.Series.Standings[0].CompletedLegs != 1 {
		t.Fatalf("restored series progress is incomplete: %+v", view.Series)
	}
	waves := restored.ScoringWindow(
		time.UnixMilli(0),
		time.UnixMilli(final.Heat.NextHeatAt+1),
		time.Now().UTC(),
	)
	if len(waves.Waves) != 1 || waves.Waves[0].WaveID != final.Heat.ID || waves.Waves[0].Results[0].TeamID != session.TeamID {
		t.Fatalf("restored scoring ledger is incomplete: %+v", waves)
	}
}

func TestPersistentArenaVoidsInterruptedSeries(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "arena.json")
	game, err := NewPersistent(testConfig(703), testPool(), statePath)
	if err != nil {
		t.Fatal(err)
	}
	initial := game.View(time.Now().UTC())
	if initial.Heat.Index != 1 {
		t.Fatalf("initial heat index is %d", initial.Heat.Index)
	}

	restored, err := NewPersistent(testConfig(704), testPool(), statePath)
	if err != nil {
		t.Fatal(err)
	}
	view := restored.View(time.Now().UTC())
	if view.Heat.Index != 5 || view.Series.Index != 2 || view.Series.CurrentLeg != 1 {
		t.Fatalf("interrupted heat did not restart at a fresh series: %+v", view)
	}
	if len(view.Series.Standings) != 0 || view.CrownOwnerID != "" {
		t.Fatalf("interrupted series leaked volatile progress: %+v", view)
	}
}

func TestPersistentArenaRejectsCorruptState(t *testing.T) {
	statePath := filepath.Join(t.TempDir(), "arena.json")
	if err := os.WriteFile(statePath, []byte(`{"schema":"rythme-arena-state-v1","heat_index":1} trailing`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := NewPersistent(testConfig(705), testPool(), statePath); err == nil {
		t.Fatal("corrupt persistent state was accepted")
	}
}

func TestPersistentArenaFailsHealthAndSchedulerOnWriteError(t *testing.T) {
	directory := t.TempDir()
	statePath := filepath.Join(directory, "arena.json")
	game, err := NewPersistent(testConfig(706), testPool(), statePath)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(statePath); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(statePath, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := game.SetPool(testPool()); err == nil {
		t.Fatal("state write to a directory unexpectedly succeeded")
	}
	if game.Healthy(time.Now().UTC()) {
		t.Fatal("arena remained healthy after durable-state failure")
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := game.Run(ctx); err == nil {
		t.Fatal("scheduler did not fail after durable-state failure")
	}
}

func testConfig(seed int64) Config {
	return Config{
		EnrollmentDuration: time.Second,
		ReadyDuration:      time.Second,
		SubmissionDuration: time.Second,
		ResultsDuration:    time.Second,
		CrownThreshold:     2_100_000,
		RawScoreFloor:      700_000,
		Seed:               seed,
	}
}
