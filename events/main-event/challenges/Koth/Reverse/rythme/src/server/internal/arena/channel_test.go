package arena

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"testing"
	"time"
)

func TestSessionStorePrunesExpiredAndBoundsEachTeam(t *testing.T) {
	game := newTestArena(t, 501)
	now := time.Now().UTC()
	keys := make([]byte, channelKeySize)
	for index := 0; index < maxSessionsPerTeam+1; index++ {
		token := fmt.Sprintf("bounded-%d", index)
		if _, err := game.storeSession(token, "bounded-team", "Bounded Crew", "channel-"+token, keys, keys, keys, now.Add(time.Duration(index)*time.Millisecond), now.Add(channelLifetime)); err != nil {
			t.Fatal(err)
		}
	}

	game.mu.Lock()
	if count := game.teamSessionCountLocked("bounded-team"); count != maxSessionsPerTeam {
		game.mu.Unlock()
		t.Fatalf("team retained %d sessions, want %d", count, maxSessionsPerTeam)
	}
	if _, exists := game.sessions["bounded-0"]; exists {
		game.mu.Unlock()
		t.Fatal("oldest inactive team session was not evicted")
	}
	game.sessions["expired"] = Session{Token: "expired", TeamID: "old", IssuedAt: now.Add(-time.Hour), ExpiresAt: now.Add(-time.Second)}
	game.pruneSessionsLocked(now)
	_, expiredExists := game.sessions["expired"]
	game.mu.Unlock()
	if expiredExists {
		t.Fatal("expired session was not pruned")
	}
}

func TestSessionStorePreservesActiveHeatIdentity(t *testing.T) {
	game := newTestArena(t, 502)
	now := time.Now().UTC()
	keys := make([]byte, channelKeySize)
	var active Session
	for index := 0; index < maxSessionsPerTeam; index++ {
		token := fmt.Sprintf("active-%d", index)
		session, err := game.storeSession(token, "active-team", "Active Crew", "channel-"+token, keys, keys, keys, now.Add(time.Duration(index)*time.Millisecond), now.Add(channelLifetime))
		if err != nil {
			t.Fatal(err)
		}
		if index == 0 {
			active = session
		}
	}
	if _, err := game.Enroll(active.Token, now); err != nil {
		t.Fatal(err)
	}
	if _, err := game.storeSession("active-new", "active-team", "Active Crew", "channel-new", keys, keys, keys, now.Add(time.Second), now.Add(channelLifetime)); err != nil {
		t.Fatal(err)
	}

	game.mu.Lock()
	defer game.mu.Unlock()
	if _, exists := game.sessions[active.Token]; !exists {
		t.Fatal("active heat session was evicted")
	}
	if _, exists := game.sessions["active-1"]; exists {
		t.Fatal("oldest inactive session was retained")
	}
}

func TestSessionStoreKeepsGlobalCapacityBounded(t *testing.T) {
	game := newTestArena(t, 503)
	now := time.Now().UTC()
	keys := make([]byte, channelKeySize)
	game.mu.Lock()
	for index := 0; index < maxSessions; index++ {
		token := fmt.Sprintf("global-%04d", index)
		game.sessions[token] = Session{
			Token: token, TeamID: fmt.Sprintf("team-%04d", index), TeamName: "Crew", ChannelID: "channel-" + token,
			RequestKey: keys, ResponseKey: keys, StateKey: keys,
			IssuedAt: now.Add(time.Duration(index) * time.Millisecond), ExpiresAt: now.Add(channelLifetime),
		}
	}
	game.mu.Unlock()
	if _, err := game.storeSession("global-new", "new-team", "New Crew", "channel-new", keys, keys, keys, now.Add(time.Hour), now.Add(2*channelLifetime)); err != nil {
		t.Fatal(err)
	}

	game.mu.Lock()
	defer game.mu.Unlock()
	if len(game.sessions) != maxSessions {
		t.Fatalf("global session store contains %d entries, want %d", len(game.sessions), maxSessions)
	}
	if _, exists := game.sessions["global-0000"]; exists {
		t.Fatal("oldest global session was not evicted")
	}
}

func TestSecureChannelRejectsReplayAndBodyTampering(t *testing.T) {
	game := newTestArena(t, 2026)
	session, err := game.CreateLocalSession("channel-team", "Channel Crew")
	if err != nil {
		t.Fatal(err)
	}

	now := time.Now().UTC()
	body := []byte(`{"heat_ticket_id":"ticket"}`)
	first := signedChannelProof(session, 1, now, initialChannelMAC, "POST", "/api/v2/test", body)
	receipt, err := game.VerifyChannelRequest(
		session.Token,
		first,
		"POST",
		"/api/v2/test",
		body,
		now,
	)
	if err != nil {
		t.Fatalf("valid channel request failed: %v", err)
	}
	if receipt.Sequence != 1 || SignChannelResponse(receipt, 200, []byte(`{"ok":true}`)) == "" {
		t.Fatal("valid channel request did not produce a signed receipt")
	}

	if _, err := game.VerifyChannelRequest(
		session.Token,
		first,
		"POST",
		"/api/v2/test",
		body,
		now,
	); !errors.Is(err, ErrConflict) {
		t.Fatalf("replayed request returned %v", err)
	}

	second := signedChannelProof(
		session,
		2,
		now.Add(time.Millisecond),
		first.Signature,
		"POST",
		"/api/v2/test",
		body,
	)
	if _, err := game.VerifyChannelRequest(
		session.Token,
		second,
		"POST",
		"/api/v2/test",
		[]byte(`{"heat_ticket_id":"tampered"}`),
		now.Add(time.Millisecond),
	); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("body tampering returned %v", err)
	}
}

func signedChannelProof(
	session Session,
	sequence uint64,
	now time.Time,
	previous string,
	method string,
	target string,
	body []byte,
) ChannelRequestProof {
	canonical := CanonicalChannelRequest(
		session.ChannelID,
		method,
		target,
		sequence,
		now.UnixMilli(),
		body,
		previous,
	)
	mac := hmac.New(sha256.New, session.RequestKey)
	mac.Write([]byte(canonical))
	return ChannelRequestProof{
		ChannelID: session.ChannelID,
		Sequence:  sequence,
		SentAtMS:  now.UnixMilli(),
		Previous:  previous,
		Signature: hex.EncodeToString(mac.Sum(nil)),
	}
}
