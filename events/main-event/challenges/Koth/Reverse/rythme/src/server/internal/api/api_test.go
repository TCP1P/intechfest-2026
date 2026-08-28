package api

import (
	"bytes"
	"crypto/ecdh"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"rythme.local/server/internal/arena"
	"rythme.local/server/internal/rctf"
)

func newAPITestArena(t *testing.T) *arena.Arena {
	t.Helper()
	return newAPITestArenaFromPool(t, loadAPITestPool(t))
}

func loadAPITestPool(t *testing.T) arena.Pool {
	t.Helper()
	poolRaw, err := os.ReadFile(filepath.Join("..", "..", "..", "shared", "pool.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pool arena.Pool
	if err := json.Unmarshal(poolRaw, &pool); err != nil {
		t.Fatal(err)
	}
	return pool
}

func newAPITestArenaFromPool(t *testing.T, pool arena.Pool) *arena.Arena {
	t.Helper()
	game, err := arena.New(arena.Config{
		EnrollmentDuration: time.Second,
		ReadyDuration:      time.Second,
		SubmissionDuration: time.Second,
		ResultsDuration:    time.Second,
		CrownThreshold:     2_100_000,
		RawScoreFloor:      700_000,
		Seed:               1,
	}, pool)
	if err != nil {
		t.Fatal(err)
	}
	return game
}

func newAPITestArenaWithArchive(t *testing.T, raw []byte) (*arena.Arena, string) {
	t.Helper()
	pool := loadAPITestPool(t)
	digest := fmt.Sprintf("%x", sha256.Sum256(raw))
	for index := range pool.Songs {
		pool.Songs[index].ArchiveSHA256 = digest
		pool.Songs[index].SourceURL = "https://beatmaps.invalid/" + pool.Songs[index].ID
	}
	return newAPITestArenaFromPool(t, pool), pool.Songs[0].ID
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestLegacyCriticalRoutesAreNotExposed(t *testing.T) {
	handler := (&Server{Arena: newAPITestArena(t)}).Handler()
	for _, path := range []string{
		"/api/v2/heats/current/enroll",
		"/api/v2/heats/current/ready",
		"/api/v2/heats/current/cues",
		"/api/v2/heats/current/input",
		"/api/v2/heats/current/result",
		"/api/v2/heats/current/manifest",
	} {
		t.Run(path, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, path, http.NoBody)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusNotFound {
				t.Fatalf("legacy route returned %d", response.Code)
			}
		})
	}
	request := httptest.NewRequest(http.MethodGet, "/api/v1/scoring", http.NoBody)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNotFound {
		t.Fatalf("legacy scoring feed returned %d", response.Code)
	}
}

func TestBeatmapProxyStaysOnCoordinatorAndCachesVerifiedArchive(t *testing.T) {
	raw := []byte("PK\x03\x04verified beatmap fixture")
	game, songID := newAPITestArenaWithArchive(t, raw)
	cacheDir := t.TempDir()
	requests := 0
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		if request.URL.Scheme != "https" || request.URL.Host != "beatmaps.invalid" {
			t.Fatalf("unexpected upstream URL %s", request.URL)
		}
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       io.NopCloser(bytes.NewReader(raw)),
			Request:    request,
		}, nil
	})}
	handler := (&Server{
		Arena:         game,
		BeatmapDir:    cacheDir,
		BeatmapProxy:  true,
		BeatmapClient: client,
	}).Handler()

	for attempt := 0; attempt < 2; attempt++ {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/beatmaps/"+songID+".osz", http.NoBody)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("attempt %d returned %d: %s", attempt, response.Code, response.Body.String())
		}
		if response.Header().Get("Location") != "" {
			t.Fatalf("attempt %d leaked an upstream redirect", attempt)
		}
		if !bytes.Equal(response.Body.Bytes(), raw) {
			t.Fatalf("attempt %d returned the wrong archive", attempt)
		}
	}
	if requests != 1 {
		t.Fatalf("upstream received %d requests, want one", requests)
	}
	cached, err := os.ReadFile(filepath.Join(cacheDir, songID+".osz"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(cached, raw) {
		t.Fatal("cached archive differs from the verified upstream response")
	}
}

func TestEveryPinnedBeatmapCanBePredownloadedDuringEnrollment(t *testing.T) {
	pool := loadAPITestPool(t)
	cacheDir := t.TempDir()
	archives := make(map[string][]byte, len(pool.Songs))
	for index := range pool.Songs {
		raw := []byte(fmt.Sprintf("PK\x03\x04pinned archive %d", index))
		pool.Songs[index].ArchiveSHA256 = fmt.Sprintf("%x", sha256.Sum256(raw))
		archives[pool.Songs[index].ID] = raw
		if err := os.WriteFile(filepath.Join(cacheDir, pool.Songs[index].ID+".osz"), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	game := newAPITestArenaFromPool(t, pool)
	initial := game.View(time.Now().UTC())
	if initial.Heat.Phase != arena.PhaseEnrollment || initial.Heat.Selection != nil {
		t.Fatalf("test must begin before the selection reveal: phase=%s selection=%+v", initial.Heat.Phase, initial.Heat.Selection)
	}

	handler := (&Server{Arena: game, BeatmapDir: cacheDir}).Handler()
	for _, song := range pool.Songs {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/beatmaps/"+song.ID+".osz", http.NoBody)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusOK {
			t.Fatalf("predownload %s returned %d: %s", song.ID, response.Code, response.Body.String())
		}
		if !bytes.Equal(response.Body.Bytes(), archives[song.ID]) {
			t.Fatalf("predownload %s returned the wrong archive", song.ID)
		}
	}
}

func TestBeatmapProxyRejectsUnpinnedArchiveWithoutRedirect(t *testing.T) {
	game, songID := newAPITestArenaWithArchive(t, []byte("expected archive"))
	cacheDir := t.TempDir()
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       io.NopCloser(strings.NewReader("tampered archive")),
			Request:    request,
		}, nil
	})}
	handler := (&Server{
		Arena:         game,
		BeatmapDir:    cacheDir,
		BeatmapProxy:  true,
		BeatmapClient: client,
	}).Handler()

	request := httptest.NewRequest(http.MethodGet, "/api/v1/beatmaps/"+songID+".osz", http.NoBody)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("tampered upstream returned %d: %s", response.Code, response.Body.String())
	}
	if response.Header().Get("Location") != "" {
		t.Fatal("tampered upstream response exposed a redirect")
	}
	if _, err := os.Stat(filepath.Join(cacheDir, songID+".osz")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("tampered archive reached the cache: %v", err)
	}
}

