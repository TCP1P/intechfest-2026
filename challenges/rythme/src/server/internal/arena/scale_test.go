package arena

import (
	"crypto/sha256"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"
	"time"
)

func TestLinearJudgeMatchesLegacySemantics(t *testing.T) {
	pool := testPool()
	for _, ruleset := range AllRulesets {
		t.Run(string(ruleset), func(t *testing.T) {
			var song Song
			for _, candidate := range pool.Songs {
				if candidate.Ruleset == ruleset {
					song = candidate
					break
				}
			}
			h, run := judgeFixture(song)
			for index := range run.frames {
				if index%14 == 0 {
					run.frames[index].ReceivedAt = run.frames[index].ReceivedAt.Add(100 * time.Millisecond)
				}
				if ruleset == RulesetOsu && index%26 == 0 && run.frames[index].PointerX != nil {
					far := uint16(65535)
					run.frames[index].PointerX = &far
					run.frames[index].PointerY = &far
				}
			}
			got := judge(h, run)
			want := legacyJudgeForRegression(h, run)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("linear judge changed result:\n got %+v\nwant %+v", got, want)
			}
		})
	}
}

func TestHundredTeamLargestChartJudgeCompletesPromptly(t *testing.T) {
	song := largestRealSong(t)
	h, fixture := judgeFixture(song)
	primeInputDigest(fixture)
	runs := make([]*run, 100)
	for index := range runs {
		runs[index] = &run{
			teamID:           string(rune(index + 1)),
			frames:           fixture.frames,
			inputDigestValue: fixture.inputDigestValue,
			appliedMods:      fixture.appliedMods,
		}
	}

	started := time.Now()
	for _, candidate := range runs {
		result := judge(h, candidate)
		if result.HitCount != len(song.Notes) || result.RawBasis != 1_000_000 {
			t.Fatalf("largest-chart run judged incorrectly: %+v", result)
		}
	}
	elapsed := time.Since(started)
	if elapsed > 2*time.Second {
		t.Fatalf("100-team largest-chart judge took %s", elapsed)
	}
	t.Logf("100-team largest-chart judge: notes=%d frames/team=%d elapsed=%s", len(song.Notes), len(fixture.frames), elapsed)
}

func TestHundredTeamFullWaveLifecycle(t *testing.T) {
	game := newTestArena(t, 909)
	started := time.Now()
	now := time.Now().UTC()
	initial := game.View(now)
	sessions := make([]Session, 100)
	enrollments := make([]EnrollResponse, len(sessions))
	for index := range sessions {
		session, err := game.CreateLocalSession(
			"scale-team-"+strconv.Itoa(index),
			"Scale Crew "+strconv.Itoa(index),
		)
		if err != nil {
			t.Fatal(err)
		}
		sessions[index] = session
		enrollments[index] = mustEnroll(t, game, session, now)
	}

	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	game.mu.Lock()
	notes := append([]Note(nil), game.heat.notes...)
	game.mu.Unlock()
	for index, session := range sessions {
		if _, err := game.Ready(
			session.Token,
			enrollments[index].HeatTicketID,
			testEligibleEnvelope(revealed.Heat.Selection.Nonce),
			revealAt,
		); err != nil {
			t.Fatalf("team %d ready: %v", index, err)
		}
	}
	for index, session := range sessions {
		playPerfect(
			t,
			game,
			session.Token,
			enrollments[index].HeatTicketID,
			revealed.Heat.Selection.Ruleset,
			notes,
			time.UnixMilli(revealed.Heat.StartAt),
			1500,
		)
	}

	submissionAt := time.UnixMilli(revealed.Heat.EndAt + 1)
	claim := ModClaim{Acronym: "HT", RateMilli: 750, SettingsHash: defaultSettingsHash}
	for index, session := range sessions {
		result, _, err := game.Result(session.Token, enrollments[index].HeatTicketID, submissionAt)
		if err != nil {
			t.Fatalf("team %d result: %v", index, err)
		}
		response, err := game.SubmitManifest(
			session.Token,
			testManifest(t, game, session, enrollments[index], revealed, result, claim),
			submissionAt,
		)
		if err != nil {
			t.Fatalf("team %d manifest: %v", index, err)
		}
		if !response.Settlement.Eligible {
			t.Fatalf("team %d settlement was ineligible: %+v", index, response.Settlement)
		}
	}

	finalAt := time.UnixMilli(revealed.Heat.SubmissionEnds + 1)
	final := game.View(finalAt)
	if final.CrownOwnerID != "" {
		t.Fatalf("100-way exact tie awarded Crown to %q", final.CrownOwnerID)
	}
	waves := game.ScoringWindow(time.UnixMilli(initial.ServerNowUnixMS-1), finalAt.Add(time.Second), finalAt)
	resultCount := 0
	if len(waves.Waves) == 1 {
		resultCount = len(waves.Waves[0].Results)
	}
	if len(waves.Waves) != 1 || resultCount != len(sessions) {
		t.Fatalf("full-wave ledger contains %d waves and %d results", len(waves.Waves), resultCount)
	}
	for _, result := range waves.Waves[0].Results {
		if result.IsCrown || result.Score <= 0 {
			t.Fatalf("invalid tied result: %+v", result)
		}
	}
	t.Logf("100-team full wave completed in %s", time.Since(started))
}

