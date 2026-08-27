package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	stateSchemaVersion = 1
	maxStateBytes      = 8 * 1024 * 1024
	maxReplayBytes     = 128 * 1024 * 1024
	maxReplayFiles     = 24
	replayRetention    = 48 * time.Hour
)

var errStatePersistence = errors.New("bridge state persistence failed")

type persistentBridgeState struct {
	Schema        int                       `json:"schema"`
	GameID        int                       `json:"gameId"`
	ChallengeID   int                       `json:"challengeId"`
	TargetID      string                    `json:"targetId"`
	Sessions      map[string]liveSession    `json:"sessions"`
	Waves         map[string]finalizedWave  `json:"waves"`
	Matches       map[string]*matchSequence `json:"matches"`
	Replays       map[string]replayArtifact `json:"replays"`
	LastTimestamp int64                     `json:"lastTimestamp"`
}

func (b *bridge) rememberSession(name string, session liveSession) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	previous, existed := b.sessions[name]
	b.sessions[name] = session
	if err := b.persistStateLocked(); err != nil {
		if existed {
			b.sessions[name] = previous
		} else {
			delete(b.sessions, name)
		}
		return err
	}
	return nil
}

func (b *bridge) loadPersistentState(now time.Time) error {
	if err := os.MkdirAll(b.cfg.StateDir, 0o700); err != nil {
		return fmt.Errorf("create state directory: %w", err)
	}
	if err := os.Chmod(b.cfg.StateDir, 0o700); err != nil {
		return fmt.Errorf("secure state directory: %w", err)
	}
	if err := os.MkdirAll(b.replayDirectory(), 0o700); err != nil {
		return fmt.Errorf("create replay directory: %w", err)
	}
	path := b.statePath()
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		b.mu.Lock()
		err = b.persistStateLocked()
		b.mu.Unlock()
		return err
	}
	if err != nil {
		return fmt.Errorf("read state: %w", err)
	}
	if len(raw) > maxStateBytes {
		return errors.New("state file exceeds the size limit")
	}
	var state persistentBridgeState
	if err := decodeExact(raw, &state); err != nil {
		return fmt.Errorf("decode state: %w", err)
	}
	if state.Schema != stateSchemaVersion || state.GameID <= 0 || state.ChallengeID <= 0 ||
		!canonicalHash(state.TargetID) || len(state.Sessions) > len(b.cfg.Secrets.Teams) ||
		len(state.Waves) > 256 || len(state.Matches) > 64 || len(state.Replays) > maxReplayFiles {
		return errors.New("state file failed structural validation")
	}

	if state.GameID == b.cfg.GameID && state.ChallengeID == b.cfg.ChallengeID {
		b.sessions = nonNilSessions(state.Sessions)
		b.replays = nonNilReplays(state.Replays)
		b.lastTimestamp = state.LastTimestamp
		if state.TargetID == b.cfg.TargetID {
			b.waves = nonNilWaves(state.Waves)
			b.matches = nonNilMatches(state.Matches)
		}
	}
	for name, session := range b.sessions {
		team, found := b.teamByName(name)
		if !found || !team.ScoringEligible || !canonicalHash(session.TokenHash) ||
			session.LastSeen.After(now.Add(time.Minute)) {
			return errors.New("state file contains an invalid scoring identity")
		}
	}
	if err := b.recoverReplayFiles(now); err != nil {
		return err
	}
	b.mu.Lock()
	b.purgeExpiredLocked(now)
	b.purgeReplaysLocked(now)
	err = b.persistStateLocked()
	b.mu.Unlock()
	return err
}

func nonNilSessions(value map[string]liveSession) map[string]liveSession {
	if value == nil {
		return make(map[string]liveSession)
	}
	return value
}

func nonNilWaves(value map[string]finalizedWave) map[string]finalizedWave {
	if value == nil {
		return make(map[string]finalizedWave)
	}
	return value
}

