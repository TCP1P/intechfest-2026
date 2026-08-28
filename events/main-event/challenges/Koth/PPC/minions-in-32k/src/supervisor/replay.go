package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"time"
)

const (
	maxReplayBytes = 128 * 1024 * 1024
	maxReplayFiles = 24
)

func (a *arena) acceptReplay(w http.ResponseWriter, request *http.Request) {
	if a.reporter == nil {
		problem(w, http.StatusServiceUnavailable, "managed scoring is not active")
		return
	}
	a.reporter.acceptReplay(w, request)
}

func (r *managedReporter) acceptReplay(w http.ResponseWriter, request *http.Request) {
	provided, ok := bearerToken(request.Header.Get("Authorization"))
	if !ok || !sameSecret(provided, r.reportToken) {
		problem(w, http.StatusUnauthorized, "invalid replay reporter")
		return
	}
	values := request.URL.Query()
	matchIDs, found := values["match_id"]
	if !found || len(values) != 1 || len(matchIDs) != 1 || !validMatchID(matchIDs[0]) {
		problem(w, http.StatusBadRequest, "invalid replay match identity")
		return
	}
	if request.ContentLength == 0 || request.ContentLength > maxReplayBytes {
		problem(w, http.StatusRequestEntityTooLarge, "replay exceeds the size limit")
		return
	}
	temporary, err := os.CreateTemp(replayPath, ".upload-*.tmp")
	if err != nil {
		problem(w, http.StatusServiceUnavailable, "replay storage unavailable")
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
	limited := &io.LimitedReader{R: request.Body, N: maxReplayBytes + 1}
	size, copyErr := io.Copy(io.MultiWriter(temporary, hash), limited)
	if copyErr != nil || size == 0 || size > maxReplayBytes || temporary.Sync() != nil || temporary.Close() != nil {
		problem(w, http.StatusBadRequest, "replay upload was interrupted")
		return
	}
	digest := hex.EncodeToString(hash.Sum(nil))
	matchID := matchIDs[0]
	artifact := replayArtifact{SHA256: digest, Size: size, ReceivedAt: time.Now().UTC()}
	finalPath := filepath.Join(replayPath, matchID+".m32r")

	r.mu.Lock()
	if existing, exists := r.replays[matchID]; exists && existing.SHA256 != digest {
		r.mu.Unlock()
		problem(w, http.StatusConflict, "match replay was already retained with different evidence")
		return
	}
	if err := os.Rename(temporaryPath, finalPath); err != nil {
		r.mu.Unlock()
		problem(w, http.StatusServiceUnavailable, "replay storage unavailable")
		return
	}
	committed = true
	r.replays[matchID] = artifact
	r.purgeReplaysLocked()
	r.mu.Unlock()
	log.Printf("managed replay retained id=%q bytes=%d sha256=%s", matchID, size, digest)
	w.Header().Set("X-Content-SHA256", digest)
	w.WriteHeader(http.StatusNoContent)
}

func (r *managedReporter) purgeReplaysLocked() {
	type retained struct {
		matchID string
		at      time.Time
	}
	ordered := make([]retained, 0, len(r.replays))
	for matchID, artifact := range r.replays {
		if !canonicalHash(artifact.SHA256) || artifact.Size <= 0 || artifact.Size > maxReplayBytes {
			delete(r.replays, matchID)
			_ = os.Remove(filepath.Join(replayPath, fmt.Sprintf("%s.m32r", matchID)))
			continue
		}
		ordered = append(ordered, retained{matchID: matchID, at: artifact.ReceivedAt})
	}
	sort.Slice(ordered, func(left, right int) bool { return ordered[left].at.After(ordered[right].at) })
	for _, old := range ordered[min(len(ordered), maxReplayFiles):] {
		delete(r.replays, old.matchID)
		_ = os.Remove(filepath.Join(replayPath, old.matchID+".m32r"))
	}
}
