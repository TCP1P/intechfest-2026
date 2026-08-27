package arena

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"math"
	mathrand "math/rand"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var (
	ErrUnauthorized = errors.New("unauthorized")
	ErrConflict     = errors.New("conflict")
	ErrWrongPhase   = errors.New("operation is not available in this heat phase")
	ErrBadRequest   = errors.New("bad request")
)

const (
	manifestSchema         = "r6-8f21"
	clientBuild            = "r6-0f3d3c3-8f21"
	defaultSettingsHash    = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
	maxFramesPerSecond     = 500
	judgementWindowMS      = int64(220)
	osuPointerToleranceU16 = int64(10500)
	seriesLegCount         = 4
	maxScoringWaves        = 64
	cueAdvance             = 2500 * time.Millisecond
	cueGrace               = 750 * time.Millisecond
	maxSessions            = 4096
	maxSessionsPerTeam     = 4
	maxSubscribers         = 1024
	sessionPruneInterval   = time.Minute
	heartbeatTolerance     = 2 * time.Second
)

type Arena struct {
	mu sync.Mutex

	cfg  Config
	pool Pool
	rng  *mathrand.Rand

	heat             *heat
	heatIndex        int64
	songBags         map[Ruleset][]string
	lastSongs        map[Ruleset]string
	rulesetBag       []Ruleset
	lastRuleset      Ruleset
	sessions         map[string]Session
	cueKey           []byte
	seriesProgress   map[string]*teamSeries
	scoringWaves     []ScoringWaveView
	crownOwnerID     string
	crownOwner       string
	crownSince       time.Time
	statePath        string
	persistenceErr   error
	subscribers      map[chan struct{}]struct{}
	nextSessionPrune time.Time
	lastTick         atomic.Int64
}

type heat struct {
	id             string
	index          int64
	phase          Phase
	commitment     string
	selection      Selection
	song           Song
	notes          []Note
	policyProgram  []byte
	enrollmentEnds time.Time
	startAt        time.Time
	endAt          time.Time
	submissionEnds time.Time
	nextHeatAt     time.Time
	runs           map[string]*run
	settled        bool
}

type run struct {
	teamID           string
	teamName         string
	sessionToken     string
	ticket           string
	ready            bool
	appliedMods      *AppliedModProfile
	modifierEnvelope []byte
	readyCapsule     string
	frames           []StampedFrame
	inputHash        hash.Hash
	inputDigestValue string
	lastSequence     uint64
	manifest         *RunManifest
	judge            *JudgeResult
	settlement       *Settlement
	noteFrames       map[int]int
}

type teamSeries struct {
	teamID   string
	teamName string
	legs     map[int]Settlement
}

func New(cfg Config, pool Pool) (*Arena, error) {
	return newArena(cfg, pool, "")
}

// NewPersistent creates an arena whose finalized scoring ledger, Crown, series
// progress, rotation bags, and monotonic heat index survive process restarts.
// The active heat and its session secrets are deliberately not restored: an
// interrupted heat is voided and the next process starts a fresh series.
func NewPersistent(cfg Config, pool Pool, statePath string) (*Arena, error) {
	statePath = strings.TrimSpace(statePath)
	if statePath == "" {
		return nil, fmt.Errorf("persistent arena state path is required")
	}
	return newArena(cfg, pool, statePath)
}

func newArena(cfg Config, pool Pool, statePath string) (*Arena, error) {
	if err := ValidatePool(pool); err != nil {
		return nil, err
	}
	if cfg.EnrollmentDuration <= 0 || cfg.ReadyDuration <= 0 || cfg.SubmissionDuration <= 0 || cfg.ResultsDuration <= 0 {
		return nil, fmt.Errorf("all phase durations must be positive")
	}

	cueKey := make([]byte, sha256.Size)
	if _, err := rand.Read(cueKey); err != nil {
		return nil, fmt.Errorf("create cue key: %w", err)
	}
	now := time.Now().UTC()
	a := &Arena{
		cfg:              cfg,
		pool:             clonePool(pool),
		rng:              mathrand.New(mathrand.NewSource(cfg.Seed)),
		songBags:         make(map[Ruleset][]string),
		lastSongs:        make(map[Ruleset]string),
		sessions:         make(map[string]Session),
		cueKey:           cueKey,
		seriesProgress:   make(map[string]*teamSeries),
		statePath:        statePath,
		subscribers:      make(map[chan struct{}]struct{}),
		nextSessionPrune: now.Add(sessionPruneInterval),
	}
	if statePath != "" {
		state, found, err := loadPersistentState(statePath)
		if err != nil {
			return nil, fmt.Errorf("load arena state: %w", err)
		}
		if found {
			if err := a.restorePersistentState(state); err != nil {
				return nil, fmt.Errorf("restore arena state: %w", err)
			}
		}
	}
	a.lastTick.Store(now.UnixNano())

	a.mu.Lock()
	a.newHeatLocked(now)
	persistenceErr := a.persistenceErr
	a.mu.Unlock()
	if persistenceErr != nil {
		return nil, fmt.Errorf("initialize arena state: %w", persistenceErr)
	}
	return a, nil
}

func ValidatePool(pool Pool) error {
	if pool.Schema != "rythme-pool-v2" {
		return fmt.Errorf("unsupported pool schema %q", pool.Schema)
	}
	if len(pool.Songs) == 0 {
		return fmt.Errorf("pool must contain at least one song")
	}

	seen := make(map[string]struct{}, len(pool.Songs))
	modeCounts := make(map[Ruleset]int)
	for _, song := range pool.Songs {
		if song.ID == "" || len(song.ID) > 120 {
			return fmt.Errorf("pool contains an invalid song id %q", song.ID)
		}
		for _, character := range song.ID {
			if (character < 'a' || character > 'z') &&
				(character < '0' || character > '9') &&
				character != '-' {
				return fmt.Errorf("pool contains an unsafe song id %q", song.ID)
			}
		}
		if _, exists := seen[song.ID]; exists {
			return fmt.Errorf("pool contains duplicate song id %q", song.ID)
		}
		seen[song.ID] = struct{}{}
		if strings.TrimSpace(song.Title) == "" || strings.TrimSpace(song.Artist) == "" {
			return fmt.Errorf("%s: title and artist are required", song.ID)
		}
		if ValidActionMask(song.Ruleset) == 0 {
			return fmt.Errorf("%s: unsupported native ruleset %q", song.ID, song.Ruleset)
		}
		if song.BPM <= 0 || song.DurationMS <= 0 || song.LeadInMS < 0 || song.NoteCount <= 0 {
			return fmt.Errorf("%s: invalid chart timing metadata", song.ID)
		}
		if song.BeatmapsetID <= 0 || song.BeatmapID <= 0 ||
			strings.TrimSpace(song.Difficulty) == "" ||
			strings.TrimSpace(song.Mapper) == "" ||
			song.StarRating < 3.5 {
			return fmt.Errorf("%s: exact hard beatmap metadata is incomplete", song.ID)
		}
		if song.Ruleset == RulesetMania && song.KeyCount != 4 {
			return fmt.Errorf("%s: only native 4K mania maps are supported", song.ID)
		}
		if !strings.HasPrefix(song.SourceURL, "https://") || !strings.HasPrefix(song.OfficialURL, "https://osu.ppy.sh/") {
			return fmt.Errorf("%s: source and official URLs must use the pinned HTTPS endpoints", song.ID)
		}
		decoded, err := hex.DecodeString(song.ArchiveSHA256)
		if err != nil || len(decoded) != sha256.Size {
			return fmt.Errorf("%s: archive_sha256 must be a SHA-256 hex digest", song.ID)
		}
		if song.NoteCount != len(song.Notes) || len(song.Notes) < 100 {
			return fmt.Errorf("%s: note_count does not match the extracted real chart", song.ID)
		}
		var previous int64 = -1
		for index, note := range song.Notes {
			if note.AtMS < 0 || note.AtMS < previous {
				return fmt.Errorf("%s: note %d is not in chart order", song.ID, index)
			}
			if note.Action == 0 || note.Action&^ValidActionMask(song.Ruleset) != 0 {
				return fmt.Errorf("%s: note %d has an invalid action mask", song.ID, index)
			}
			previous = note.AtMS
		}
		modeCounts[song.Ruleset]++
	}
	for _, ruleset := range AllRulesets {
		if modeCounts[ruleset] < 4 {
			return fmt.Errorf("pool needs at least four real %s difficulties", ruleset)
		}
	}
	return nil
}