func TestBeatmapCacheMissNeverRedirectsWhenProxyDisabled(t *testing.T) {
	game := newAPITestArena(t)
	pool := game.Pool()
	songID := pool.Songs[0].ID
	handler := (&Server{Arena: game, BeatmapDir: t.TempDir()}).Handler()

	request := httptest.NewRequest(http.MethodGet, "/api/v1/beatmaps/"+songID+".osz", http.NoBody)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("cache miss returned %d: %s", response.Code, response.Body.String())
	}
	if response.Header().Get("Location") != "" {
		t.Fatal("cache miss exposed the pinned third-party source")
	}
}

func TestArenaFrameRejectsBadMagicOnAuthenticatedChannel(t *testing.T) {
	game := newAPITestArena(t)
	handler := (&Server{Arena: game}).Handler()
	session, err := game.CreateLocalSession("frame-team", "Frame Crew")
	if err != nil {
		t.Fatal(err)
	}

	body := []byte("not-an-arena-frame")
	now := time.Now().UTC()
	previous := strings.Repeat("0", sha256.Size*2)
	canonical := arena.CanonicalChannelRequest(
		session.ChannelID,
		http.MethodPost,
		"/api/v3/frame",
		1,
		now.UnixMilli(),
		body,
		previous,
	)
	mac := hmac.New(sha256.New, session.RequestKey)
	_, _ = mac.Write([]byte(canonical))

	request := httptest.NewRequest(http.MethodPost, "/api/v3/frame", bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+session.Token)
	request.Header.Set("X-Rythme-Channel", session.ChannelID)
	request.Header.Set("X-Rythme-Sequence", "1")
	request.Header.Set("X-Rythme-Timestamp", strconv.FormatInt(now.UnixMilli(), 10))
	request.Header.Set("X-Rythme-Previous", previous)
	request.Header.Set("X-Rythme-Signature", hex.EncodeToString(mac.Sum(nil)))
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("bad frame returned %d: %s", response.Code, response.Body.String())
	}
	if response.Header().Get("X-Rythme-Ack") != "1" ||
		response.Header().Get("X-Rythme-Response-Signature") == "" {
		t.Fatal("authenticated frame error was not bound to the channel")
	}

	replayRequest := httptest.NewRequest(http.MethodPost, "/api/v3/frame", bytes.NewReader(body))
	replayRequest.Header = request.Header.Clone()
	replay := httptest.NewRecorder()
	handler.ServeHTTP(replay, replayRequest)
	if replay.Code != response.Code || replay.Body.String() != response.Body.String() {
		t.Fatalf("authenticated error replay changed: %d %q", replay.Code, replay.Body.String())
	}
	if replay.Header().Get("X-Rythme-Ack") != response.Header().Get("X-Rythme-Ack") ||
		replay.Header().Get("X-Rythme-Response-Signature") != response.Header().Get("X-Rythme-Response-Signature") {
		t.Fatal("authenticated error replay changed its secure binding")
	}
}

