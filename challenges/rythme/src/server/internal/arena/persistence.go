package arena

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
)

const (
	persistentStateSchema  = "rythme-arena-state-v1"
	maxPersistentStateSize = 64 << 20
	maxPersistentHeatIndex = int64(^uint64(0)>>1) - seriesLegCount
)

type persistentArenaState struct {
	Schema            string                 `json:"schema"`
	HeatIndex         int64                  `json:"heat_index"`
	ActiveHeatSettled bool                   `json:"active_heat_settled"`
	SongBags          map[Ruleset][]string   `json:"song_bags"`
	LastSongs         map[Ruleset]string     `json:"last_songs"`
	RulesetBag        []Ruleset              `json:"ruleset_bag"`
	LastRuleset       Ruleset                `json:"last_ruleset,omitempty"`
	SeriesProgress    []persistentTeamSeries `json:"series_progress"`
	ScoringWaves      []ScoringWaveView      `json:"scoring_waves"`
	CrownOwnerID      string                 `json:"crown_owner_id,omitempty"`
	CrownOwnerName    string                 `json:"crown_owner_name,omitempty"`
	CrownSinceUnixMS  int64                  `json:"crown_since_unix_ms,omitempty"`
}

type persistentTeamSeries struct {
	TeamID   string             `json:"team_id"`
	TeamName string             `json:"team_name"`
	Legs     map[int]Settlement `json:"legs"`
}

func loadPersistentState(path string) (persistentArenaState, bool, error) {
	info, err := os.Lstat(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return persistentArenaState{}, false, nil
		}
		return persistentArenaState{}, false, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return persistentArenaState{}, false, fmt.Errorf("state path must be a regular file")
	}
	if info.Size() > maxPersistentStateSize {
		return persistentArenaState{}, false, fmt.Errorf("state file exceeds %d bytes", maxPersistentStateSize)
	}

	file, err := os.Open(path)
	if err != nil {
		return persistentArenaState{}, false, err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, maxPersistentStateSize+1))
	if err != nil {
		return persistentArenaState{}, false, err
	}
	if len(raw) > maxPersistentStateSize {
		return persistentArenaState{}, false, fmt.Errorf("state file exceeds %d bytes", maxPersistentStateSize)
	}

	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var state persistentArenaState
	if err := decoder.Decode(&state); err != nil {
		return persistentArenaState{}, false, err
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return persistentArenaState{}, false, err
	}
	if err := validatePersistentState(state); err != nil {
		return persistentArenaState{}, false, err
	}
	return state, true, nil
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return fmt.Errorf("state file contains multiple JSON values")
		}
		return err
	}
	return nil
}