func clonePool(pool Pool) Pool {
	cloned := pool
	cloned.Songs = append([]Song(nil), pool.Songs...)
	for index := range cloned.Songs {
		cloned.Songs[index].Notes = append([]Note(nil), pool.Songs[index].Notes...)
	}
	return cloned
}

func (a *Arena) Run(ctx context.Context) error {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case now := <-ticker.C:
			now = now.UTC()
			a.mu.Lock()
			if !now.Before(a.nextSessionPrune) {
				a.pruneSessionsLocked(now)
				a.nextSessionPrune = now.Add(sessionPruneInterval)
			}
			changed := a.advanceLocked(now)
			if changed {
				a.notifyLocked()
			}
			persistenceErr := a.persistenceErr
			a.mu.Unlock()
			a.lastTick.Store(now.UnixNano())
			if persistenceErr != nil {
				return fmt.Errorf("persist arena state: %w", persistenceErr)
			}
		}
	}
}

// Healthy reports whether the arena scheduler is still advancing. Serving an
// HTTP response alone is not sufficient: a wedged scheduler would leave every
// player stuck in the same heat even though the process remained reachable.
func (a *Arena) Healthy(now time.Time) bool {
	a.mu.Lock()
	persistenceHealthy := a.persistenceErr == nil
	a.mu.Unlock()
	if !persistenceHealthy {
		return false
	}
	lastTick := a.lastTick.Load()
	if lastTick == 0 {
		return false
	}
	age := now.UTC().Sub(time.Unix(0, lastTick))
	return age >= -time.Second && age <= heartbeatTolerance
}

func (a *Arena) CreateLocalSession(teamID, teamName string) (Session, error) {
	now := time.Now().UTC()
	keyMaterial := make([]byte, channelKeySize*channelKeyCount)
	if _, err := rand.Read(keyMaterial); err != nil {
		return Session{}, err
	}
	defer zeroBytes(keyMaterial)
	token, err := randomToken(32)
	if err != nil {
		return Session{}, err
	}
	channelID, err := randomToken(16)
	if err != nil {
		return Session{}, err
	}
	return a.storeSession(
		token,
		teamID,
		teamName,
		channelID,
		keyMaterial[0:32],
		keyMaterial[32:64],
		keyMaterial[64:96],
		now,
		now.Add(channelLifetime),
	)
}

func (a *Arena) storeSession(
	token string,
	teamID string,
	teamName string,
	channelID string,
	requestKey []byte,
	responseKey []byte,
	stateKey []byte,
	issuedAt time.Time,
	expiresAt time.Time,
) (Session, error) {
	teamID = strings.TrimSpace(teamID)
	teamName = strings.TrimSpace(teamName)
	if teamID == "" || teamName == "" || len(teamID) > 80 || len(teamName) > 80 {
		return Session{}, fmt.Errorf("%w: team id and name are required", ErrBadRequest)
	}
	if token == "" || channelID == "" ||
		len(requestKey) != channelKeySize ||
		len(responseKey) != channelKeySize ||
		len(stateKey) != channelKeySize {
		return Session{}, fmt.Errorf("%w: invalid secure channel material", ErrBadRequest)
	}
	issuedAt = issuedAt.UTC()
	expiresAt = expiresAt.UTC()
	if !expiresAt.After(issuedAt) {
		return Session{}, fmt.Errorf("%w: invalid secure channel lifetime", ErrBadRequest)
	}
	session := Session{
		Token:       token,
		TeamID:      teamID,
		TeamName:    teamName,
		ChannelID:   channelID,
		RequestKey:  append([]byte(nil), requestKey...),
		ResponseKey: append([]byte(nil), responseKey...),
		StateKey:    append([]byte(nil), stateKey...),
		IssuedAt:    issuedAt,
		ExpiresAt:   expiresAt,
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	a.pruneSessionsLocked(issuedAt)
	if _, exists := a.sessions[token]; exists {
		return Session{}, fmt.Errorf("%w: secure session token collision", ErrConflict)
	}
	for a.teamSessionCountLocked(teamID) >= maxSessionsPerTeam {
		if !a.evictOldestSessionLocked(teamID) {
			return Session{}, fmt.Errorf("%w: all secure sessions for this team are active", ErrConflict)
		}
	}
	for len(a.sessions) >= maxSessions {
		if !a.evictOldestSessionLocked("") {
			return Session{}, fmt.Errorf("%w: secure session capacity reached", ErrConflict)
		}
	}
	a.sessions[token] = session
	return cloneSession(session), nil
}

func (a *Arena) Session(token string) (Session, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	session, ok := a.sessions[token]
	if !ok {
		return Session{}, ErrUnauthorized
	}
	if !time.Now().UTC().Before(session.ExpiresAt) {
		delete(a.sessions, token)
		return Session{}, fmt.Errorf("%w: secure channel expired", ErrUnauthorized)
	}
	return cloneSession(session), nil
}

func cloneSession(session Session) Session {
	session.RequestKey = append([]byte(nil), session.RequestKey...)
	session.ResponseKey = append([]byte(nil), session.ResponseKey...)
	session.StateKey = append([]byte(nil), session.StateKey...)
	session.LastRequestMAC = append([]byte(nil), session.LastRequestMAC...)
	return session
}

func (a *Arena) pruneSessionsLocked(now time.Time) {
	for token, session := range a.sessions {
		if !now.Before(session.ExpiresAt) {
			delete(a.sessions, token)
		}
	}
}

func (a *Arena) teamSessionCountLocked(teamID string) int {
	count := 0
	for _, session := range a.sessions {
		if session.TeamID == teamID {
			count++
		}
	}
	return count
}

func (a *Arena) activeSessionLocked(token string) bool {
	if a.heat == nil {
		return false
	}
	for _, run := range a.heat.runs {
		if run.sessionToken == token {
			return true
		}
	}
	return false
}

func (a *Arena) evictOldestSessionLocked(teamID string) bool {
	oldestToken := ""
	var oldestIssuedAt time.Time
	for token, session := range a.sessions {
		if (teamID != "" && session.TeamID != teamID) || a.activeSessionLocked(token) {
			continue
		}
		if oldestToken == "" || session.IssuedAt.Before(oldestIssuedAt) ||
			(session.IssuedAt.Equal(oldestIssuedAt) && token < oldestToken) {
			oldestToken = token
			oldestIssuedAt = session.IssuedAt
		}
	}
	if oldestToken == "" {
		return false
	}
	delete(a.sessions, oldestToken)
	return true
}

func (a *Arena) Enroll(token string, now time.Time) (EnrollResponse, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now.UTC())

	session, ok := a.sessions[token]
	if !ok {
		return EnrollResponse{}, ErrUnauthorized
	}
	if a.heat.phase != PhaseEnrollment {
		return EnrollResponse{}, ErrWrongPhase
	}
	if existing := a.heat.runs[session.TeamID]; existing != nil {
		if existing.sessionToken != token {
			return EnrollResponse{}, fmt.Errorf("%w: team already has a performer in this heat", ErrConflict)
		}
		return EnrollResponse{HeatID: a.heat.id, HeatTicketID: existing.ticket}, nil
	}

	ticket, err := randomToken(24)
	if err != nil {
		return EnrollResponse{}, err
	}
	a.heat.runs[session.TeamID] = &run{
		teamID:       session.TeamID,
		teamName:     session.TeamName,
		sessionToken: token,
		ticket:       ticket,
		noteFrames:   make(map[int]int),
	}
	a.notifyLocked()
	return EnrollResponse{HeatID: a.heat.id, HeatTicketID: ticket}, nil
}