func nonNilMatches(value map[string]*matchSequence) map[string]*matchSequence {
	if value == nil {
		return make(map[string]*matchSequence)
	}
	return value
}

func nonNilReplays(value map[string]replayArtifact) map[string]replayArtifact {
	if value == nil {
		return make(map[string]replayArtifact)
	}
	return value
}

func (b *bridge) persistStateLocked() error {
	if b.cfg.StateDir == "" {
		return nil
	}
	state := persistentBridgeState{
		Schema: stateSchemaVersion, GameID: b.cfg.GameID, ChallengeID: b.cfg.ChallengeID,
		TargetID: b.cfg.TargetID, Sessions: b.sessions, Waves: b.waves, Matches: b.matches,
		Replays: b.replays, LastTimestamp: b.lastTimestamp,
	}
	raw, err := json.Marshal(state)
	if err != nil {
		return fmt.Errorf("%w: encode: %v", errStatePersistence, err)
	}
	if len(raw) > maxStateBytes {
		return fmt.Errorf("%w: state exceeds size limit", errStatePersistence)
	}
	temporary, err := os.CreateTemp(b.cfg.StateDir, ".state-*.tmp")
	if err != nil {
		return fmt.Errorf("%w: create temporary file: %v", errStatePersistence, err)
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
		return fmt.Errorf("%w: chmod: %v", errStatePersistence, err)
	}
	if _, err := temporary.Write(raw); err != nil {
		return fmt.Errorf("%w: write: %v", errStatePersistence, err)
	}
	if err := temporary.Sync(); err != nil {
		return fmt.Errorf("%w: sync: %v", errStatePersistence, err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("%w: close: %v", errStatePersistence, err)
	}
	if err := os.Rename(temporaryPath, b.statePath()); err != nil {
		return fmt.Errorf("%w: replace: %v", errStatePersistence, err)
	}
	committed = true
	if directory, err := os.Open(b.cfg.StateDir); err == nil {
		_ = directory.Sync()
		_ = directory.Close()
	}
	return nil
}

func (b *bridge) acceptReplay(w http.ResponseWriter, r *http.Request) {
	provided, ok := bearerToken(r.Header.Get("Authorization"))
	if !ok || !sameSecret(provided, b.cfg.Secrets.ReportToken) {
		writeProblem(w, http.StatusUnauthorized, "invalid replay reporter")
		return
	}
	values := r.URL.Query()
	matchIDs, found := values["match_id"]
	if !found || len(values) != 1 || len(matchIDs) != 1 || !validMatchID(matchIDs[0]) {
		writeProblem(w, http.StatusBadRequest, "invalid replay match identity")
		return
	}
	if r.ContentLength == 0 || r.ContentLength > maxReplayBytes {
		writeProblem(w, http.StatusRequestEntityTooLarge, "replay exceeds the size limit")
		return
	}

	temporary, err := os.CreateTemp(b.replayDirectory(), ".upload-*.tmp")
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "replay storage unavailable")
		return
	}
	temporaryPath := temporary.Name()
	committed := false
	defer func() {
		_ = temporary.Close()
		if !committed {
			_ = os.Remove(temporaryPath)
		}
	}()
	hash := sha256.New()
	limited := &io.LimitedReader{R: r.Body, N: maxReplayBytes + 1}
	size, err := io.Copy(io.MultiWriter(temporary, hash), limited)
	if err != nil {
		writeProblem(w, http.StatusBadRequest, "replay upload was interrupted")
		return
	}
	if size == 0 || size > maxReplayBytes {
		writeProblem(w, http.StatusRequestEntityTooLarge, "replay exceeds the size limit")
		return
	}
	if err := temporary.Chmod(0o600); err != nil || temporary.Sync() != nil || temporary.Close() != nil {
		writeProblem(w, http.StatusServiceUnavailable, "replay storage unavailable")
		return
	}
	digest := hex.EncodeToString(hash.Sum(nil))
	matchID := matchIDs[0]
	artifact := replayArtifact{SHA256: digest, Size: size, ReceivedAt: time.Now().UTC()}

	b.mu.Lock()
	if existing, exists := b.replays[matchID]; exists && existing.SHA256 != digest {
		b.mu.Unlock()
		writeProblem(w, http.StatusConflict, "match replay was already retained with different evidence")
		return
	}
	finalPath := b.replayPath(matchID)
	if err := os.Rename(temporaryPath, finalPath); err != nil {
		b.mu.Unlock()
		writeProblem(w, http.StatusServiceUnavailable, "replay storage unavailable")
		return
	}
	committed = true
	previous, existed := b.replays[matchID]
	b.replays[matchID] = artifact
	b.purgeReplaysLocked(artifact.ReceivedAt)
	err = b.persistStateLocked()
	if err != nil {
		if existed {
			b.replays[matchID] = previous
		} else {
			delete(b.replays, matchID)
		}
	}
	b.mu.Unlock()
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "replay metadata could not be retained")
		return
	}
	log.Printf("replay retained id=%q bytes=%d sha256=%s", matchID, size, digest)
	w.Header().Set("X-Content-SHA256", digest)
	w.WriteHeader(http.StatusNoContent)
}

