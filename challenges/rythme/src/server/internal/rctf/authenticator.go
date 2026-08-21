package rctf

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

var (
	ErrAuthenticationRejected = errors.New("RSCTF authentication rejected")
	ErrAuthenticationService  = errors.New("RSCTF authentication service unavailable")
)

type TeamIdentity struct {
	ID   string
	Name string
}

type TeamTokenAuthenticator struct {
	BaseURL     string
	GameID      int
	ChallengeID int
	Client      *http.Client
}

func (a *TeamTokenAuthenticator) Enabled() bool {
	return strings.TrimSpace(a.BaseURL) != "" && a.GameID > 0 && a.ChallengeID > 0
}

func (a *TeamTokenAuthenticator) Authenticate(ctx context.Context, teamToken string) (TeamIdentity, error) {
	teamToken = strings.TrimSpace(teamToken)
	if !validKothToken(teamToken) {
		return TeamIdentity{}, ErrAuthenticationRejected
	}
	if !a.Enabled() {
		return TeamIdentity{}, ErrAuthenticationService
	}

	loginBody, err := json.Marshal(struct {
		Token       string `json:"token"`
		GameID      int    `json:"gameId"`
		ChallengeID int    `json:"challengeId"`
	}{
		Token: teamToken, GameID: a.GameID, ChallengeID: a.ChallengeID,
	})
	if err != nil {
		return TeamIdentity{}, fmt.Errorf("%w: encode login request", ErrAuthenticationService)
	}
	loginRequest, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		strings.TrimRight(a.BaseURL, "/")+"/api/v1/koth/capability/authenticate",
		bytes.NewReader(loginBody),
	)
	if err != nil {
		return TeamIdentity{}, fmt.Errorf("%w: create login request", ErrAuthenticationService)
	}
	loginRequest.Header.Set("Content-Type", "application/json")

	var identityResponse struct {
		TeamID   string `json:"teamId"`
		TeamName string `json:"teamName"`
	}
	if err := a.do(loginRequest, &identityResponse); err != nil {
		return TeamIdentity{}, err
	}
	identity := TeamIdentity{
		ID:   strings.TrimSpace(identityResponse.TeamID),
		Name: strings.TrimSpace(identityResponse.TeamName),
	}
	if !validTeamPseudonym(identity.ID) || identity.Name == "" || len(identity.Name) > 256 {
		return TeamIdentity{}, ErrAuthenticationRejected
	}
	return identity, nil
}

func validKothToken(token string) bool {
	secret, ok := strings.CutPrefix(token, "koth_")
	if !ok || len(secret) < 8 || len(secret) > 128 {
		return false
	}
	for _, character := range secret {
		if (character < 'a' || character > 'z') &&
			(character < 'A' || character > 'Z') &&
			(character < '0' || character > '9') && character != '_' && character != '-' {
			return false
		}
	}
	return true
}

func validTeamPseudonym(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}

func (a *TeamTokenAuthenticator) do(request *http.Request, destination any) error {
	client := a.Client
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrAuthenticationService, err)
	}
	defer response.Body.Close()

	if response.StatusCode == http.StatusUnauthorized || response.StatusCode == http.StatusForbidden ||
		response.StatusCode == http.StatusBadRequest {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 64<<10))
		return ErrAuthenticationRejected
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 64<<10))
		return fmt.Errorf("%w: upstream returned HTTP %d", ErrAuthenticationService, response.StatusCode)
	}

	decoder := json.NewDecoder(io.LimitReader(response.Body, 64<<10))
	if err := decoder.Decode(destination); err != nil {
		return fmt.Errorf("%w: decode upstream response", ErrAuthenticationService)
	}
	return nil
}
