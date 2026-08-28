package reporter

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"rythme.local/server/internal/arena"
)

const (
	maxTeams        = 2_000
	maxWaves        = 64
	maxTeamWaveRows = 2_000
	maxScore        = int64(1_000_000_000_000)
	maxResponseBody = 256 << 10
	pollInterval    = time.Second
)

var objectiveIDs = []string{"official-score"}

var errRoundGap = errors.New("rsctf scoring context is not active")

type Config struct {
	GameID         int
	ChallengeID    int
	ContextURL     string
	ObservationURL string
	Secret         string
	Client         *http.Client
	Logger         *log.Logger
}

type Reporter struct {
	cfg   Config
	arena *arena.Arena

	mu            sync.Mutex
	lastContext   string
	lastDigest    [32]byte
	hasDigest     bool
	lastTimestamp int64
}

type reportingContext struct {
	APIVersion          string   `json:"apiVersion"`
	Context             string   `json:"context"`
	CycleNumber         int      `json:"cycleNumber"`
	ResetAttempt        int      `json:"resetAttempt"`
	RoundNumber         int      `json:"roundNumber"`
	CycleEndsAt         int64    `json:"cycleEndsAt"`
	WaveWindowStartsAt  int64    `json:"waveWindowStartsAt"`
	WaveWindowEndsAt    int64    `json:"waveWindowEndsAt"`
	EligibleTokenHashes []string `json:"eligibleTokenHashes"`
	ObjectiveIDs        []string `json:"objectiveIds"`
	ObjectiveSchemaHash *string  `json:"objectiveSchemaHash"`
	GeneratedAt         int64    `json:"generatedAt"`
}

type ratio struct {
	Earned   int64 `json:"earned"`
	Possible int64 `json:"possible"`
}

type teamEvidence struct {
	TokenHash  string  `json:"tokenHash"`
	Activity   ratio   `json:"activity"`
	Objectives []ratio `json:"objectives"`
	IsCrown    bool    `json:"isCrown"`
}

type waveEvidence struct {
	WaveID  string         `json:"waveId"`
	EndedAt int64          `json:"endedAtUnixMs"`
	Teams   []teamEvidence `json:"teams"`
}

type observation struct {
	Context      string         `json:"context"`
	ObjectiveIDs []string       `json:"objectiveIds"`
	Waves        []waveEvidence `json:"waves"`
}

type acceptedObservation struct {
	Accepted        bool `json:"accepted"`
	CycleNumber     int  `json:"cycleNumber"`
	ResetAttempt    int  `json:"resetAttempt"`
	RoundNumber     int  `json:"roundNumber"`
	SubmittedWaves  int  `json:"submittedWaves"`
	SubmittedTeams  int  `json:"submittedTeams"`
	RecognizedTeams int  `json:"recognizedTeams"`
}

func New(cfg Config, game *arena.Arena) (*Reporter, error) {
	if cfg.GameID <= 0 || cfg.ChallengeID <= 0 || game == nil {
		return nil, errors.New("managed reporter scope is invalid")
	}
	if !validEndpoint(cfg.ContextURL) || !validEndpoint(cfg.ObservationURL) {
		return nil, errors.New("managed reporter endpoint is invalid")
	}
	if !strings.HasPrefix(cfg.Secret, "koth_target_") || len(cfg.Secret) < 48 || len(cfg.Secret) > 128 || strings.ContainsAny(cfg.Secret, " \t\r\n") {
		return nil, errors.New("managed reporter credential is invalid")
	}
	if cfg.Client == nil {
		cfg.Client = &http.Client{Timeout: 6 * time.Second}
	}
	return &Reporter{cfg: cfg, arena: game}, nil
}

func validEndpoint(raw string) bool {
	parsed, err := url.Parse(raw)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") && parsed.Host != "" && parsed.User == nil && parsed.RawQuery == "" && parsed.Fragment == ""
}

