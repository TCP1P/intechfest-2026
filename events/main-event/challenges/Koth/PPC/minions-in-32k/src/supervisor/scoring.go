package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"sort"
	"time"
)

const (
	maxReportBody    = 64 * 1024
	waveRetention    = 2 * time.Hour
	scoringLegLength = time.Minute
	scoringLegCount  = uint32(5)
	maxLegDeaths     = uint32(31)
	maxLegTakeovers  = uint32(31)
)

var objectiveIDs = []string{"official-score"}

type reportPlayer struct {
	TeamUUID        string `json:"team_uuid"`
	Name            string `json:"name"`
	Rank            uint32 `json:"rank"`
	Kills           uint32 `json:"kills"`
	ConfirmedKills  uint32 `json:"confirmed_kills"`
	Deaths          uint32 `json:"deaths"`
	Connected       bool   `json:"connected"`
	Takeovers       uint32 `json:"takeovers"`
	IntegrityStatus string `json:"integrity_status"`
}

type matchReport struct {
	MatchID       string         `json:"match_id"`
	Kind          string         `json:"kind"`
	Sequence      uint32         `json:"sequence"`
	LegCount      uint32         `json:"leg_count"`
	StartedUnixMS int64          `json:"started_unix_ms"`
	EndedUnixMS   int64          `json:"ended_unix_ms"`
	DurationMS    int64          `json:"duration_ms"`
	Players       []reportPlayer `json:"players"`
}

type matchSequence struct {
	LegCount    uint32
	LegKeys     map[uint32]string
	Final       *matchReport
	FinalDigest [32]byte
	LastEndedAt time.Time
}

type finalizedWave struct {
	ID         string
	EndedAt    time.Time
	Result     matchReport
	Identities map[string]string
	Digest     [32]byte
}

type replayArtifact struct {
	SHA256     string
	Size       int64
	ReceivedAt time.Time
}

type evidenceRatio struct {
	Earned   int64 `json:"earned"`
	Possible int64 `json:"possible"`
}