func (b *bridge) recoverReplayFiles(now time.Time) error {
	entries, err := os.ReadDir(b.replayDirectory())
	if err != nil {
		return fmt.Errorf("scan replay directory: %w", err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		path := filepath.Join(b.replayDirectory(), name)
		if strings.HasPrefix(name, ".upload-") || !strings.HasSuffix(name, ".m32r") {
			_ = os.Remove(path)
			continue
		}
		matchID := strings.TrimSuffix(name, ".m32r")
		if !validMatchID(matchID) {
			_ = os.Remove(path)
			continue
		}
		if _, exists := b.replays[matchID]; exists {
			continue
		}
		file, err := os.Open(path)
		if err != nil {
			return fmt.Errorf("open retained replay: %w", err)
		}
		hash := sha256.New()
		size, copyErr := io.Copy(hash, io.LimitReader(file, maxReplayBytes+1))
		closeErr := file.Close()
		if copyErr != nil || closeErr != nil || size == 0 || size > maxReplayBytes {
			_ = os.Remove(path)
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return fmt.Errorf("inspect retained replay: %w", err)
		}
		received := info.ModTime().UTC()
		if received.IsZero() || received.After(now.Add(time.Minute)) {
			received = now
		}
		b.replays[matchID] = replayArtifact{
			SHA256: hex.EncodeToString(hash.Sum(nil)), Size: size, ReceivedAt: received,
		}
	}
	return nil
}

func (b *bridge) purgeReplaysLocked(now time.Time) {
	type retained struct {
		matchID string
		at      time.Time
	}
	ordered := make([]retained, 0, len(b.replays))
	for matchID, artifact := range b.replays {
		if !canonicalHash(artifact.SHA256) || artifact.Size <= 0 || artifact.Size > maxReplayBytes ||
			artifact.ReceivedAt.IsZero() || now.Sub(artifact.ReceivedAt) > replayRetention {
			delete(b.replays, matchID)
			_ = os.Remove(b.replayPath(matchID))
			continue
		}
		ordered = append(ordered, retained{matchID: matchID, at: artifact.ReceivedAt})
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].at.After(ordered[j].at) })
	if len(ordered) > maxReplayFiles {
		for _, old := range ordered[maxReplayFiles:] {
			delete(b.replays, old.matchID)
			_ = os.Remove(b.replayPath(old.matchID))
		}
	}
}

func (b *bridge) statePath() string {
	return filepath.Join(b.cfg.StateDir, "state.json")
}

func (b *bridge) replayDirectory() string {
	return filepath.Join(b.cfg.StateDir, "replays")
}

func (b *bridge) replayPath(matchID string) string {
	return filepath.Join(b.replayDirectory(), matchID+".m32r")
}