func (r *Reporter) Run(ctx context.Context) {
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()
	for {
		if err := r.report(ctx); err != nil && !errors.Is(err, errRoundGap) && r.cfg.Logger != nil {
			r.cfg.Logger.Printf("managed KotH reporter waiting: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (r *Reporter) report(ctx context.Context) error {
	current, err := r.fetchContext(ctx)
	if err != nil {
		return err
	}
	waves := r.arena.ScoringWindow(
		time.UnixMilli(current.WaveWindowStartsAt).UTC(),
		time.UnixMilli(current.WaveWindowEndsAt).UTC(),
		time.Now().UTC(),
	)
	body, teamCount, err := buildObservation(current, waves)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(body)
	r.mu.Lock()
	unchanged := r.hasDigest && r.lastContext == current.Context && r.lastDigest == digest
	r.mu.Unlock()
	if unchanged {
		return nil
	}
	accepted, err := r.submit(ctx, body)
	if err != nil {
		return err
	}
	if !accepted.Accepted || accepted.CycleNumber != current.CycleNumber ||
		accepted.ResetAttempt != current.ResetAttempt || accepted.RoundNumber != current.RoundNumber ||
		accepted.SubmittedWaves != len(waves.Waves) || accepted.SubmittedTeams != teamCount ||
		accepted.RecognizedTeams != teamCount {
		return errors.New("rsctf did not acknowledge the complete Rythme snapshot")
	}
	r.mu.Lock()
	r.lastContext, r.lastDigest, r.hasDigest = current.Context, digest, true
	r.mu.Unlock()
	if r.cfg.Logger != nil {
		r.cfg.Logger.Printf("managed KotH snapshot accepted round=%d cycle=%d waves=%d teams=%d", accepted.RoundNumber, accepted.CycleNumber, accepted.SubmittedWaves, teamCount)
	}
	return nil
}

func (r *Reporter) fetchContext(ctx context.Context) (reportingContext, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, r.cfg.ContextURL, nil)
	if err != nil {
		return reportingContext{}, err
	}
	request.Header.Set("Accept", "application/json")
	response, err := r.cfg.Client.Do(request)
	if err != nil {
		return reportingContext{}, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusConflict {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, maxResponseBody+1))
		return reportingContext{}, errRoundGap
	}
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, maxResponseBody+1))
		return reportingContext{}, fmt.Errorf("rsctf context returned HTTP %d", response.StatusCode)
	}
	var value reportingContext
	if err := decodeBounded(response.Body, &value); err != nil {
		return reportingContext{}, fmt.Errorf("decode rsctf context: %w", err)
	}
	return value, nil
}

func (r *Reporter) submit(ctx context.Context, body []byte) (acceptedObservation, error) {
	r.mu.Lock()
	timestamp := time.Now().UnixMilli()
	if timestamp <= r.lastTimestamp {
		timestamp = r.lastTimestamp + 1
	}
	r.lastTimestamp = timestamp
	r.mu.Unlock()
	message := []byte(fmt.Sprintf("%d.%d.%d.", timestamp, r.cfg.GameID, r.cfg.ChallengeID))
	mac := hmac.New(sha256.New, []byte(r.cfg.Secret))
	_, _ = mac.Write(message)
	_, _ = mac.Write(body)

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, r.cfg.ObservationURL, bytes.NewReader(body))
	if err != nil {
		return acceptedObservation{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-RSCTF-Timestamp", strconv.FormatInt(timestamp, 10))
	request.Header.Set("X-RSCTF-Signature", "sha256="+hex.EncodeToString(mac.Sum(nil)))
	response, err := r.cfg.Client.Do(request)
	if err != nil {
		return acceptedObservation{}, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusConflict {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, maxResponseBody+1))
		return acceptedObservation{}, errRoundGap
	}
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, maxResponseBody+1))
		return acceptedObservation{}, fmt.Errorf("rsctf observation returned HTTP %d", response.StatusCode)
	}
	var accepted acceptedObservation
	if err := decodeBounded(response.Body, &accepted); err != nil {
		return acceptedObservation{}, fmt.Errorf("decode rsctf acknowledgement: %w", err)
	}
	return accepted, nil
}

func decodeBounded(reader io.Reader, destination any) error {
	limited := &io.LimitedReader{R: reader, N: maxResponseBody + 1}
	decoder := json.NewDecoder(limited)
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("response contains trailing JSON")
	}
	if limited.N == 0 {
		return errors.New("response exceeds the size limit")
	}
	return nil
}