func TestRCTFSessionUsesAuthoritativeIdentity(t *testing.T) {
	rctfServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path != "/api/v1/koth/capability/authenticate" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"teamId":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","teamName":"Authoritative Crew"}`))
	}))
	defer rctfServer.Close()

	poolRaw, err := os.ReadFile(filepath.Join("..", "..", "..", "shared", "pool.json"))
	if err != nil {
		t.Fatal(err)
	}
	var pool arena.Pool
	if err := json.Unmarshal(poolRaw, &pool); err != nil {
		t.Fatal(err)
	}
	game, err := arena.New(arena.Config{
		EnrollmentDuration: time.Second,
		ReadyDuration:      time.Second,
		SubmissionDuration: time.Second,
		ResultsDuration:    time.Second,
		CrownThreshold:     2_100_000,
		RawScoreFloor:      700_000,
		Seed:               1,
	}, pool)
	if err != nil {
		t.Fatal(err)
	}

	handler := (&Server{
		Arena: game,
		RCTFAuth: &rctf.TeamTokenAuthenticator{
			BaseURL:     rctfServer.URL,
			GameID:      7,
			ChallengeID: 9,
			Client:      rctfServer.Client(),
		},
	}).Handler()
	clientPrivate, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	clientPublic, err := x509.MarshalPKIXPublicKey(clientPrivate.PublicKey())
	if err != nil {
		t.Fatal(err)
	}
	clientNonce := make([]byte, 32)
	if _, err := rand.Read(clientNonce); err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(map[string]string{
		"team_token":        "koth_player-team-token",
		"client_public_key": base64.StdEncoding.EncodeToString(clientPublic),
		"client_nonce":      base64.StdEncoding.EncodeToString(clientNonce),
	})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v2/session/rctf",
		bytes.NewReader(payload),
	)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("unexpected status %d: %s", response.Code, response.Body.String())
	}
	var session struct {
		AccessToken     string `json:"access_token"`
		TeamID          string `json:"team_id"`
		TeamName        string `json:"team_name"`
		ChannelID       string `json:"channel_id"`
		ServerPublicKey string `json:"server_public_key"`
		ServerProof     string `json:"server_proof"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &session); err != nil {
		t.Fatal(err)
	}
	if session.AccessToken == "" ||
		session.TeamID != "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" ||
		session.TeamName != "Authoritative Crew" ||
		session.ChannelID == "" ||
		session.ServerPublicKey == "" ||
		session.ServerProof == "" {
		t.Fatalf("unexpected session %+v", session)
	}
}
