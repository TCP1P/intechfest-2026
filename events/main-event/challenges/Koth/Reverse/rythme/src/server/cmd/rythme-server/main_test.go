package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestCoordinatorHealthcheckRequiresHealthyJSON(t *testing.T) {
	healthy := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"status":"ok"}`))
	}))
	defer healthy.Close()
	if err := coordinatorHealthcheck(healthy.URL); err != nil {
		t.Fatalf("healthy coordinator was rejected: %v", err)
	}

	unhealthy := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		http.Error(response, "unhealthy", http.StatusServiceUnavailable)
	}))
	defer unhealthy.Close()
	if err := coordinatorHealthcheck(unhealthy.URL); err == nil {
		t.Fatal("unhealthy coordinator was accepted")
	}
}

func TestDefaultReadyWindowIsFixedAtThirtySeconds(t *testing.T) {
	if defaultReadyDuration != 30*time.Second {
		t.Fatalf("default ready duration is %s, want 30s", defaultReadyDuration)
	}
}

func TestManagedReporterEnvironmentIsAllOrNothing(t *testing.T) {
	names := []string{
		"RSCTF_KOTH_GAME_ID", "RSCTF_KOTH_CHALLENGE_ID", "RSCTF_KOTH_PLATFORM_URL",
		"RSCTF_KOTH_CONTEXT_URL", "RSCTF_KOTH_OBSERVATION_URL", "RSCTF_KOTH_REPORTER_SECRET",
	}
	for _, name := range names {
		t.Setenv(name, "")
	}
	if enabled, err := managedReporterEnvironment(); enabled || err != nil {
		t.Fatalf("empty environment enabled=%v error=%v", enabled, err)
	}
	t.Setenv(names[0], "7")
	if enabled, err := managedReporterEnvironment(); enabled || err == nil {
		t.Fatalf("partial environment enabled=%v error=%v", enabled, err)
	}
	for _, name := range names {
		t.Setenv(name, "configured")
	}
	if enabled, err := managedReporterEnvironment(); !enabled || err != nil {
		t.Fatalf("complete environment enabled=%v error=%v", enabled, err)
	}
}

func TestBundledRealHardPool(t *testing.T) {
	sharedDir := filepath.Join("..", "..", "..", "shared")
	pool, err := loadPool(filepath.Join(sharedDir, "pool.json"))
	if err != nil {
		t.Fatalf("load bundled pool: %v", err)
	}
	if pool.Schema != "rythme-pool-v2" || pool.Source != "organizer-curated-real-osu-beatmaps" {
		t.Fatalf("unexpected pool identity: schema=%q source=%q", pool.Schema, pool.Source)
	}
	if len(pool.Songs) != 21 {
		t.Fatalf("got %d exact difficulties, want 21", len(pool.Songs))
	}

	modeCounts := make(map[string]int)
	seenBeatmaps := make(map[int64]struct{}, len(pool.Songs))
	for _, song := range pool.Songs {
		modeCounts[string(song.Ruleset)]++
		if song.StarRating < 3.5 {
			t.Errorf("%s is only %.2f stars", song.ID, song.StarRating)
		}
		if song.BeatmapID <= 0 || song.BeatmapsetID <= 0 || song.Difficulty == "" {
			t.Errorf("%s lacks exact beatmap metadata", song.ID)
		}
		if song.NoteCount != len(song.Notes) || song.NoteCount < 100 {
			t.Errorf("%s extracted %d/%d objects", song.ID, len(song.Notes), song.NoteCount)
		}
		if _, duplicate := seenBeatmaps[song.BeatmapID]; duplicate {
			t.Errorf("duplicate beatmap ID %d", song.BeatmapID)
		}
		seenBeatmaps[song.BeatmapID] = struct{}{}
	}
	for _, mode := range []string{"osu", "taiko", "fruits", "mania"} {
		if modeCounts[mode] < 4 {
			t.Errorf("mode %s has only %d exact maps", mode, modeCounts[mode])
		}
	}

	emptyCache := t.TempDir()
	if err := validateBeatmapCache(pool, emptyCache, false); err != nil {
		t.Fatalf("empty cache should use pinned remote fallback: %v", err)
	}
	if err := validateBeatmapCache(pool, emptyCache, true); err == nil {
		t.Fatal("offline mode accepted an incomplete beatmap cache")
	}

	first := pool.Songs[0]
	if err := os.WriteFile(filepath.Join(emptyCache, first.ID+".osz"), []byte("tampered"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := validateBeatmapCache(pool, emptyCache, false); err == nil {
		t.Fatal("tampered cached archive was accepted")
	}
}