func buildObservation(current reportingContext, scoring arena.ScoringWindowView) ([]byte, int, error) {
	if current.APIVersion != "v1" || !canonicalHash(current.Context) || current.CycleNumber <= 0 ||
		current.ResetAttempt <= 0 || current.RoundNumber <= 0 || current.CycleEndsAt <= 0 ||
		current.WaveWindowStartsAt <= 0 || current.WaveWindowEndsAt <= current.WaveWindowStartsAt ||
		current.CycleEndsAt < current.WaveWindowEndsAt || current.GeneratedAt <= 0 {
		return nil, 0, errors.New("rsctf context is invalid")
	}
	if len(current.EligibleTokenHashes) < 1 || len(current.EligibleTokenHashes) > maxTeams {
		return nil, 0, errors.New("rsctf eligible roster is invalid")
	}
	eligible := make(map[string]bool, len(current.EligibleTokenHashes))
	for _, token := range current.EligibleTokenHashes {
		if !canonicalHash(token) || eligible[token] {
			return nil, 0, errors.New("rsctf eligible roster is invalid")
		}
		eligible[token] = true
	}
	if len(current.ObjectiveIDs) != 0 && (len(current.ObjectiveIDs) != 1 || current.ObjectiveIDs[0] != objectiveIDs[0]) {
		return nil, 0, errors.New("Rythme objective schema changed")
	}
	if current.ObjectiveSchemaHash != nil && !canonicalHash(*current.ObjectiveSchemaHash) {
		return nil, 0, errors.New("Rythme objective schema hash is invalid")
	}
	if len(scoring.Waves) > maxWaves {
		return nil, 0, errors.New("Rythme scoring window has too many waves")
	}
	waves := make([]waveEvidence, 0, len(scoring.Waves))
	seenWaves := make(map[string]bool, len(scoring.Waves))
	allTeams := make(map[string]bool)
	teamWaveRows := 0
	for _, wave := range scoring.Waves {
		if wave.WaveID == "" || len(wave.WaveID) > 128 || seenWaves[wave.WaveID] ||
			wave.EndedAtUnixMS < current.WaveWindowStartsAt || wave.EndedAtUnixMS >= current.WaveWindowEndsAt {
			return nil, 0, errors.New("Rythme finalized wave is invalid")
		}
		seenWaves[wave.WaveID] = true
		results := make([]arena.ScoringResultView, 0, len(wave.Results))
		seenTeams := make(map[string]bool, len(wave.Results))
		for _, result := range wave.Results {
			if !eligible[result.TeamID] {
				continue
			}
			if seenTeams[result.TeamID] || result.Score < 0 || result.Score > maxScore {
				return nil, 0, errors.New("Rythme team wave result is invalid")
			}
			seenTeams[result.TeamID] = true
			results = append(results, result)
		}
		teamWaveRows += len(results)
		if teamWaveRows > maxTeamWaveRows {
			return nil, 0, errors.New("Rythme scoring window has too many team rows")
		}
		best := int64(0)
		for _, result := range results {
			if result.Score > best {
				best = result.Score
			}
		}
		leaders := []string{}
		for _, result := range results {
			if best > 0 && result.Score == best {
				leaders = append(leaders, result.TeamID)
			}
		}
		crownOwner := ""
		if len(leaders) == 1 {
			crownOwner = leaders[0]
		}
		possible := best
		if possible == 0 {
			possible = 1
		}
		teams := make([]teamEvidence, 0, len(results))
		for _, result := range results {
			teams = append(teams, teamEvidence{
				TokenHash:  result.TeamID,
				Activity:   ratio{Earned: 1, Possible: 1},
				Objectives: []ratio{{Earned: result.Score, Possible: possible}},
				IsCrown:    result.TeamID == crownOwner,
			})
			allTeams[result.TeamID] = true
		}
		sort.Slice(teams, func(left, right int) bool { return teams[left].TokenHash < teams[right].TokenHash })
		waves = append(waves, waveEvidence{WaveID: wave.WaveID, EndedAt: wave.EndedAtUnixMS, Teams: teams})
	}
	sort.Slice(waves, func(left, right int) bool {
		if waves[left].EndedAt == waves[right].EndedAt {
			return waves[left].WaveID < waves[right].WaveID
		}
		return waves[left].EndedAt < waves[right].EndedAt
	})
	body, err := json.Marshal(observation{Context: current.Context, ObjectiveIDs: objectiveIDs, Waves: waves})
	return body, len(allTeams), err
}

func canonicalHash(value string) bool {
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
