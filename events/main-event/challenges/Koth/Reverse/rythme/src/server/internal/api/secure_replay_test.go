package api

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"rythme.local/server/internal/arena"
)

func TestSecureTransportReplaysLostResponseWithoutRepeatingHandler(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("replay-team", "Replay Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"accepted":1}`))
	}))
	body := []byte(`{"frames":[1]}`)
	now := time.Now().UTC()

	first := httptest.NewRecorder()
	handler.ServeHTTP(first, newSecureTestRequest(session, now, "/secure-test", body))
	second := httptest.NewRecorder()
	handler.ServeHTTP(second, newSecureTestRequest(session, now, "/secure-test", body))

	if calls.Load() != 1 {
		t.Fatalf("secure handler ran %d times for an exact replay", calls.Load())
	}
	assertSecureResponsesEqual(t, first, second)

	tampered := httptest.NewRecorder()
	request := newSecureTestRequest(session, now, "/secure-test", body)
	request.Body = io.NopCloser(bytes.NewReader([]byte(`{"frames":[2]}`)))
	handler.ServeHTTP(tampered, request)
	if tampered.Code != http.StatusConflict {
		t.Fatalf("tampered replay returned %d: %s", tampered.Code, tampered.Body.String())
	}
	if calls.Load() != 1 {
		t.Fatal("tampered replay reached the protected handler")
	}
}

func TestEnrollmentResponseReplayDoesNotDuplicateThePerformer(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("enrollment-replay", "Enrollment Replay Crew")
	if err != nil {
		t.Fatal(err)
	}
	handler := (&Server{Arena: game}).Handler()
	body := []byte{0x29, 0x9e, 0xd9, 0x46, 0x01, 0x5c}
	now := time.Now().UTC()

	first := httptest.NewRecorder()
	handler.ServeHTTP(first, newSecureTestRequest(session, now, "/api/v3/frame", body))
	if first.Code != http.StatusOK {
		t.Fatalf("initial enrollment returned %d: %x", first.Code, first.Body.Bytes())
	}
	replay := httptest.NewRecorder()
	handler.ServeHTTP(replay, newSecureTestRequest(session, now, "/api/v3/frame", body))
	assertSecureResponsesEqual(t, first, replay)

	performers := game.View(now).Heat.Performers
	if len(performers) != 1 || performers[0].TeamID != session.TeamID {
		t.Fatalf("enrollment replay produced unexpected performers: %+v", performers)
	}
}

func TestSecureTransportCoalescesConcurrentReplay(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("concurrent-team", "Concurrent Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	entered := make(chan struct{})
	release := make(chan struct{})
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		close(entered)
		<-release
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = w.Write([]byte("bound response"))
	}))
	body := []byte("request")
	now := time.Now().UTC()

	first := httptest.NewRecorder()
	firstDone := make(chan struct{})
	go func() {
		handler.ServeHTTP(first, newSecureTestRequest(session, now, "/secure-test", body))
		close(firstDone)
	}()
	<-entered

	second := httptest.NewRecorder()
	secondDone := make(chan struct{})
	go func() {
		handler.ServeHTTP(second, newSecureTestRequest(session, now, "/secure-test", body))
		close(secondDone)
	}()

	select {
	case <-secondDone:
		t.Fatal("duplicate returned before the original response was available")
	case <-time.After(25 * time.Millisecond):
	}
	close(release)
	<-firstDone
	<-secondDone

	if calls.Load() != 1 {
		t.Fatalf("concurrent replay ran the handler %d times", calls.Load())
	}
	assertSecureResponsesEqual(t, first, second)
}

func TestSecureTransportCompletesReplayWhenProtectedHandlerPanics(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("panic-team", "Panic Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if calls.Add(1) == 1 {
			panic("fixture failure")
		}
		_, _ = w.Write([]byte("next response"))
	}))
	now := time.Now().UTC()
	firstRequest := newSecureTestRequest(session, now, "/secure-test", []byte("first"))
	firstMAC := firstRequest.Header.Get("X-Rythme-Signature")

	first := httptest.NewRecorder()
	handler.ServeHTTP(first, firstRequest)
	if first.Code != http.StatusInternalServerError {
		t.Fatalf("panicking handler returned %d: %s", first.Code, first.Body.String())
	}
	replay := httptest.NewRecorder()
	handler.ServeHTTP(replay, newSecureTestRequest(session, now, "/secure-test", []byte("first")))
	assertSecureResponsesEqual(t, first, replay)
	if calls.Load() != 1 {
		t.Fatalf("panic replay reran the handler %d times", calls.Load())
	}

	next := httptest.NewRecorder()
	handler.ServeHTTP(next, newSecureTestRequestAt(
		session,
		2,
		now.Add(time.Millisecond),
		firstMAC,
		"/secure-test",
		[]byte("second"),
	))
	if next.Code != http.StatusOK || next.Body.String() != "next response" {
		t.Fatalf("request after panic recovery returned %d: %q", next.Code, next.Body.String())
	}
}

