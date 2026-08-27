package arena

import (
	"encoding/json"
	mathrand "math/rand"
	"os"
	"testing"
)

// TestReportFastSmokeSeed is an opt-in author utility for selecting a short,
// deterministic four-wave rotation from the real checked pool. It never changes
// the production selector or map data.
func TestReportFastSmokeSeed(t *testing.T) {
	if os.Getenv("RYTHME_REPORT_SMOKE_SEED") != "1" {
		t.Skip("set RYTHME_REPORT_SMOKE_SEED=1 to search")
	}
	raw, err := os.ReadFile("../../../shared/pool.json")
	if err != nil {
		t.Fatal(err)
	}
	var pool Pool
	if err := json.Unmarshal(raw, &pool); err != nil {
		t.Fatal(err)
	}

	bestSeed := int64(0)
	bestDuration := int64(^uint64(0) >> 1)
	var bestSongs []string
	for seed := int64(1); seed <= 100_000; seed++ {
		a := &Arena{
			pool:      pool,
			rng:       mathrand.New(mathrand.NewSource(seed)),
			songBags:  make(map[Ruleset][]string),
			lastSongs: make(map[Ruleset]string),
		}
		total := int64(0)
		songs := make([]string, 0, seriesLegCount)
		for leg := 0; leg < seriesLegCount; leg++ {
			ruleset := a.nextRulesetLocked()
			song := a.nextSongLocked(ruleset)
			total += song.DurationMS
			songs = append(songs, string(ruleset)+":"+song.ID)
			// Mirror the nonce read between selection operations.
			nonce := make([]byte, 16)
			_, _ = a.rng.Read(nonce)
		}
		if total < bestDuration {
			bestSeed, bestDuration, bestSongs = seed, total, songs
		}
	}
	t.Logf("seed=%d total_duration_ms=%d songs=%v", bestSeed, bestDuration, bestSongs)
}