func (a *Arena) Ready(token, ticket, encodedEnvelope string, now time.Time) (ReadyResponse, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now.UTC())

	run, err := a.authorizedRunLocked(token, ticket)
	if err != nil {
		return ReadyResponse{}, err
	}
	if a.heat.phase != PhaseReveal {
		return ReadyResponse{}, ErrWrongPhase
	}
	envelope, err := decodeModifierEnvelope(encodedEnvelope)
	if err != nil {
		return ReadyResponse{}, fmt.Errorf("%w: %v", ErrBadRequest, err)
	}
	requestedMods, err := readyModifierProfile(envelope, a.heat.policyProgram)
	if err != nil {
		return ReadyResponse{}, fmt.Errorf("%w: %v", ErrBadRequest, err)
	}
	applied, err := canonicalAppliedMods(a.heat.selection.Ruleset, requestedMods)
	if err != nil {
		return ReadyResponse{}, fmt.Errorf("%w: %v", ErrBadRequest, err)
	}
	if run.ready {
		if run.appliedMods == nil || *run.appliedMods != applied || !bytes.Equal(run.modifierEnvelope, envelope) {
			return ReadyResponse{}, fmt.Errorf("%w: modifier profile was already locked", ErrConflict)
		}
		return ReadyResponse{Ready: true, LockedMods: applied, PolicyCapsule: run.readyCapsule}, nil
	}
	capsule := sealReadyCapsule(readyCapsule{
		TeamID: run.teamID, HeatID: a.heat.id, Ticket: run.ticket,
		SongID: a.heat.selection.SongID, Ruleset: a.heat.selection.Ruleset,
		ChartDigest: a.heat.selection.ChartDigest, Nonce: a.heat.selection.Nonce,
		StartAtUnixMS: a.heat.startAt.UnixMilli(), ModifierEnvelope: envelope,
		Program: a.heat.policyProgram,
	})
	run.appliedMods = &applied
	run.modifierEnvelope = append([]byte(nil), envelope...)
	run.readyCapsule = capsule
	run.ready = true
	a.notifyLocked()
	return ReadyResponse{Ready: true, LockedMods: applied, PolicyCapsule: capsule}, nil
}

func (a *Arena) SubmitFrames(token string, frames []InputFrame, now time.Time) (InputResponse, error) {
	if len(frames) == 0 || len(frames) > 64 {
		return InputResponse{}, fmt.Errorf("%w: send between 1 and 64 frames", ErrBadRequest)
	}

	now = now.UTC()
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now)
	if a.heat.phase != PhaseRunning {
		return InputResponse{}, ErrWrongPhase
	}

	first := frames[0]
	run, err := a.authorizedRunLocked(token, first.HeatTicketID)
	if err != nil {
		return InputResponse{}, err
	}
	if !run.ready {
		return InputResponse{}, fmt.Errorf("%w: performer did not become ready", ErrConflict)
	}
	if run.judge != nil {
		return InputResponse{}, fmt.Errorf("%w: input transcript is already sealed", ErrConflict)
	}

	candidateLastSequence := run.lastSequence
	recentFrames := recentFrameCount(run.frames, now)
	noteFrameDeltas := make(map[int]int, len(frames))
	staged := make([]StampedFrame, len(frames))
	for index, frame := range frames {
		if frame.HeatTicketID != run.ticket || frame.Ruleset != a.heat.selection.Ruleset {
			return InputResponse{}, fmt.Errorf("%w: frame %d is bound to the wrong heat or ruleset", ErrBadRequest, index)
		}
		if frame.Sequence <= candidateLastSequence {
			return InputResponse{}, fmt.Errorf("%w: frame sequence must strictly increase", ErrConflict)
		}
		if err := validateFrameShape(frame); err != nil {
			return InputResponse{}, fmt.Errorf("%w: frame %d: %v", ErrBadRequest, index, err)
		}
		if frame.NoteIndex < 0 || frame.NoteIndex >= len(a.heat.notes) {
			return InputResponse{}, fmt.Errorf("%w: frame %d has an invalid note index", ErrBadRequest, index)
		}
		note := a.heat.notes[frame.NoteIndex]
		if frame.ActionBits != 0 && frame.ActionBits&note.Action == 0 {
			return InputResponse{}, fmt.Errorf("%w: frame %d is not compatible with its note", ErrBadRequest, index)
		}
		opens, expires := a.cueWindowLocked(run, note)
		if now.Before(opens) || now.After(expires) ||
			!hmac.Equal([]byte(frame.CueToken), []byte(a.cueTokenLocked(run, frame.NoteIndex, opens, expires))) {
			return InputResponse{}, fmt.Errorf("%w: frame %d has an unavailable or expired cue", ErrUnauthorized, index)
		}
		if run.noteFrames[frame.NoteIndex]+noteFrameDeltas[frame.NoteIndex] >= 2 {
			return InputResponse{}, fmt.Errorf("%w: note %d already has a complete input pulse", ErrConflict, frame.NoteIndex)
		}
		if recentFrames+index >= maxFramesPerSecond {
			return InputResponse{}, fmt.Errorf("%w: input rate exceeded", ErrConflict)
		}
		candidateLastSequence = frame.Sequence
		noteFrameDeltas[frame.NoteIndex]++
		staged[index] = StampedFrame{InputFrame: frame, ReceivedAt: now}
	}

	run.frames = append(run.frames, staged...)
	for noteIndex, delta := range noteFrameDeltas {
		run.noteFrames[noteIndex] += delta
	}
	run.lastSequence = candidateLastSequence
	if run.inputHash == nil {
		run.inputHash = sha256.New()
		writeInputDigest(run.inputHash, run.frames)
	} else {
		writeInputDigest(run.inputHash, staged)
	}
	digest := hex.EncodeToString(run.inputHash.Sum(nil))
	run.inputDigestValue = digest
	a.notifyLocked()
	return InputResponse{Accepted: len(staged), Digest: digest}, nil
}