func TestSecureTransportAcceptsTheNextRequestAfterAReplay(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("ratchet-team", "Ratchet Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = io.Copy(w, r.Body)
	}))
	now := time.Now().UTC()
	firstRequest := newSecureTestRequest(session, now, "/secure-test", []byte("first"))
	firstMAC := firstRequest.Header.Get("X-Rythme-Signature")

	first := httptest.NewRecorder()
	handler.ServeHTTP(first, firstRequest)
	replay := httptest.NewRecorder()
	handler.ServeHTTP(replay, newSecureTestRequest(session, now, "/secure-test", []byte("first")))
	assertSecureResponsesEqual(t, first, replay)

	secondRequest := newSecureTestRequestAt(
		session,
		2,
		now.Add(time.Millisecond),
		firstMAC,
		"/secure-test",
		[]byte("second"),
	)
	second := httptest.NewRecorder()
	handler.ServeHTTP(second, secondRequest)
	if second.Code != http.StatusOK || second.Body.String() != "second" {
		t.Fatalf("next ratchet request returned %d: %q", second.Code, second.Body.String())
	}
	if calls.Load() != 2 {
		t.Fatalf("handler ran %d times, want one call per sequence", calls.Load())
	}
}

func TestSecureTransportDoesNotReplayOutsideRecoveryWindow(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("stale-team", "Stale Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte("response"))
	}))
	body := []byte("request")
	now := time.Now().UTC()
	first := httptest.NewRecorder()
	handler.ServeHTTP(first, newSecureTestRequest(session, now, "/secure-test", body))

	shard := server.secureReplays.shard(session.Token)
	shard.mu.Lock()
	shard.entries[session.Token].createdAt = time.Now().Add(-secureReplayWindow - time.Second)
	shard.mu.Unlock()

	stale := httptest.NewRecorder()
	handler.ServeHTTP(stale, newSecureTestRequest(session, now, "/secure-test", body))
	if stale.Code != http.StatusConflict {
		t.Fatalf("stale replay returned %d: %s", stale.Code, stale.Body.String())
	}
	if calls.Load() != 1 {
		t.Fatal("stale replay reached the protected handler")
	}
}

func TestSecureReplayPruningIsThrottledAndRemovesCompletedEntries(t *testing.T) {
	now := time.Now()
	done := make(chan struct{})
	close(done)
	shard := secureReplayShard{
		entries: map[string]*secureReplay{
			"expired": {
				createdAt: now.Add(-secureReplayRetention - time.Second),
				done:      done,
			},
		},
		lastPruned: now,
	}

	shard.prune(now.Add(secureReplayPruneEvery - time.Millisecond))
	if shard.entries["expired"] == nil {
		t.Fatal("replay cache performed an eager full-shard scan")
	}
	shard.prune(now.Add(secureReplayPruneEvery))
	if shard.entries["expired"] != nil {
		t.Fatal("replay cache retained a completed expired entry")
	}
}

func TestSecureTransportCancelledWaiterDoesNotPoisonReplay(t *testing.T) {
	game := newAPITestArena(t)
	session, err := game.CreateLocalSession("cancel-team", "Cancel Crew")
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	entered := make(chan struct{})
	release := make(chan struct{})
	server := &Server{Arena: game}
	handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		close(entered)
		<-release
		_, _ = w.Write([]byte("cached"))
	}))
	body := []byte("request")
	now := time.Now().UTC()

	first := httptest.NewRecorder()
	firstDone := make(chan struct{})
	go func() {
		handler.ServeHTTP(first, newSecureTestRequest(session, now, "/secure-test", body))
		close(firstDone)
	}()
	<-entered

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	cancelledRequest := newSecureTestRequest(session, now, "/secure-test", body).WithContext(ctx)
	cancelled := httptest.NewRecorder()
	handler.ServeHTTP(cancelled, cancelledRequest)
	close(release)
	<-firstDone

	recovered := httptest.NewRecorder()
	handler.ServeHTTP(recovered, newSecureTestRequest(session, now, "/secure-test", body))
	assertSecureResponsesEqual(t, first, recovered)
	if calls.Load() != 1 {
		t.Fatalf("cancelled duplicate caused %d protected handler calls", calls.Load())
	}
}