func BenchmarkJudgeHundredLargestRuns(b *testing.B) {
	song := largestRealSong(b)
	h, fixture := judgeFixture(song)
	primeInputDigest(fixture)
	runs := make([]*run, 100)
	for index := range runs {
		runs[index] = &run{
			frames: fixture.frames, inputDigestValue: fixture.inputDigestValue, appliedMods: fixture.appliedMods,
		}
	}
	b.ReportAllocs()
	b.ResetTimer()
	for iteration := 0; iteration < b.N; iteration++ {
		for _, candidate := range runs {
			_ = judge(h, candidate)
		}
	}
}

func primeInputDigest(candidate *run) {
	candidate.inputHash = sha256.New()
	writeInputDigest(candidate.inputHash, candidate.frames)
	candidate.inputDigestValue = runInputDigest(candidate)
}

func largestRealSong(tb testing.TB) Song {
	tb.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "shared", "pool.json"))
	if err != nil {
		tb.Fatal(err)
	}
	var pool Pool
	if err := json.Unmarshal(raw, &pool); err != nil {
		tb.Fatal(err)
	}
	var largest Song
	for _, song := range pool.Songs {
		if len(song.Notes) > len(largest.Notes) {
			largest = song
		}
	}
	if len(largest.Notes) == 0 {
		tb.Fatal("real pool contains no notes")
	}
	return largest
}

func judgeFixture(song Song) (*heat, *run) {
	start := time.Unix(1_800_000_000, 0).UTC()
	h := &heat{
		startAt: start,
		notes:   append([]Note(nil), song.Notes...),
		selection: Selection{
			Ruleset: song.Ruleset,
		},
	}
	frames := make([]StampedFrame, 0, len(song.Notes)*2)
	sequence := uint64(0)
	for noteIndex, note := range song.Notes {
		sequence++
		x, y := note.X, note.Y
		down := InputFrame{
			Sequence:   sequence,
			Ruleset:    song.Ruleset,
			NoteIndex:  noteIndex,
			CueToken:   "benchmark-cue",
			ActionBits: note.Action,
		}
		if song.Ruleset == RulesetOsu {
			down.PointerX = &x
			down.PointerY = &y
		}
		frames = append(frames, StampedFrame{InputFrame: down, ReceivedAt: start.Add(time.Duration(note.AtMS) * time.Millisecond)})
		sequence++
		up := down
		up.Sequence = sequence
		up.ActionBits = 0
		frames = append(frames, StampedFrame{InputFrame: up, ReceivedAt: start.Add(time.Duration(note.AtMS+1) * time.Millisecond)})
	}
	return h, &run{
		frames: frames,
		appliedMods: &AppliedModProfile{
			Acronym: "NM", RateMilli: 1000, SettingsHash: defaultSettingsHash, ScoreMultiplierMilli: 1000,
		},
	}
}

func legacyJudgeForRegression(h *heat, run *run) JudgeResult {
	type edge struct {
		atMS      int64
		noteIndex int
		action    uint16
		x         uint16
		y         uint16
		used      bool
	}

	edges := make([]edge, 0, len(run.frames))
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
		item := edge{
			atMS:      int64(math.Round(float64(frame.ReceivedAt.Sub(h.startAt).Milliseconds()) * float64(rateMilli) / 1000)),
			noteIndex: frame.NoteIndex,
			action:    rising,
		}
		if frame.PointerX != nil {
			item.x = *frame.PointerX
			item.y = *frame.PointerY
		}
		edges = append(edges, item)
	}

	var raw int64
	hits := 0
	combo := 0
	maxCombo := 0
	for noteIndex, note := range h.notes {
		best := -1
		bestDelta := judgementWindowMS + 1
		for index := range edges {
			candidate := &edges[index]
			if candidate.used || candidate.noteIndex != noteIndex || candidate.action&note.Action == 0 {
				continue
			}
			delta := abs64(candidate.atMS - note.AtMS)
			if delta > judgementWindowMS || delta >= bestDelta {
				continue
			}
			if h.selection.Ruleset == RulesetOsu {
				dx := int64(candidate.x) - int64(note.X)
				dy := int64(candidate.y) - int64(note.Y)
				if dx*dx+dy*dy > osuPointerToleranceU16*osuPointerToleranceU16 {
					continue
				}
			}
			best = index
			bestDelta = delta
		}
		if best < 0 {
			combo = 0
			continue
		}
		edges[best].used = true
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
		HitCount: hits, NoteCount: len(h.notes), TotalScoreWithoutMods: raw,
		PerfectRaw: perfect, RawBasis: rawBasis, Accuracy: accuracy,
		MaxCombo: maxCombo, InputDigest: inputDigest(run.frames),
	}
}