func validatePersistentState(state persistentArenaState) error {
	if state.Schema != persistentStateSchema {
		return fmt.Errorf("unsupported state schema %q", state.Schema)
	}
	if state.HeatIndex <= 0 || state.HeatIndex > maxPersistentHeatIndex {
		return fmt.Errorf("invalid heat index %d", state.HeatIndex)
	}
	if len(state.ScoringWaves) > maxScoringWaves {
		return fmt.Errorf("scoring ledger exceeds %d waves", maxScoringWaves)
	}
	if len(state.SeriesProgress) > maxSessions {
		return fmt.Errorf("series progress exceeds %d teams", maxSessions)
	}

	rulesetBag := make(map[Ruleset]struct{}, len(state.RulesetBag))
	for _, ruleset := range state.RulesetBag {
		if !knownRuleset(ruleset) {
			return fmt.Errorf("ruleset bag contains %q", ruleset)
		}
		if _, duplicate := rulesetBag[ruleset]; duplicate {
			return fmt.Errorf("ruleset bag contains duplicate %q", ruleset)
		}
		rulesetBag[ruleset] = struct{}{}
	}
	if state.LastRuleset != "" && !knownRuleset(state.LastRuleset) {
		return fmt.Errorf("invalid last ruleset %q", state.LastRuleset)
	}
	for ruleset := range state.SongBags {
		if !knownRuleset(ruleset) {
			return fmt.Errorf("song bag contains invalid ruleset %q", ruleset)
		}
	}
	for ruleset := range state.LastSongs {
		if !knownRuleset(ruleset) {
			return fmt.Errorf("last-song map contains invalid ruleset %q", ruleset)
		}
	}

	seenWaves := make(map[string]struct{}, len(state.ScoringWaves))
	for _, wave := range state.ScoringWaves {
		if wave.WaveID == "" || wave.EndedAtUnixMS <= 0 || !knownRuleset(wave.Ruleset) {
			return fmt.Errorf("scoring ledger contains an invalid wave")
		}
		if _, duplicate := seenWaves[wave.WaveID]; duplicate {
			return fmt.Errorf("scoring ledger contains duplicate wave %q", wave.WaveID)
		}
		seenWaves[wave.WaveID] = struct{}{}
	}
	if len(state.ScoringWaves) == 0 {
		if state.CrownOwnerID != "" || state.CrownOwnerName != "" || state.CrownSinceUnixMS != 0 {
			return fmt.Errorf("Crown state exists without a finalized wave")
		}
	} else {
		latest := state.ScoringWaves[len(state.ScoringWaves)-1]
		if latest.CrownOwnerID != state.CrownOwnerID || latest.CrownOwnerName != state.CrownOwnerName {
			return fmt.Errorf("Crown state does not match the latest finalized wave")
		}
		if state.CrownOwnerID == "" && state.CrownSinceUnixMS != 0 {
			return fmt.Errorf("empty Crown owner has a Crown timestamp")
		}
		if state.CrownOwnerID != "" && state.CrownSinceUnixMS <= 0 {
			return fmt.Errorf("Crown owner is missing its timestamp")
		}
	}

	seenTeams := make(map[string]struct{}, len(state.SeriesProgress))
	for _, progress := range state.SeriesProgress {
		if progress.TeamID == "" || progress.TeamName == "" || len(progress.Legs) > seriesLegCount {
			return fmt.Errorf("series progress contains an invalid team")
		}
		if _, duplicate := seenTeams[progress.TeamID]; duplicate {
			return fmt.Errorf("series progress contains duplicate team %q", progress.TeamID)
		}
		seenTeams[progress.TeamID] = struct{}{}
		for leg, settlement := range progress.Legs {
			if leg < 1 || leg > seriesLegCount || settlement.TeamID != progress.TeamID {
				return fmt.Errorf("series progress for %q contains an invalid leg", progress.TeamID)
			}
		}
	}
	return nil
}

func knownRuleset(ruleset Ruleset) bool {
	for _, candidate := range AllRulesets {
		if ruleset == candidate {
			return true
		}
	}
	return false
}

func (a *Arena) restorePersistentState(state persistentArenaState) error {
	knownSongs := make(map[string]Ruleset, len(a.pool.Songs))
	for _, song := range a.pool.Songs {
		knownSongs[song.ID] = song.Ruleset
	}

	a.heatIndex = state.HeatIndex
	a.songBags = make(map[Ruleset][]string, len(state.SongBags))
	for ruleset, bag := range state.SongBags {
		seen := make(map[string]struct{}, len(bag))
		for _, songID := range bag {
			if knownSongs[songID] != ruleset {
				continue
			}
			if _, duplicate := seen[songID]; duplicate {
				return fmt.Errorf("%s song bag contains duplicate %q", ruleset, songID)
			}
			seen[songID] = struct{}{}
			a.songBags[ruleset] = append(a.songBags[ruleset], songID)
		}
	}
	a.lastSongs = make(map[Ruleset]string, len(state.LastSongs))
	for ruleset, songID := range state.LastSongs {
		if knownSongs[songID] == ruleset {
			a.lastSongs[ruleset] = songID
		}
	}
	a.rulesetBag = append([]Ruleset(nil), state.RulesetBag...)
	a.lastRuleset = state.LastRuleset
	a.scoringWaves = cloneScoringWaves(state.ScoringWaves)
	a.crownOwnerID = state.CrownOwnerID
	a.crownOwner = state.CrownOwnerName
	if state.CrownSinceUnixMS > 0 {
		a.crownSince = time.UnixMilli(state.CrownSinceUnixMS).UTC()
	}
	a.seriesProgress = make(map[string]*teamSeries, len(state.SeriesProgress))
	for _, progress := range state.SeriesProgress {
		legs := make(map[int]Settlement, len(progress.Legs))
		for leg, settlement := range progress.Legs {
			legs[leg] = settlement
		}
		a.seriesProgress[progress.TeamID] = &teamSeries{
			teamID: progress.TeamID, teamName: progress.TeamName, legs: legs,
		}
	}

	if !state.ActiveHeatSettled {
		// The interrupted heat cannot be authenticated after cue/session keys
		// rotate. Skip the rest of its four-leg series so every team starts the
		// replacement series at leg one under one process lifetime.
		a.heatIndex = ((a.heatIndex + seriesLegCount - 1) / seriesLegCount) * seriesLegCount
		a.seriesProgress = make(map[string]*teamSeries)
	}
	return nil
}

