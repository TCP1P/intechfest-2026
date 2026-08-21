package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPinnedSeedLedger(t *testing.T) {
	sourcesPath := filepath.Join("..", "..", "..", "..", "shared", "pool.sources.json")
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatal(err)
	}

	var sources sourceManifest
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatal(err)
	}
	seeds, err := loadSeedLedger(filepath.Dir(sourcesPath), sources)
	if err != nil {
		t.Fatal(err)
	}
	if len(seeds) != 29 {
		t.Fatalf("loaded %d seeds, expected 29", len(seeds))
	}
	if seeds[1].Title != "言の刃" || seeds[29].Title != "Astrum Unicum / Stars, Rebirth, and You" {
		t.Fatal("the pinned ledger endpoints do not match the organizer list")
	}
	if !seeds[15].LocalOnly || seeds[15].Title != "回想部屋" {
		t.Fatal("the supplied local-only track was not preserved")
	}
	for _, entry := range sources.Entries {
		seed, ok := seeds[entry.SeedIndex]
		if !ok {
			t.Fatalf("%s references missing seed %d", entry.ID, entry.SeedIndex)
		}
		if entry.SeedTitle != seed.Title || entry.SeedArtist != seed.Artist {
			t.Fatalf("%s does not match seed %d", entry.ID, entry.SeedIndex)
		}
	}
}

func TestPinnedSeedLedgerRejectsHashMismatch(t *testing.T) {
	sourcesPath := filepath.Join("..", "..", "..", "..", "shared", "pool.sources.json")
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatal(err)
	}

	var sources sourceManifest
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatal(err)
	}
	sources.SeedLedgerSHA256 = strings.Repeat("0", 64)
	if _, err := loadSeedLedger(filepath.Dir(sourcesPath), sources); err == nil {
		t.Fatal("tampered seed ledger hash was accepted")
	}
}
