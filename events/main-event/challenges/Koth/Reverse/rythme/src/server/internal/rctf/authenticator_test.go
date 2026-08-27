package rctf

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestTeamTokenAuthentication(t *testing.T) {
	var requestBody struct {
		Token       string `json:"token"`
		GameID      int    `json:"gameId"`
		ChallengeID int    `json:"challengeId"`
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/koth/capability/authenticate" {
			http.NotFound(w, r)
			return
		}
		if r.Method != http.MethodPost {
			t.Fatalf("unexpected authentication method %s", r.Method)
		}
		if err := json.NewDecoder(r.Body).Decode(&requestBody); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"teamId":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","teamName":"Tempo Crew","extra":true}`))
	}))
	defer upstream.Close()

	authenticator := TeamTokenAuthenticator{
		BaseURL:     upstream.URL,
		GameID:      7,
		ChallengeID: 9,
		Client:      &http.Client{Timeout: time.Second},
	}
	identity, err := authenticator.Authenticate(context.Background(), "koth_team-secret")
	if err != nil {
		t.Fatal(err)
	}
	if identity.ID != "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" || identity.Name != "Tempo Crew" {
		t.Fatalf("unexpected identity %+v", identity)
	}
	if requestBody.Token != "koth_team-secret" || requestBody.GameID != 7 || requestBody.ChallengeID != 9 {
		t.Fatalf("unexpected authentication scope %+v", requestBody)
	}
}

func TestTeamTokenAuthenticationRejectsBadLogin(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "bad token", http.StatusUnauthorized)
	}))
	defer upstream.Close()

	authenticator := TeamTokenAuthenticator{BaseURL: upstream.URL, GameID: 7, ChallengeID: 9}
	_, err := authenticator.Authenticate(context.Background(), "koth_wrong-token")
	if err != ErrAuthenticationRejected {
		t.Fatalf("expected authentication rejection, got %v", err)
	}
}

func TestTeamTokenAuthenticationRejectsMalformedTokenLocally(t *testing.T) {
	called := false
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		called = true
	}))
	defer upstream.Close()

	authenticator := TeamTokenAuthenticator{BaseURL: upstream.URL, GameID: 7, ChallengeID: 9}
	for _, token := range []string{"", "wrong", "koth_short", "koth_invalid.token"} {
		if _, err := authenticator.Authenticate(context.Background(), token); err != ErrAuthenticationRejected {
			t.Fatalf("expected %q to be rejected locally, got %v", token, err)
		}
	}
	if called {
		t.Fatal("malformed tokens must not reach RSCTF")
	}
}