type teamEvidence struct {
	TokenHash  string          `json:"tokenHash"`
	Activity   evidenceRatio   `json:"activity"`
	Objectives []evidenceRatio `json:"objectives"`
	IsCrown    bool            `json:"isCrown"`
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

func (a *arena) acceptReport(w http.ResponseWriter, request *http.Request) {
	if a.reporter == nil {
		problem(w, http.StatusServiceUnavailable, "managed scoring is not active")
		return
	}
	a.reporter.acceptReport(w, request)
}

func (r *managedReporter) acceptReport(w http.ResponseWriter, request *http.Request) {
	provided, ok := bearerToken(request.Header.Get("Authorization"))
	if !ok || !sameSecret(provided, r.reportToken) {
		problem(w, http.StatusUnauthorized, "invalid match reporter")
		return
	}
	body, err := readLimited(w, request, maxReportBody)
	if err != nil {
		problem(w, http.StatusBadRequest, "invalid match report")
		return
	}
	var report matchReport
	if decodeExact(body, &report) != nil || r.validateReport(report, time.Now().UTC()) != nil {
		problem(w, http.StatusBadRequest, "invalid match report")
		return
	}
	digest := sha256.Sum256(body)
	r.mu.Lock()
	idempotent, err := r.storeReportLocked(report, digest, time.Now().UTC())
	r.mu.Unlock()
	if err != nil {
		problem(w, http.StatusConflict, err.Error())
		return
	}
	if !idempotent {
		log.Printf("managed match report accepted id=%q kind=%s sequence=%d", report.MatchID, report.Kind, report.Sequence)
	}
	w.WriteHeader(http.StatusNoContent)
}

func (r *managedReporter) storeReportLocked(report matchReport, digest [32]byte, now time.Time) (bool, error) {
	state := r.matches[report.MatchID]
	if report.Kind == "leg" {
		if state == nil {
			if report.Sequence != 1 {
				return false, errors.New("match reports must arrive in sequence")
			}
			state = &matchSequence{LegCount: report.LegCount, LegKeys: make(map[uint32]string)}
			r.matches[report.MatchID] = state
		}
		if state.LegCount != report.LegCount || state.Final != nil {
			return false, errors.New("match report sequence was already finalized")
		}
		if key, exists := state.LegKeys[report.Sequence]; exists {
			if r.waves[key].Digest != digest {
				return false, errors.New("match leg was already finalized with different evidence")
			}
			return true, nil
		}
		if report.Sequence != uint32(len(state.LegKeys))+1 {
			return false, errors.New("match reports must arrive in sequence")
		}
		if report.Sequence > 1 {
			previous := r.waves[state.LegKeys[report.Sequence-1]].Result
			if previous.EndedUnixMS != report.StartedUnixMS {
				return false, errors.New("match leg windows must be contiguous")
			}
		}
		if err := r.validateLegPrefixLocked(state, report); err != nil {
			return false, err
		}
		roster, ok := r.arena.rosterSnapshot()
		if !ok {
			return false, errors.New("arena roster is unavailable")
		}
		identities := make(map[string]string, len(roster.tokenByName))
		for name, tokenHash := range roster.tokenByName {
			identities[name] = tokenHash
		}
		idHash := sha256.Sum256([]byte(report.MatchID))
		waveID := fmt.Sprintf("m32-%s-l%02d", hex.EncodeToString(idHash[:8]), report.Sequence)
		key := fmt.Sprintf("%s:%d", report.MatchID, report.Sequence)
		endedAt := time.UnixMilli(report.EndedUnixMS).UTC()
		r.waves[key] = finalizedWave{
			ID: waveID, EndedAt: endedAt, Result: report, Identities: identities, Digest: digest,
		}
		state.LegKeys[report.Sequence] = key
		state.LastEndedAt = endedAt
	} else {
		if state == nil || uint32(len(state.LegKeys)) != report.LegCount {
			return false, errors.New("final report requires every scoring leg")
		}
		if state.Final != nil {
			if state.FinalDigest != digest {
				return false, errors.New("match reconciliation was already finalized with different evidence")
			}
			return true, nil
		}
		if err := r.validateReconciliationLocked(state, report); err != nil {
			return false, err
		}
		copy := report
		state.Final = &copy
		state.FinalDigest = digest
		state.LastEndedAt = time.UnixMilli(report.EndedUnixMS).UTC()
	}
	r.purgeExpiredLocked(now)
	return false, nil
}

func (r *managedReporter) validateReconciliationLocked(state *matchSequence, report matchReport) error {
	if _, retained := r.replays[report.MatchID]; !retained {
		return errors.New("final report requires retained replay evidence")
	}
	first := r.waves[state.LegKeys[1]].Result
	last := r.waves[state.LegKeys[state.LegCount]].Result
	if report.StartedUnixMS != first.StartedUnixMS || report.EndedUnixMS != last.EndedUnixMS {
		return errors.New("final report does not cover the scoring legs")
	}
	totals := make(map[string]reportPlayer, len(report.Players))
	for sequence := uint32(1); sequence <= state.LegCount; sequence++ {
		leg := r.waves[state.LegKeys[sequence]].Result
		for _, player := range leg.Players {
			total := totals[player.TeamUUID]
			total.TeamUUID, total.Name = player.TeamUUID, player.Name
			total.Kills += player.Kills
			total.ConfirmedKills += player.ConfirmedKills
			total.Deaths += player.Deaths
			total.Takeovers += player.Takeovers
			total.IntegrityStatus = mergeIntegrityStatus(total.IntegrityStatus, player.IntegrityStatus)
			totals[player.TeamUUID] = total
		}
	}
	for _, player := range report.Players {
		total := totals[player.TeamUUID]
		if total.Name != player.Name || total.Kills != player.Kills ||
			total.ConfirmedKills != player.ConfirmedKills || total.Deaths != player.Deaths ||
			total.Takeovers != player.Takeovers || total.IntegrityStatus != player.IntegrityStatus {
			return errors.New("final report does not reconcile with the scoring legs")
		}
	}
	return nil
}

func (r *managedReporter) purgeExpiredLocked(now time.Time) {
	for matchID, state := range r.matches {
		if state.LastEndedAt.IsZero() || now.Sub(state.LastEndedAt) <= waveRetention {
			continue
		}
		for _, key := range state.LegKeys {
			delete(r.waves, key)
		}
		delete(r.matches, matchID)
		delete(r.replays, matchID)
	}
}

func (r *managedReporter) validateReport(report matchReport, now time.Time) error {
	roster, ok := r.arena.rosterSnapshot()
	if !ok || !validMatchID(report.MatchID) || len(report.Players) != len(roster.participants) ||
		report.StartedUnixMS <= 0 || report.EndedUnixMS < report.StartedUnixMS ||
		report.DurationMS != report.EndedUnixMS-report.StartedUnixMS ||
		report.EndedUnixMS > now.Add(30*time.Second).UnixMilli() ||
		report.EndedUnixMS < now.Add(-30*time.Minute).UnixMilli() {
		return errors.New("invalid match metadata")
	}
	legMilliseconds := int64(scoringLegLength / time.Millisecond)
	matchMilliseconds := legMilliseconds * int64(scoringLegCount)
	if report.LegCount != scoringLegCount ||
		(report.Kind == "leg" && (report.Sequence < 1 || report.Sequence > scoringLegCount || report.DurationMS != legMilliseconds)) ||
		(report.Kind == "final" && (report.Sequence != scoringLegCount+1 || report.DurationMS != matchMilliseconds)) ||
		(report.Kind != "leg" && report.Kind != "final") {
		return errors.New("invalid scoring sequence")
	}
	expected := make(map[string]startPlayer, len(roster.participants))
	for _, player := range roster.participants {
		expected[player.UUID] = player
	}
	counterMultiplier := uint32(1)
	if report.Kind == "final" {
		counterMultiplier = scoringLegCount
	}
	maximumDeaths := maxLegDeaths * counterMultiplier
	maximumTakeovers := maxLegTakeovers * counterMultiplier
	seen := make(map[string]bool, len(report.Players))
	var totalKills, totalDeaths uint64
	for _, player := range report.Players {
		team, exists := expected[player.TeamUUID]
		if !exists || seen[player.TeamUUID] || player.Name != team.Name || player.Rank < 1 ||
			player.Rank > uint32(len(report.Players)) || player.Deaths > maximumDeaths ||
			player.Takeovers > maximumTakeovers || !validIntegrityStatus(player.IntegrityStatus) ||
			(report.Kind == "final" && player.ConfirmedKills > player.Kills) {
			return errors.New("invalid player result")
		}
		seen[player.TeamUUID] = true
		totalKills += uint64(player.Kills)
		totalDeaths += uint64(player.Deaths)
	}
	if totalKills > totalDeaths {
		return errors.New("kill counters do not reconcile with deaths")
	}
	ranked := append([]reportPlayer(nil), report.Players...)
	sort.Slice(ranked, func(left, right int) bool {
		if ranked[left].Connected != ranked[right].Connected {
			return ranked[left].Connected
		}
		if ranked[left].ConfirmedKills != ranked[right].ConfirmedKills {
			return ranked[left].ConfirmedKills > ranked[right].ConfirmedKills
		}
		if ranked[left].Kills != ranked[right].Kills {
			return ranked[left].Kills > ranked[right].Kills
		}
		if ranked[left].Deaths != ranked[right].Deaths {
			return ranked[left].Deaths < ranked[right].Deaths
		}
		return ranked[left].TeamUUID < ranked[right].TeamUUID
	})
	var rank uint32
	var previous *reportPlayer
	for index := range ranked {
		current := &ranked[index]
		if previous == nil || current.Connected != previous.Connected ||
			current.ConfirmedKills != previous.ConfirmedKills || current.Kills != previous.Kills ||
			current.Deaths != previous.Deaths {
			rank++
		}
		if current.Rank != rank {
			return errors.New("inconsistent player rank")
		}
		previous = current
	}
	return nil
}

func (a *arena) rosterSnapshot() (arenaRoster, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.cfg.roster == nil {
		return arenaRoster{}, false
	}
	snapshot := *a.cfg.roster
	snapshot.participants = append([]startPlayer(nil), a.cfg.roster.participants...)
	snapshot.tokenByName = make(map[string]string, len(a.cfg.roster.tokenByName))
	for name, token := range a.cfg.roster.tokenByName {
		snapshot.tokenByName[name] = token
	}
	return snapshot, true
}

func (r *managedReporter) validateLegPrefixLocked(state *matchSequence, report matchReport) error {
	type counters struct {
		kills         uint64
		confirmations uint64
	}
	totals := make(map[string]counters, len(report.Players))
	for sequence := uint32(1); sequence < report.Sequence; sequence++ {
		key, exists := state.LegKeys[sequence]
		if !exists {
			return errors.New("match reports must arrive in sequence")
		}
		for _, player := range r.waves[key].Result.Players {
			value := totals[player.TeamUUID]
			value.kills += uint64(player.Kills)
			value.confirmations += uint64(player.ConfirmedKills)
			totals[player.TeamUUID] = value
		}
	}
	for _, player := range report.Players {
		value := totals[player.TeamUUID]
		value.kills += uint64(player.Kills)
		value.confirmations += uint64(player.ConfirmedKills)
		if value.confirmations > value.kills {
			return errors.New("confirmed-kill prefix exceeds authoritative frags")
		}
		totals[player.TeamUUID] = value
	}
	return nil
}

func (r *managedReporter) buildObservation(current reportingContext) ([]byte, int, int, error) {
	eligible := make(map[string]bool, len(current.EligibleTokenHashes))
	for _, tokenHash := range current.EligibleTokenHashes {
		eligible[tokenHash] = true
	}
	r.mu.Lock()
	waves := make([]finalizedWave, 0, len(r.waves))
	for _, wave := range r.waves {
		ended := wave.EndedAt.UnixMilli()
		if ended >= current.WaveWindowStartsAt && ended < current.WaveWindowEndsAt {
			waves = append(waves, wave)
		}
	}
	r.mu.Unlock()
	sort.Slice(waves, func(left, right int) bool {
		if waves[left].EndedAt.Equal(waves[right].EndedAt) {
			return waves[left].ID < waves[right].ID
		}
		return waves[left].EndedAt.Before(waves[right].EndedAt)
	})
	if len(waves) > 64 {
		return nil, 0, 0, errors.New("match window contains too many waves")
	}
	out := observation{Context: current.Context, ObjectiveIDs: objectiveIDs, Waves: make([]waveEvidence, 0, len(waves))}
	uniqueTeams := make(map[string]bool)
	for _, wave := range waves {
		scoringNames := make(map[string]bool, len(wave.Identities))
		for name, tokenHash := range wave.Identities {
			if eligible[tokenHash] {
				scoringNames[name] = true
			}
		}
		possible, scores, crownName := scoreMatch(wave.Result, scoringNames)
		teams := make([]teamEvidence, 0, len(wave.Identities))
		for _, player := range wave.Result.Players {
			tokenHash := wave.Identities[player.Name]
			if tokenHash == "" || !eligible[tokenHash] {
				continue
			}
			earned := scores[player.Name]
			if earned > possible {
				earned = possible
			}
			activity := int64(0)
			if player.Connected && scoringIntegrity(player.IntegrityStatus) {
				activity = 1
			} else {
				earned = 0
			}
			teams = append(teams, teamEvidence{
				TokenHash: tokenHash, Activity: evidenceRatio{Earned: activity, Possible: 1},
				Objectives: []evidenceRatio{{Earned: earned, Possible: possible}},
				IsCrown:    player.Name == crownName,
			})
			uniqueTeams[tokenHash] = true
		}
		sort.Slice(teams, func(left, right int) bool { return teams[left].TokenHash < teams[right].TokenHash })
		out.Waves = append(out.Waves, waveEvidence{
			WaveID: wave.ID, EndedAt: wave.EndedAt.UnixMilli(), Teams: teams,
		})
	}
	body, err := json.Marshal(out)
	return body, len(out.Waves), len(uniqueTeams), err
}

func scoreMatch(report matchReport, scoringNames map[string]bool) (int64, map[string]int64, string) {
	scores := make(map[string]int64, len(report.Players))
	possible := int64(1)
	for _, player := range report.Players {
		if !scoringNames[player.Name] || !player.Connected || !scoringIntegrity(player.IntegrityStatus) ||
			player.ConfirmedKills == 0 {
			scores[player.Name] = 0
			continue
		}
		score := int64(player.ConfirmedKills)
		scores[player.Name] = score
		if score > possible {
			possible = score
		}
	}
	crownName := ""
	bestScore, leaderCount := int64(0), 0
	for name, score := range scores {
		if score > bestScore {
			bestScore, leaderCount, crownName = score, 1, name
		} else if score > 0 && score == bestScore {
			leaderCount++
		}
	}
	if leaderCount != 1 {
		crownName = ""
	}
	return possible, scores, crownName
}

func validIntegrityStatus(value string) bool {
	return value == integrityClean || value == integrityAutomatedCombat
}

func scoringIntegrity(value string) bool {
	return value == integrityClean
}

func mergeIntegrityStatus(current, next string) string {
	if current == integrityAutomatedCombat || next == integrityAutomatedCombat {
		return integrityAutomatedCombat
	}
	return integrityClean
}

const (
	integrityClean           = "clean"
	integrityAutomatedCombat = "automated_combat"
)

func decodeExact(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("multiple JSON values")
	}
	return nil
}

func validMatchID(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, character := range value {
		if !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') &&
			!(character >= '0' && character <= '9') && character != '-' && character != '_' && character != '.' {
			return false
		}
	}
	return true
}