func cloneScoringWaves(waves []ScoringWaveView) []ScoringWaveView {
	cloned := make([]ScoringWaveView, len(waves))
	for index, wave := range waves {
		cloned[index] = wave
		cloned[index].Results = append(make([]ScoringResultView, 0, len(wave.Results)), wave.Results...)
	}
	return cloned
}

func (a *Arena) persistentStateLocked() persistentArenaState {
	progress := make([]persistentTeamSeries, 0, len(a.seriesProgress))
	for _, current := range a.seriesProgress {
		legs := make(map[int]Settlement, len(current.legs))
		for leg, settlement := range current.legs {
			legs[leg] = settlement
		}
		progress = append(progress, persistentTeamSeries{
			TeamID: current.teamID, TeamName: current.teamName, Legs: legs,
		})
	}
	sort.Slice(progress, func(i, j int) bool { return progress[i].TeamID < progress[j].TeamID })

	return persistentArenaState{
		Schema:            persistentStateSchema,
		HeatIndex:         a.heatIndex,
		ActiveHeatSettled: a.heat != nil && a.heat.settled,
		SongBags:          a.songBags,
		LastSongs:         a.lastSongs,
		RulesetBag:        a.rulesetBag,
		LastRuleset:       a.lastRuleset,
		SeriesProgress:    progress,
		ScoringWaves:      a.scoringWaves,
		CrownOwnerID:      a.crownOwnerID,
		CrownOwnerName:    a.crownOwner,
		CrownSinceUnixMS: func() int64 {
			if a.crownSince.IsZero() {
				return 0
			}
			return a.crownSince.UnixMilli()
		}(),
	}
}

func (a *Arena) persistLocked() {
	if a.statePath == "" {
		return
	}
	if err := writePersistentState(a.statePath, a.persistentStateLocked()); err != nil {
		a.persistenceErr = err
		return
	}
	a.persistenceErr = nil
}

func writePersistentState(path string, state persistentArenaState) error {
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	if len(raw) > maxPersistentStateSize {
		return fmt.Errorf("serialized state exceeds %d bytes", maxPersistentStateSize)
	}

	directory := filepath.Dir(path)
	directoryInfo, err := os.Stat(directory)
	if err != nil {
		return err
	}
	if !directoryInfo.IsDir() {
		return fmt.Errorf("state parent is not a directory")
	}
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return fmt.Errorf("state path must be a regular file")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}

	temporary, err := os.CreateTemp(directory, ".rythme-state-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	committed := false
	defer func() {
		_ = temporary.Close()
		if !committed {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if info, err := os.Lstat(path); err == nil && (info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular()) {
		return fmt.Errorf("state path changed to a non-regular file")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return err
	}
	committed = true

	directoryHandle, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer directoryHandle.Close()
	if err := directoryHandle.Sync(); err != nil {
		return err
	}
	return nil
}