func TestSecureTransportRejectsNearDuplicateRequests(t *testing.T) {
	mutations := map[string]func(*http.Request){
		"body": func(request *http.Request) {
			request.Body = io.NopCloser(bytes.NewReader([]byte("changed")))
		},
		"method": func(request *http.Request) { request.Method = http.MethodPut },
		"target": func(request *http.Request) { request.URL.Path = "/other" },
		"timestamp": func(request *http.Request) {
			request.Header.Set("X-Rythme-Timestamp", strconv.FormatInt(time.Now().Add(time.Second).UnixMilli(), 10))
		},
		"previous":  func(request *http.Request) { request.Header.Set("X-Rythme-Previous", strings.Repeat("f", 64)) },
		"channel":   func(request *http.Request) { request.Header.Set("X-Rythme-Channel", "other-channel") },
		"signature": func(request *http.Request) { request.Header.Set("X-Rythme-Signature", strings.Repeat("f", 64)) },
		"token":     func(request *http.Request) { request.Header.Set("Authorization", "Bearer other-token") },
	}

	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			game := newAPITestArena(t)
			session, err := game.CreateLocalSession("identity-team", "Identity Crew")
			if err != nil {
				t.Fatal(err)
			}
			var calls atomic.Int32
			server := &Server{Arena: game}
			handler := server.secure(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				calls.Add(1)
				_, _ = w.Write([]byte("response"))
			}))
			now := time.Now().UTC()
			body := []byte("request")
			first := httptest.NewRecorder()
			handler.ServeHTTP(first, newSecureTestRequest(session, now, "/secure-test", body))

			nearDuplicate := newSecureTestRequest(session, now, "/secure-test", body)
			mutate(nearDuplicate)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, nearDuplicate)
			if response.Code < 400 {
				t.Fatalf("near-duplicate %s was accepted with %d", name, response.Code)
			}
			if calls.Load() != 1 {
				t.Fatalf("near-duplicate %s reached the handler", name)
			}
		})
	}
}

func newSecureTestRequest(
	session arena.Session,
	now time.Time,
	target string,
	body []byte,
) *http.Request {
	return newSecureTestRequestAt(
		session,
		1,
		now,
		strings.Repeat("0", sha256.Size*2),
		target,
		body,
	)
}

func newSecureTestRequestAt(
	session arena.Session,
	sequence uint64,
	now time.Time,
	previous string,
	target string,
	body []byte,
) *http.Request {
	canonical := arena.CanonicalChannelRequest(
		session.ChannelID,
		http.MethodPost,
		target,
		sequence,
		now.UnixMilli(),
		body,
		previous,
	)
	mac := hmac.New(sha256.New, session.RequestKey)
	_, _ = mac.Write([]byte(canonical))

	request := httptest.NewRequest(http.MethodPost, target, bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+session.Token)
	request.Header.Set("X-Rythme-Channel", session.ChannelID)
	request.Header.Set("X-Rythme-Sequence", strconv.FormatUint(sequence, 10))
	request.Header.Set("X-Rythme-Timestamp", strconv.FormatInt(now.UnixMilli(), 10))
	request.Header.Set("X-Rythme-Previous", previous)
	request.Header.Set("X-Rythme-Signature", hex.EncodeToString(mac.Sum(nil)))
	return request
}

func assertSecureResponsesEqual(t *testing.T, first, second *httptest.ResponseRecorder) {
	t.Helper()
	if first.Code != second.Code || !bytes.Equal(first.Body.Bytes(), second.Body.Bytes()) {
		t.Fatalf(
			"replayed response changed: first=(%d, %q) second=(%d, %q)",
			first.Code,
			first.Body.String(),
			second.Code,
			second.Body.String(),
		)
	}
	for _, name := range []string{"X-Rythme-Ack", "X-Rythme-Response-Signature"} {
		if first.Header().Get(name) == "" || first.Header().Get(name) != second.Header().Get(name) {
			t.Fatalf("replayed response changed %s", name)
		}
	}
}