func (a *Arena) Cues(token, ticket string, now time.Time) (CueResponse, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	now = now.UTC()
	a.advanceLocked(now)

	run, err := a.authorizedRunLocked(token, ticket)
	if err != nil {
		return CueResponse{}, err
	}
	if !run.ready || run.appliedMods == nil {
		return CueResponse{}, fmt.Errorf("%w: lock modifiers before requesting live cues", ErrConflict)
	}
	if run.judge != nil {
		return CueResponse{}, fmt.Errorf("%w: input transcript is already sealed", ErrConflict)
	}
	if a.heat.phase != PhaseReveal && a.heat.phase != PhaseRunning {
		return CueResponse{}, ErrWrongPhase
	}

	response := CueResponse{
		HeatID:          a.heat.id,
		ServerNowUnixMS: now.UnixMilli(),
		Cues:            make([]Cue, 0, 32),
	}
	rateMilli := int64(run.appliedMods.RateMilli)
	elapsedMS := now.Sub(a.heat.startAt).Milliseconds()
	lowerChartMS := (elapsedMS - cueGrace.Milliseconds() - 2) * rateMilli / 1000
	upperChartMS := (elapsedMS + cueAdvance.Milliseconds() + 2) * rateMilli / 1000
	if lowerChartMS < 0 {
		lowerChartMS = 0
	}
	first := sort.Search(len(a.heat.notes), func(index int) bool {
		return a.heat.notes[index].AtMS >= lowerChartMS
	})
	last := sort.Search(len(a.heat.notes), func(index int) bool {
		return a.heat.notes[index].AtMS > upperChartMS
	})
	for index := first; index < last; index++ {
		note := a.heat.notes[index]
		opens, expires := a.cueWindowLocked(run, note)
		if now.Before(opens) || now.After(expires) {
			continue
		}
		response.Cues = append(response.Cues, Cue{
			NoteIndex:     index,
			OpensUnixMS:   opens.UnixMilli(),
			ExpiresUnixMS: expires.UnixMilli(),
			Token:         a.cueTokenLocked(run, index, opens, expires),
		})
	}
	return response, nil
}

func (a *Arena) cueWindowLocked(run *run, note Note) (time.Time, time.Time) {
	rateMilli := int64(1000)
	if run.appliedMods != nil && run.appliedMods.RateMilli > 0 {
		rateMilli = int64(run.appliedMods.RateMilli)
	}
	wallOffset := time.Duration(note.AtMS*1000/rateMilli) * time.Millisecond
	target := a.heat.startAt.Add(wallOffset)
	return target.Add(-cueAdvance), target.Add(cueGrace)
}

func (a *Arena) cueTokenLocked(run *run, noteIndex int, opens, expires time.Time) string {
	payload := strings.Join([]string{
		"rythme-live-cue-v1",
		a.heat.id,
		run.teamID,
		run.ticket,
		strconv.Itoa(noteIndex),
		strconv.FormatInt(opens.UnixMilli(), 10),
		strconv.FormatInt(expires.UnixMilli(), 10),
		"",
	}, "\n")
	mac := hmac.New(sha256.New, a.cueKey)
	mac.Write([]byte(payload))
	return hex.EncodeToString(mac.Sum(nil))
}

func (a *Arena) SubmitManifest(token string, manifest RunManifest, now time.Time) (ManifestResponse, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now.UTC())
	if a.heat.phase != PhaseRunning && a.heat.phase != PhaseSubmission {
		return ManifestResponse{}, ErrWrongPhase
	}

	run, err := a.authorizedRunLocked(token, manifest.HeatTicketID)
	if err != nil {
		return ManifestResponse{}, err
	}
	if run.manifest != nil {
		return ManifestResponse{}, fmt.Errorf("%w: a manifest was already accepted", ErrConflict)
	}
	if run.judge == nil {
		return ManifestResponse{}, fmt.Errorf("%w: input transcript is not sealed", ErrConflict)
	}
	if err := a.verifyManifestLocked(run, manifest); err != nil {
		return ManifestResponse{}, err
	}

	run.manifest = &manifest
	settlement := a.settleRunLocked(run)
	run.settlement = &settlement
	a.notifyLocked()
	return ManifestResponse{Settlement: settlement, TookCrown: false}, nil
}

func (a *Arena) Result(token, ticket string, now time.Time) (JudgeResult, *Settlement, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	now = now.UTC()
	a.advanceLocked(now)

	run, err := a.authorizedRunLocked(token, ticket)
	if err != nil {
		return JudgeResult{}, nil, err
	}
	if run.judge == nil {
		if a.heat.phase != PhaseRunning || now.Before(a.runInputClosesAtLocked(run)) {
			return JudgeResult{}, nil, ErrWrongPhase
		}
		a.sealRunLocked(run)
		a.notifyLocked()
	}

	var settlement *Settlement
	if run.settlement != nil {
		copy := *run.settlement
		settlement = &copy
	}
	return *run.judge, settlement, nil
}

func (a *Arena) bindJudgeCapsuleLocked(run *run, result *JudgeResult) {
	if result == nil || result.JudgeCapsule != "" {
		return
	}
	result.JudgeCapsule = sealJudgeCapsule(judgeCapsule{
		TeamID: run.teamID, HeatID: a.heat.id, Ticket: run.ticket,
		InputDigest: result.InputDigest, RawBasis: result.RawBasis,
	})
}

func (a *Arena) sealRunLocked(run *run) {
	if run.judge != nil {
		return
	}
	result := judge(a.heat, run)
	a.bindJudgeCapsuleLocked(run, &result)
	run.judge = &result
}

// runInputClosesAtLocked returns the instant after which no valid live cue can
// produce another scoreable input for this performer's locked playback rate.
// The global heat still reserves enough time for the slowest supported rate.
func (a *Arena) runInputClosesAtLocked(run *run) time.Time {
	if run.appliedMods == nil || run.appliedMods.RateMilli <= 0 || len(a.heat.notes) == 0 {
		return a.heat.endAt
	}

	_, closesAt := a.cueWindowLocked(run, a.heat.notes[len(a.heat.notes)-1])
	if closesAt.After(a.heat.endAt) {
		return a.heat.endAt
	}
	return closesAt
}

func (a *Arena) View(now time.Time) ArenaView {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now.UTC())
	return a.viewLocked(now.UTC())
}

// ScoringWindow returns immutable finalized waves inside one RSCTF scoring
// round. The bounded durable ledger survives coordinator and observer restarts
// without putting historical wave rows on the heavily-polled public route.
func (a *Arena) ScoringWindow(from, to, now time.Time) ScoringWindowView {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.advanceLocked(now.UTC())
	waves := make([]ScoringWaveView, 0, len(a.scoringWaves))
	for _, wave := range a.scoringWaves {
		endedAt := time.UnixMilli(wave.EndedAtUnixMS)
		if endedAt.Before(from) || !endedAt.Before(to) {
			continue
		}
		cloned := wave
		// Keep an empty result collection as [] on the JSON wire. append(nil,
		// empty...) produces a nil slice, which serializes as null and makes a
		// valid no-performer wave fail the referee's strict snapshot schema.
		cloned.Results = append(make([]ScoringResultView, 0, len(wave.Results)), wave.Results...)
		waves = append(waves, cloned)
	}
	return ScoringWindowView{Waves: waves}
}

func (a *Arena) Subscribe() (<-chan struct{}, func(), error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(a.subscribers) >= maxSubscribers {
		return nil, nil, fmt.Errorf("%w: event subscriber capacity reached", ErrConflict)
	}
	ch := make(chan struct{}, 1)
	a.subscribers[ch] = struct{}{}
	cancel := func() {
		a.mu.Lock()
		defer a.mu.Unlock()
		if _, ok := a.subscribers[ch]; ok {
			delete(a.subscribers, ch)
			close(ch)
		}
	}
	return ch, cancel, nil
}

func (a *Arena) Pool() Pool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return clonePool(a.pool)
}

// SetPool replaces the songs used for future heats. An in-progress heat keeps
// its already committed selection and remains downloadable until it ends.
func (a *Arena) SetPool(pool Pool) error {
	if err := ValidatePool(pool); err != nil {
		return err
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	a.pool = clonePool(pool)
	a.songBags = make(map[Ruleset][]string)
	a.lastSongs = make(map[Ruleset]string)
	a.persistLocked()
	if a.persistenceErr != nil {
		return fmt.Errorf("persist pool rotation: %w", a.persistenceErr)
	}
	return nil
}

// HasSong reports whether a song belongs to the current pool or the active
// heat. Keeping the active heat visible avoids breaking downloads when a
// scheduled pool refresh occurs during the reveal window.
func (a *Arena) HasSong(songID string) bool {
	_, ok := a.Song(songID)
	return ok
}

func (a *Arena) Song(songID string) (Song, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.heat != nil && a.heat.song.ID == songID {
		song := a.heat.song
		song.Notes = append([]Note(nil), song.Notes...)
		return song, true
	}
	for _, song := range a.pool.Songs {
		if song.ID == songID {
			song.Notes = append([]Note(nil), song.Notes...)
			return song, true
		}
	}
	return Song{}, false
}

func (a *Arena) advanceLocked(now time.Time) bool {
	changed := false

	for !now.Before(a.heat.nextHeatAt) {
		if !a.heat.settled {
			a.finalizeHeatLocked(a.heat.submissionEnds)
		}
		a.newHeatLocked(a.heat.nextHeatAt)
		changed = true
	}

	phase := phaseAt(a.heat, now)
	if phase != a.heat.phase {
		a.heat.phase = phase
		changed = true
	}

	if !now.Before(a.heat.endAt) {
		for _, run := range a.heat.runs {
			a.sealRunLocked(run)
		}
	}
	if !now.Before(a.heat.submissionEnds) && !a.heat.settled {
		a.finalizeHeatLocked(a.heat.submissionEnds)
		changed = true
	}

	return changed
}

func (a *Arena) newHeatLocked(now time.Time) {
	a.heatIndex++
	seriesIndex := seriesIndexForHeat(a.heatIndex)
	seriesLeg := seriesLegForHeat(a.heatIndex)
	if seriesLeg == 1 {
		a.seriesProgress = make(map[string]*teamSeries)
	}
	ruleset := a.nextRulesetLocked()
	song := a.nextSongLocked(ruleset)
	nonceBytes := make([]byte, 16)
	_, _ = a.rng.Read(nonceBytes)
	nonce := hex.EncodeToString(nonceBytes)
	digest := ChartDigest(song)
	policy := derivePolicySpec(nonce)
	selection := Selection{
		SongID:         song.ID,
		SongTitle:      song.Title,
		SongArtist:     song.Artist,
		Ruleset:        ruleset,
		ChartDigest:    digest,
		ControlProfile: ControlProfile(ruleset),
		BPM:            song.BPM,
		DurationMS:     song.DurationMS,
		LeadInMS:       song.LeadInMS,
		NoteCount:      song.NoteCount,
		Origin:         song.Origin,
		BeatmapsetID:   song.BeatmapsetID,
		BeatmapID:      song.BeatmapID,
		Difficulty:     song.Difficulty,
		StarRating:     song.StarRating,
		Status:         song.Status,
		KeyCount:       song.KeyCount,
		Mapper:         song.Mapper,
		SourceURL:      song.SourceURL,
		OfficialURL:    song.OfficialURL,
		License:        song.License,
		ArchiveSHA256:  song.ArchiveSHA256,
		Nonce:          nonce,
		SeriesIndex:    seriesIndex,
		SeriesLeg:      seriesLeg,
	}
	commitmentPayload := fmt.Sprintf("rythme-selection-v1\n%d\n%s\n%s\n%s\n%s\n", a.heatIndex, song.ID, ruleset, digest, nonce)
	commitmentSum := sha256.Sum256([]byte(commitmentPayload))
	enrollmentEnds := now.Add(a.cfg.EnrollmentDuration)
	startAt := enrollmentEnds.Add(a.cfg.ReadyDuration)
	// The shared result window begins after the slowest selectable ranked
	// playback rate (HT/DC at 0.75x) has completed.
	endAt := startAt.Add(time.Duration(song.DurationMS*1000/750) * time.Millisecond)
	submissionEnds := endAt.Add(a.cfg.SubmissionDuration)

	a.heat = &heat{
		id:             fmt.Sprintf("heat-%06d", a.heatIndex),
		index:          a.heatIndex,
		phase:          PhaseEnrollment,
		commitment:     hex.EncodeToString(commitmentSum[:]),
		selection:      selection,
		song:           song,
		notes:          Chart(song),
		policyProgram:  append([]byte(nil), policy.program...),
		enrollmentEnds: enrollmentEnds,
		startAt:        startAt,
		endAt:          endAt,
		submissionEnds: submissionEnds,
		nextHeatAt:     submissionEnds.Add(a.cfg.ResultsDuration),
		runs:           make(map[string]*run),
	}
	a.persistLocked()
}

func (a *Arena) nextSongLocked(ruleset Ruleset) Song {
	bag := a.songBags[ruleset]
	if len(bag) == 0 {
		bag = make([]string, 0, len(a.pool.Songs))
		for _, song := range a.pool.Songs {
			if song.Ruleset == ruleset {
				bag = append(bag, song.ID)
			}
		}
		a.rng.Shuffle(len(bag), func(i, j int) {
			bag[i], bag[j] = bag[j], bag[i]
		})
		if len(bag) > 1 && bag[0] == a.lastSongs[ruleset] {
			bag[0], bag[1] = bag[1], bag[0]
		}
	}

	songID := bag[0]
	a.songBags[ruleset] = bag[1:]
	a.lastSongs[ruleset] = songID
	for _, song := range a.pool.Songs {
		if song.ID == songID && song.Ruleset == ruleset {
			return song
		}
	}
	panic("arena mode-specific song bag referenced a song outside the current pool")
}

func (a *Arena) nextRulesetLocked() Ruleset {
	if len(a.rulesetBag) == 0 {
		a.rulesetBag = append([]Ruleset(nil), AllRulesets...)
		a.rng.Shuffle(len(a.rulesetBag), func(i, j int) {
			a.rulesetBag[i], a.rulesetBag[j] = a.rulesetBag[j], a.rulesetBag[i]
		})
		if len(a.rulesetBag) > 1 && a.rulesetBag[0] == a.lastRuleset {
			a.rulesetBag[0], a.rulesetBag[1] = a.rulesetBag[1], a.rulesetBag[0]
		}
	}
	next := a.rulesetBag[0]
	a.rulesetBag = a.rulesetBag[1:]
	a.lastRuleset = next
	return next
}

func (a *Arena) finalizeHeatLocked(now time.Time) {
	if a.heat.settled {
		return
	}
	a.heat.settled = true

	for _, run := range a.heat.runs {
		a.sealRunLocked(run)
		if run.settlement == nil {
			settlement := a.settleRunLocked(run)
			run.settlement = &settlement
		}
		progress := a.seriesProgress[run.teamID]
		if progress == nil {
			progress = &teamSeries{
				teamID:   run.teamID,
				teamName: run.teamName,
				legs:     make(map[int]Settlement),
			}
			a.seriesProgress[run.teamID] = progress
		}
		progress.legs[seriesLegForHeat(a.heat.index)] = *run.settlement
	}

	candidates := make([]scoringCandidate, 0, len(a.heat.runs))
	for _, run := range a.heat.runs {
		if run.manifest == nil || run.settlement == nil {
			continue
		}
		score := run.settlement.SettledScore
		if score < 0 {
			score = 0
		}
		candidates = append(candidates, scoringCandidate{
			teamID:   run.teamID,
			teamName: run.teamName,
			score:    score,
		})
	}
	crownID := selectWaveCrown(candidates)
	crownName := ""
	results := make([]ScoringResultView, 0, len(candidates))
	for _, candidate := range candidates {
		isCrown := candidate.teamID == crownID
		if isCrown {
			crownName = candidate.teamName
		}
		results = append(results, ScoringResultView{
			TeamID:   candidate.teamID,
			TeamName: candidate.teamName,
			Score:    candidate.score,
			IsCrown:  isCrown,
		})
	}
	sort.Slice(results, func(i, j int) bool {
		if results[i].Score != results[j].Score {
			return results[i].Score > results[j].Score
		}
		return results[i].TeamID < results[j].TeamID
	})
	a.scoringWaves = append(a.scoringWaves, ScoringWaveView{
		WaveID:         a.heat.id,
		EndedAtUnixMS:  now.UnixMilli(),
		Ruleset:        a.heat.selection.Ruleset,
		CrownOwnerID:   crownID,
		CrownOwnerName: crownName,
		Results:        results,
	})
	if len(a.scoringWaves) > maxScoringWaves {
		a.scoringWaves = append([]ScoringWaveView(nil), a.scoringWaves[len(a.scoringWaves)-maxScoringWaves:]...)
	}

	crownChanged := crownID != a.crownOwnerID
	a.crownOwnerID = crownID
	a.crownOwner = crownName
	if crownID == "" {
		a.crownSince = time.Time{}
	} else if crownChanged || a.crownSince.IsZero() {
		a.crownSince = now
	}
	a.persistLocked()
}

type scoringCandidate struct {
	teamID   string
	teamName string
	score    int64
}

func selectWaveCrown(candidates []scoringCandidate) string {
	bestScore := int64(0)
	leaders := 0
	winnerID := ""
	for _, candidate := range candidates {
		if candidate.score > bestScore {
			bestScore = candidate.score
			leaders = 1
			winnerID = candidate.teamID
		} else if candidate.score == bestScore && candidate.score > 0 {
			leaders++
		}
	}
	if bestScore == 0 || leaders != 1 {
		return ""
	}
	return winnerID
}

func (a *Arena) settleRunLocked(run *run) Settlement {
	result := JudgeResult{InputDigest: runInputDigest(run)}
	if run.judge != nil {
		result = *run.judge
	}
	settlement := Settlement{
		TeamID:             run.teamID,
		TeamName:           run.teamName,
		AppliedMultiplier:  1,
		AttestedMultiplier: 1,
		Multiplier:         1,
		SettledScore:       result.RawBasis,
		Judge:              result,
		SeriesIndex:        seriesIndexForHeat(a.heat.index),
		SeriesLeg:          seriesLegForHeat(a.heat.index),
	}

	if !run.ready || run.appliedMods == nil {
		settlement.Reason = "performer was not ready"
		return settlement
	}
	if run.manifest == nil {
		settlement.Reason = "completion record was not accepted"
		return settlement
	}

	claim := run.manifest.AttestedMods
	applied := *run.appliedMods
	settlement.AppliedMods = &applied
	settlement.AttestedMods = &claim
	settlement.AppliedMultiplier = float64(applied.ScoreMultiplierMilli) / 1000

	bonusEligible := !modClaimMatchesApplied(claim, applied)
	if !bonusEligible {
		settlement.Multiplier = settlement.AppliedMultiplier
	} else {
		settlement.AttestedMultiplier = multiplierFor(a.heat.selection.Ruleset, claim)

		settlement.Multiplier = settlement.AppliedMultiplier / settlement.AttestedMultiplier
	}
	settlement.SettledScore = int64(math.Floor(float64(result.RawBasis) * settlement.Multiplier))

	if result.RawBasis < a.cfg.RawScoreFloor {
		settlement.Reason = "raw performance floor was not reached"
		return settlement
	}
	if result.HitCount*100 < result.NoteCount*65 {
		settlement.Reason = "input coverage floor was not reached"
		return settlement
	}
	if settlement.SettledScore < a.cfg.CrownThreshold {
		settlement.Reason = "exploit boost score threshold was not reached"
		return settlement
	}
	if !bonusEligible {
		settlement.Reason = "exploit boost policy requirements were not met"
		return settlement
	}

	settlement.Eligible = true
	return settlement
}

func (a *Arena) verifyManifestLocked(run *run, manifest RunManifest) error {
	if manifest.Schema != manifestSchema ||
		manifest.ClientBuild != clientBuild ||
		manifest.TeamID != run.teamID ||
		manifest.HeatID != a.heat.id ||
		manifest.HeatTicketID != run.ticket ||
		manifest.SongID != a.heat.selection.SongID ||
		manifest.Ruleset != a.heat.selection.Ruleset ||
		manifest.ChartDigest != a.heat.selection.ChartDigest ||
		manifest.StartAtUnixMS != a.heat.startAt.UnixMilli() ||
		manifest.InputDigest != run.judge.InputDigest ||
		manifest.ReadyCapsule == "" || manifest.ReadyCapsule != run.readyCapsule ||
		manifest.JudgeCapsule == "" || manifest.JudgeCapsule != run.judge.JudgeCapsule {
		return fmt.Errorf("%w: manifest does not match the sealed run", ErrBadRequest)
	}
	if run.appliedMods == nil {
		return fmt.Errorf("%w: modifier profile was not locked", ErrBadRequest)
	}

	ready, readyRaw, err := openReadyCapsule(manifest.ReadyCapsule)
	if err != nil || ready.TeamID != run.teamID || ready.HeatID != a.heat.id ||
		ready.Ticket != run.ticket || ready.Ruleset != a.heat.selection.Ruleset ||
		ready.SongID != a.heat.selection.SongID || ready.ChartDigest != a.heat.selection.ChartDigest ||
		ready.Nonce != a.heat.selection.Nonce || ready.StartAtUnixMS != a.heat.startAt.UnixMilli() ||
		!bytes.Equal(ready.ModifierEnvelope, run.modifierEnvelope) || !bytes.Equal(ready.Program, a.heat.policyProgram) {
		return fmt.Errorf("%w: ready capsule does not match the locked run", ErrBadRequest)
	}
	judged, judgeRaw, err := openJudgeCapsule(manifest.JudgeCapsule)
	if err != nil || judged.TeamID != run.teamID || judged.HeatID != a.heat.id ||
		judged.Ticket != run.ticket || judged.InputDigest != run.judge.InputDigest ||
		judged.RawBasis != run.judge.RawBasis {
		return fmt.Errorf("%w: judge capsule does not match the sealed transcript", ErrBadRequest)
	}
	expectedClaim, err := executePolicyVM(ready.Ruleset, ready.ModifierEnvelope, ready.Program)
	if err != nil || !sameModClaim(manifest.AttestedMods, expectedClaim) {
		return fmt.Errorf("%w: native policy claim is invalid", ErrBadRequest)
	}

	provided, err := hex.DecodeString(manifest.NativeProof)
	if err != nil || len(provided) != sha256.Size ||
		!hmac.Equal(provided, nativeProof(readyRaw, judgeRaw, expectedClaim)) {
		return fmt.Errorf("%w: native policy proof did not verify", ErrUnauthorized)
	}
	return nil
}

func (a *Arena) authorizedRunLocked(token, ticket string) (*run, error) {
	session, ok := a.sessions[token]
	if !ok {
		return nil, ErrUnauthorized
	}
	run := a.heat.runs[session.TeamID]
	if run == nil || run.ticket != ticket || run.sessionToken != token {
		return nil, ErrUnauthorized
	}
	return run, nil
}

func (a *Arena) viewLocked(now time.Time) ArenaView {
	performers := make([]PerformerView, 0, len(a.heat.runs))
	for _, run := range a.heat.runs {
		view := PerformerView{
			TeamID:   run.teamID,
			TeamName: run.teamName,
			Ready:    run.ready,
			Frames:   len(run.frames),
		}
		if run.appliedMods != nil {
			applied := *run.appliedMods
			view.AppliedMods = &applied
		}
		if a.heat.phase != PhaseRunning {
			view.ManifestSent = run.manifest != nil
			if run.judge != nil {
				view.RawBasis = run.judge.RawBasis
			}
			if run.settlement != nil {
				view.SettledScore = run.settlement.SettledScore
				view.Eligible = run.settlement.Eligible
			}
		}
		performers = append(performers, view)
	}
	sort.Slice(performers, func(i, j int) bool {
		return performers[i].TeamName < performers[j].TeamName
	})

	var selection *Selection
	if a.heat.phase != PhaseEnrollment {
		copy := a.heat.selection
		selection = &copy
	}

	view := ArenaView{
		ServerNowUnixMS: now.UnixMilli(),
		PoolSchema:      a.pool.Schema,
		CrownOwnerID:    a.crownOwnerID,
		CrownOwnerName:  a.crownOwner,
		CrownThreshold:  a.cfg.CrownThreshold,
		RawScoreFloor:   a.cfg.RawScoreFloor,
		RulesetBagRule:  "one native osu, taiko, fruits, and 4K mania difficulty per four-wave rotation",
		SeriesRule:      "every finalized heat scores independently; a fresh completed run is required; an exact top-score tie awards no Crown",
		Series:          a.seriesViewLocked(),
		Heat: HeatView{
			ID:             a.heat.id,
			Index:          a.heat.index,
			Phase:          a.heat.phase,
			Commitment:     a.heat.commitment,
			EnrollmentEnds: a.heat.enrollmentEnds.UnixMilli(),
			StartAt:        a.heat.startAt.UnixMilli(),
			EndAt:          a.heat.endAt.UnixMilli(),
			SubmissionEnds: a.heat.submissionEnds.UnixMilli(),
			NextHeatAt:     a.heat.nextHeatAt.UnixMilli(),
			Selection:      selection,
			Performers:     performers,
		},
	}
	if !a.crownSince.IsZero() {
		view.CrownSinceUnixMS = a.crownSince.UnixMilli()
	}
	return view
}

func (a *Arena) seriesViewLocked() SeriesView {
	standingsByTeam := make(map[string]*teamSeries, len(a.seriesProgress)+len(a.heat.runs))
	for teamID, progress := range a.seriesProgress {
		standingsByTeam[teamID] = progress
	}
	for teamID, run := range a.heat.runs {
		if standingsByTeam[teamID] == nil {
			standingsByTeam[teamID] = &teamSeries{
				teamID:   teamID,
				teamName: run.teamName,
				legs:     make(map[int]Settlement),
			}
		}
	}

	standings := make([]SeriesStandingView, 0, len(standingsByTeam))
	for _, progress := range standingsByTeam {
		completed, eligible, weakest, averageRaw, averageSettled := seriesMetrics(progress)
		standings = append(standings, SeriesStandingView{
			TeamID:         progress.teamID,
			TeamName:       progress.teamName,
			CompletedLegs:  completed,
			EligibleLegs:   eligible,
			WeakestSettled: weakest,
			AverageRaw:     averageRaw,
			AverageSettled: averageSettled,
			Complete:       completeSeries(progress),
		})
	}
	sort.Slice(standings, func(i, j int) bool {
		if standings[i].Complete != standings[j].Complete {
			return standings[i].Complete
		}
		if standings[i].WeakestSettled != standings[j].WeakestSettled {
			return standings[i].WeakestSettled > standings[j].WeakestSettled
		}
		if standings[i].AverageRaw != standings[j].AverageRaw {
			return standings[i].AverageRaw > standings[j].AverageRaw
		}
		return standings[i].TeamID < standings[j].TeamID
	})
	return SeriesView{
		Index:      seriesIndexForHeat(a.heat.index),
		CurrentLeg: seriesLegForHeat(a.heat.index),
		TotalLegs:  seriesLegCount,
		Standings:  standings,
	}
}

func (a *Arena) notifyLocked() {
	for ch := range a.subscribers {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

func phaseAt(h *heat, now time.Time) Phase {
	switch {
	case now.Before(h.enrollmentEnds):
		return PhaseEnrollment
	case now.Before(h.startAt):
		return PhaseReveal
	case now.Before(h.endAt):
		return PhaseRunning
	case now.Before(h.submissionEnds):
		return PhaseSubmission
	default:
		return PhaseResults
	}
}

func inputDigest(frames []StampedFrame) string {
	hash := sha256.New()
	writeInputDigest(hash, frames)
	return hex.EncodeToString(hash.Sum(nil))
}

func writeInputDigest(hash hash.Hash, frames []StampedFrame) {
	line := make([]byte, 0, 192)
	for _, frame := range frames {
		line = line[:0]
		line = strconv.AppendUint(line, frame.Sequence, 10)
		line = append(line, '|')
		line = append(line, frame.Ruleset...)
		line = append(line, '|')
		line = strconv.AppendInt(line, int64(frame.NoteIndex), 10)
		line = append(line, '|')
		line = append(line, frame.CueToken...)
		line = append(line, '|')
		line = strconv.AppendUint(line, uint64(frame.ActionBits), 10)
		line = append(line, '|')
		if frame.PointerX == nil {
			line = append(line, '-')
		} else {
			line = strconv.AppendUint(line, uint64(*frame.PointerX), 10)
		}
		line = append(line, '|')
		if frame.PointerY == nil {
			line = append(line, '-')
		} else {
			line = strconv.AppendUint(line, uint64(*frame.PointerY), 10)
		}
		line = append(line, '\n')
		_, _ = hash.Write(line)
	}
}

func runInputDigest(run *run) string {
	if run.inputDigestValue != "" {
		return run.inputDigestValue
	}
	if run.inputHash != nil {
		return hex.EncodeToString(run.inputHash.Sum(nil))
	}
	return inputDigest(run.frames)
}

func judge(h *heat, run *run) JudgeResult {
	bestDeltas := make([]uint16, len(h.notes))
	for index := range bestDeltas {
		bestDeltas[index] = uint16(judgementWindowMS + 1)
	}
	rateMilli := int64(1000)
	if run.appliedMods != nil {
		rateMilli = int64(run.appliedMods.RateMilli)
	}
	var previous uint16
	for _, frame := range run.frames {
		rising := frame.ActionBits &^ previous
		previous = frame.ActionBits
		if rising == 0 {
			continue
		}
		if frame.NoteIndex < 0 || frame.NoteIndex >= len(h.notes) {
			continue
		}
		note := h.notes[frame.NoteIndex]
		if rising&note.Action == 0 {
			continue
		}
		atMS := int64(math.Round(float64(frame.ReceivedAt.Sub(h.startAt).Milliseconds()) * float64(rateMilli) / 1000))
		delta := abs64(atMS - note.AtMS)
		if delta > judgementWindowMS || uint16(delta) >= bestDeltas[frame.NoteIndex] {
			continue
		}
		if h.selection.Ruleset == RulesetOsu {
			if frame.PointerX == nil || frame.PointerY == nil {
				continue
			}
			dx := int64(*frame.PointerX) - int64(note.X)
			dy := int64(*frame.PointerY) - int64(note.Y)
			if dx*dx+dy*dy > osuPointerToleranceU16*osuPointerToleranceU16 {
				continue
			}
		}
		bestDeltas[frame.NoteIndex] = uint16(delta)
	}

	var raw int64
	hits := 0
	combo := 0
	maxCombo := 0
	for _, bestDelta := range bestDeltas {
		if bestDelta > uint16(judgementWindowMS) {
			combo = 0
			continue
		}
		hits++
		combo++
		if combo > maxCombo {
			maxCombo = combo
		}
		switch {
		case bestDelta <= 70:
			raw += 1000
		case bestDelta <= 130:
			raw += 700
		default:
			raw += 300
		}
	}

	perfect := int64(len(h.notes) * 1000)
	rawBasis := int64(0)
	accuracy := float64(0)
	if perfect > 0 {
		rawBasis = raw * 1_000_000 / perfect
		accuracy = float64(raw) / float64(perfect)
	}
	return JudgeResult{
		HitCount:              hits,
		NoteCount:             len(h.notes),
		TotalScoreWithoutMods: raw,
		PerfectRaw:            perfect,
		RawBasis:              rawBasis,
		Accuracy:              accuracy,
		MaxCombo:              maxCombo,
		InputDigest:           runInputDigest(run),
	}
}

func sameModClaim(left, right ModClaim) bool {
	return left.Acronym == right.Acronym &&
		left.RateMilli == right.RateMilli &&
		left.SettingsHash == right.SettingsHash
}

func modClaimMatchesApplied(claim ModClaim, applied AppliedModProfile) bool {
	return claim.Acronym == applied.Acronym &&
		claim.RateMilli == applied.RateMilli &&
		claim.SettingsHash == applied.SettingsHash
}

func multiplierFor(ruleset Ruleset, claim ModClaim) float64 {
	if claim.SettingsHash != defaultSettingsHash {
		return 1
	}
	if claim.Acronym == "NM" && claim.RateMilli == 1000 {
		return 1
	}
	requested := strings.Split(claim.Acronym, ",")
	applied, err := canonicalAppliedMods(ruleset, requested)
	if err != nil ||
		applied.Acronym != claim.Acronym ||
		applied.RateMilli != claim.RateMilli ||
		applied.SettingsHash != claim.SettingsHash {
		return 1
	}
	return float64(applied.ScoreMultiplierMilli) / 1000
}

func betterSettlement(left, right Settlement) bool {
	if left.SettledScore != right.SettledScore {
		return left.SettledScore > right.SettledScore
	}
	if left.Judge.RawBasis != right.Judge.RawBasis {
		return left.Judge.RawBasis > right.Judge.RawBasis
	}
	return left.TeamID < right.TeamID
}

func seriesIndexForHeat(heatIndex int64) int64 {
	return (heatIndex-1)/seriesLegCount + 1
}

func seriesLegForHeat(heatIndex int64) int {
	return int((heatIndex-1)%seriesLegCount) + 1
}

func completeSeries(progress *teamSeries) bool {
	if progress == nil || len(progress.legs) != seriesLegCount {
		return false
	}
	for leg := 1; leg <= seriesLegCount; leg++ {
		if _, ok := progress.legs[leg]; !ok {
			return false
		}
	}
	return true
}

func seriesMetrics(progress *teamSeries) (completed, eligible int, weakest, averageRaw, averageSettled int64) {
	if progress == nil || len(progress.legs) == 0 {
		return 0, 0, 0, 0, 0
	}
	weakest = math.MaxInt64
	var rawTotal int64
	var settledTotal int64
	for leg := 1; leg <= seriesLegCount; leg++ {
		settlement, ok := progress.legs[leg]
		if !ok {
			continue
		}
		completed++
		if settlement.Eligible {
			eligible++
		}
		if settlement.SettledScore < weakest {
			weakest = settlement.SettledScore
		}
		rawTotal += settlement.Judge.RawBasis
		settledTotal += settlement.SettledScore
	}
	if completed == 0 {
		return 0, 0, 0, 0, 0
	}
	return completed, eligible, weakest, rawTotal / int64(completed), settledTotal / int64(completed)
}

func betterSeries(left, right *teamSeries) bool {
	_, _, leftWeakest, leftAverageRaw, leftAverageSettled := seriesMetrics(left)
	_, _, rightWeakest, rightAverageRaw, rightAverageSettled := seriesMetrics(right)
	if leftWeakest != rightWeakest {
		return leftWeakest > rightWeakest
	}
	if leftAverageRaw != rightAverageRaw {
		return leftAverageRaw > rightAverageRaw
	}
	if leftAverageSettled != rightAverageSettled {
		return leftAverageSettled > rightAverageSettled
	}
	return left.teamID < right.teamID
}

func recentFrameCount(frames []StampedFrame, now time.Time) int {
	cutoff := now.Add(-time.Second)
	count := 0
	for index := len(frames) - 1; index >= 0; index-- {
		if frames[index].ReceivedAt.Before(cutoff) {
			break
		}
		count++
	}
	return count
}

func randomToken(size int) (string, error) {
	buffer := make([]byte, size)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return hex.EncodeToString(buffer), nil
}

func abs64(value int64) int64 {
	if value < 0 {
		return -value
	}
	return value
}

func MarshalState(view ArenaView) []byte {
	encoded, _ := json.Marshal(view)
	return encoded
}
