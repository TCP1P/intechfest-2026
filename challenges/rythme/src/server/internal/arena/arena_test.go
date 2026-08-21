package arena

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

func TestEmptyFinalizedWaveKeepsArrayWireShape(t *testing.T) {
	game := newTestArena(t, 72)
	initial := game.View(time.Now().UTC())
	finalAt := time.UnixMilli(initial.Heat.SubmissionEnds + 1)
	game.View(finalAt)

	waves := game.ScoringWindow(
		time.UnixMilli(initial.ServerNowUnixMS-1),
		finalAt.Add(time.Second),
		finalAt,
	)
	if len(waves.Waves) != 1 || waves.Waves[0].Results == nil || len(waves.Waves[0].Results) != 0 {
		t.Fatalf("empty finalized wave lost its array shape: %+v", waves)
	}
	raw, err := json.Marshal(waves)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"results":[]`) {
		t.Fatalf("empty finalized wave changed wire shape: %s", raw)
	}
}

func TestWaveCrownRequiresOneUniquePositiveLeader(t *testing.T) {
	candidates := []scoringCandidate{
		{teamID: "one", score: 100},
		{teamID: "two", score: 100},
	}
	if got := selectWaveCrown(candidates); got != "" {
		t.Fatalf("tied wave awarded Crown to %q", got)
	}
	if got := selectWaveCrown([]scoringCandidate{{teamID: "winner", score: 101}, {teamID: "other", score: 100}}); got != "winner" {
		t.Fatalf("unique leader did not take Crown: %q", got)
	}
	if got := selectWaveCrown([]scoringCandidate{{teamID: "zero", score: 0}}); got != "" {
		t.Fatalf("zero score retained Crown as %q", got)
	}
}

func TestEveryCompletedHeatProducesFreshCrownEvidence(t *testing.T) {
	game := newTestArena(t, 73)
	session, err := game.CreateLocalSession("attacker-team", "Patch Crew")
	if err != nil {
		t.Fatal(err)
	}

	now := time.Now().UTC()
	settlement, final := completeEligibleLeg(t, game, session, now)
	if !settlement.Eligible || settlement.Judge.RawBasis != 1_000_000 {
		t.Fatalf("heat did not qualify: %+v", settlement)
	}
	if final.CrownOwnerID != session.TeamID {
		t.Fatalf("expected heat winner %q, got %q", session.TeamID, final.CrownOwnerID)
	}
	waves := game.ScoringWindow(
		time.UnixMilli(final.Heat.StartAt-1),
		time.UnixMilli(final.Heat.NextHeatAt+1),
		time.UnixMilli(final.ServerNowUnixMS),
	)
	if len(waves.Waves) != 1 || len(waves.Waves[0].Results) != 1 {
		t.Fatalf("unexpected finalized scoring ledger: %+v", waves)
	}
	result := waves.Waves[0].Results[0]
	if result.TeamID != session.TeamID || !result.IsCrown || result.Score != settlement.SettledScore {
		t.Fatalf("unexpected scoring result: %+v", result)
	}
}

func TestExactFreshWaveTieClearsTheCrown(t *testing.T) {
	game := newTestArena(t, 91)
	incumbent, _ := game.CreateLocalSession("incumbent-team", "Incumbent Crew")
	challenger, _ := game.CreateLocalSession("challenger-team", "Challenger Crew")
	now := time.Now().UTC()
	_, first := completeEligibleLeg(t, game, incumbent, now)
	if first.CrownOwnerID != incumbent.TeamID {
		t.Fatalf("initial Crown owner was %q", first.CrownOwnerID)
	}

	now = time.UnixMilli(first.Heat.NextHeatAt + 1)
	view := game.View(now)
	incumbentEnrollment := mustEnroll(t, game, incumbent, time.UnixMilli(view.ServerNowUnixMS))
	challengerEnrollment := mustEnroll(t, game, challenger, time.UnixMilli(view.ServerNowUnixMS))
	revealAt := time.UnixMilli(view.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	mustReadyAndPlay(t, game, incumbent, incumbentEnrollment, revealed)
	mustReadyAndPlay(t, game, challenger, challengerEnrollment, revealed)
	submitEligibleManifest(t, game, incumbent, incumbentEnrollment, revealed)
	submitEligibleManifest(t, game, challenger, challengerEnrollment, revealed)
	finalAt := time.UnixMilli(revealed.Heat.SubmissionEnds + 1)
	final := game.View(finalAt)
	if final.CrownOwnerID != "" {
		t.Fatalf("exact tie awarded a Crown: crown=%q", final.CrownOwnerID)
	}
	waves := game.ScoringWindow(time.UnixMilli(first.ServerNowUnixMS), finalAt.Add(time.Second), finalAt)
	latest := waves.Waves[len(waves.Waves)-1]
	if latest.CrownOwnerID != "" || latest.CrownOwnerName != "" || len(latest.Results) != 2 {
		t.Fatalf("unexpected tied wave evidence: %+v", latest)
	}
	for _, result := range latest.Results {
		if result.IsCrown {
			t.Fatalf("tied result was marked as Crown: %+v", result)
		}
	}
}

func TestCompletedRawScoreCountsWithoutExploitEligibility(t *testing.T) {
	game := newTestArena(t, 127)
	session, _ := game.CreateLocalSession("legit-team", "Legit Crew")
	initial := game.View(time.Now().UTC())
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	if _, err := game.Ready(session.Token, enrollment.HeatTicketID, testEnvelope(revealed.Heat.Selection.Nonce, nil), revealAt); err != nil {
		t.Fatal(err)
	}
	playPerfect(t, game, session.Token, enrollment.HeatTicketID, revealed.Heat.Selection.Ruleset, game.heat.notes, time.UnixMilli(revealed.Heat.StartAt), 1000)

	submissionAt := time.UnixMilli(revealed.Heat.EndAt + 1)
	judgeResult, _, err := game.Result(session.Token, enrollment.HeatTicketID, submissionAt)
	if err != nil {
		t.Fatal(err)
	}
	if judgeResult.RawBasis != 1_000_000 {
		t.Fatalf("perfect run judged as %+v", judgeResult)
	}
	response, err := game.SubmitManifest(session.Token, testManifest(t, game, session, enrollment, revealed, judgeResult, ModClaim{
		Acronym: "NM", RateMilli: 1000, SettingsHash: defaultSettingsHash,
	}), submissionAt)
	if err != nil {
		t.Fatal(err)
	}
	if response.Settlement.Eligible || response.Settlement.SettledScore != 1_000_000 {
		t.Fatalf("plain one-million run qualified: %+v", response.Settlement)
	}
	finalAt := time.UnixMilli(revealed.Heat.SubmissionEnds + 1)
	final := game.View(finalAt)
	if final.CrownOwnerID != session.TeamID {
		t.Fatalf("non-exploit completed performance did not take uncontested Crown: %+v", final)
	}
	waves := game.ScoringWindow(submissionAt.Add(-time.Second), finalAt.Add(time.Second), finalAt)
	if len(waves.Waves) != 1 || waves.Waves[0].Results[0].Score != 1_000_000 {
		t.Fatalf("non-exploit score was not recorded for relative scoring: %+v", waves)
	}
}

func TestFourModeRotationCompletionDoesNotRequireExploitBoost(t *testing.T) {
	progress := &teamSeries{legs: map[int]Settlement{
		1: {Eligible: false, SettledScore: 100},
		2: {Eligible: true, SettledScore: 200},
		3: {Eligible: false, SettledScore: 300},
		4: {Eligible: false, SettledScore: 400},
	}}
	if !completeSeries(progress) {
		t.Fatal("four completed scoring waves were treated as an exploit-qualification gate")
	}
	delete(progress.legs, 4)
	if completeSeries(progress) {
		t.Fatal("an incomplete four-mode rotation was reported as complete")
	}
}

func TestRunSealsAtItsOwnPlaybackDeadlineWithoutLeakingEarlyScore(t *testing.T) {
	game := newTestArena(t, 128)
	fast, _ := game.CreateLocalSession("fast-team", "Fast Crew")
	slow, _ := game.CreateLocalSession("slow-team", "Slow Crew")
	initial := game.View(time.Now().UTC())
	fastEnrollment := mustEnroll(t, game, fast, time.UnixMilli(initial.ServerNowUnixMS))
	slowEnrollment := mustEnroll(t, game, slow, time.UnixMilli(initial.ServerNowUnixMS))
	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)

	fastEnvelope := testEligibleEnvelope(revealed.Heat.Selection.Nonce)
	fastReady, err := game.Ready(fast.Token, fastEnrollment.HeatTicketID, fastEnvelope, revealAt)
	if err != nil {
		t.Fatal(err)
	}
	if fastReady.LockedMods.RateMilli != 1500 {
		t.Fatalf("fast run locked at %d, want 1500", fastReady.LockedMods.RateMilli)
	}
	slowReady, err := game.Ready(
		slow.Token,
		slowEnrollment.HeatTicketID,
		testEnvelope(revealed.Heat.Selection.Nonce, []string{"HT"}),
		revealAt,
	)
	if err != nil {
		t.Fatal(err)
	}
	if slowReady.LockedMods.RateMilli != 750 {
		t.Fatalf("slow run locked at %d, want 750", slowReady.LockedMods.RateMilli)
	}

	game.mu.Lock()
	notes := append([]Note(nil), game.heat.notes...)
	game.mu.Unlock()
	playPerfect(
		t,
		game,
		fast.Token,
		fastEnrollment.HeatTicketID,
		revealed.Heat.Selection.Ruleset,
		notes,
		time.UnixMilli(revealed.Heat.StartAt),
		1500,
	)

	game.mu.Lock()
	fastRun := game.heat.runs[fast.TeamID]
	fastDeadline := game.runInputClosesAtLocked(fastRun)
	globalDeadline := game.heat.endAt
	game.mu.Unlock()
	if !fastDeadline.Before(globalDeadline) {
		t.Fatalf("fast deadline %s did not precede global deadline %s", fastDeadline, globalDeadline)
	}
	if _, _, err := game.Result(fast.Token, fastEnrollment.HeatTicketID, fastDeadline.Add(-time.Millisecond)); !errors.Is(err, ErrWrongPhase) {
		t.Fatalf("result before the per-run deadline returned %v", err)
	}

	result, _, err := game.Result(fast.Token, fastEnrollment.HeatTicketID, fastDeadline)
	if err != nil {
		t.Fatalf("result at the per-run deadline failed: %v", err)
	}
	if result.RawBasis != 1_000_000 {
		t.Fatalf("early-sealed perfect run was judged as %+v", result)
	}
	if phase := game.View(fastDeadline).Heat.Phase; phase != PhaseRunning {
		t.Fatalf("fast run only sealed in phase %q, want the shared running phase", phase)
	}
	game.mu.Lock()
	slowWasSealed := game.heat.runs[slow.TeamID].judge != nil
	game.mu.Unlock()
	if slowWasSealed {
		t.Fatal("sealing the fast run also sealed the unfinished slow run")
	}

	if _, err := game.Cues(fast.Token, fastEnrollment.HeatTicketID, fastDeadline); !errors.Is(err, ErrConflict) || !strings.Contains(err.Error(), "transcript is already sealed") {
		t.Fatalf("post-seal cues returned %v", err)
	}
	lateFrame := testFrame(
		fastEnrollment.HeatTicketID,
		revealed.Heat.Selection.Ruleset,
		notes[0],
		0,
		10_000,
		"late",
	)
	if _, err := game.SubmitFrames(fast.Token, []InputFrame{lateFrame}, fastDeadline); !errors.Is(err, ErrConflict) || !strings.Contains(err.Error(), "transcript is already sealed") {
		t.Fatalf("post-seal input returned %v", err)
	}

	decodedEnvelope, err := decodeModifierEnvelope(fastEnvelope)
	if err != nil {
		t.Fatal(err)
	}
	game.mu.Lock()
	program := append([]byte(nil), game.heat.policyProgram...)
	game.mu.Unlock()
	claim, err := executePolicyVM(revealed.Heat.Selection.Ruleset, decodedEnvelope, program)
	if err != nil {
		t.Fatal(err)
	}
	response, err := game.SubmitManifest(
		fast.Token,
		testManifest(t, game, fast, fastEnrollment, revealed, result, claim),
		fastDeadline,
	)
	if err != nil {
		t.Fatalf("completion during the shared running phase failed: %v", err)
	}

	runningView := game.View(fastDeadline)
	fastView := performerByTeam(t, runningView, fast.TeamID)
	if fastView.ManifestSent || fastView.RawBasis != 0 || fastView.SettledScore != 0 || fastView.Eligible {
		t.Fatalf("early result leaked during shared play: %+v", fastView)
	}

	submissionView := game.View(globalDeadline.Add(time.Millisecond))
	fastView = performerByTeam(t, submissionView, fast.TeamID)
	if !fastView.ManifestSent || fastView.RawBasis != result.RawBasis || fastView.SettledScore != response.Settlement.SettledScore {
		t.Fatalf("sealed result was not published in submission: %+v", fastView)
	}
}

func TestLiveCueTokenIsRequiredAndExpires(t *testing.T) {
	game := newTestArena(t, 144)
	session, _ := game.CreateLocalSession("cue-team", "Cue Crew")
	initial := game.View(time.Now().UTC())
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	if _, err := game.Ready(session.Token, enrollment.HeatTicketID, testEnvelope(revealed.Heat.Selection.Nonce, nil), revealAt); err != nil {
		t.Fatal(err)
	}

	note := game.heat.notes[0]
	target := time.UnixMilli(revealed.Heat.StartAt + note.AtMS)
	cues, err := game.Cues(session.Token, enrollment.HeatTicketID, target)
	if err != nil {
		t.Fatal(err)
	}
	cue, ok := cueByIndex(cues, 0)
	if !ok {
		t.Fatal("live cue was not available at the note target")
	}

	frame := testFrame(enrollment.HeatTicketID, revealed.Heat.Selection.Ruleset, note, 0, 1, "wrong")
	if _, err := game.SubmitFrames(session.Token, []InputFrame{frame}, target); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("bad cue returned %v", err)
	}
	frame.CueToken = cue.Token
	if _, err := game.SubmitFrames(session.Token, []InputFrame{frame}, target); err != nil {
		t.Fatalf("valid cue was rejected: %v", err)
	}

	second := game.heat.notes[1]
	secondTarget := time.UnixMilli(revealed.Heat.StartAt + second.AtMS)
	secondCues, err := game.Cues(session.Token, enrollment.HeatTicketID, secondTarget)
	if err != nil {
		t.Fatal(err)
	}
	secondCue, ok := cueByIndex(secondCues, 1)
	if !ok {
		t.Fatal("second live cue was not available")
	}
	expired := testFrame(enrollment.HeatTicketID, revealed.Heat.Selection.Ruleset, second, 1, 2, secondCue.Token)
	if _, err := game.SubmitFrames(session.Token, []InputFrame{expired}, time.UnixMilli(secondCue.ExpiresUnixMS+1)); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("expired cue returned %v", err)
	}
}

func TestRejectedInputBatchIsAtomic(t *testing.T) {
	game := newTestArena(t, 314)
	session, err := game.CreateLocalSession("atomic-team", "Atomic Crew")
	if err != nil {
		t.Fatal(err)
	}
	initial := game.View(time.Now().UTC())
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	if _, err := game.Ready(session.Token, enrollment.HeatTicketID, testEnvelope(revealed.Heat.Selection.Nonce, nil), revealAt); err != nil {
		t.Fatal(err)
	}

	game.mu.Lock()
	run := game.heat.runs[session.TeamID]
	note := game.heat.notes[0]
	opens, expires := game.cueWindowLocked(run, note)
	valid := InputFrame{
		HeatTicketID: enrollment.HeatTicketID,
		Sequence:     1,
		Ruleset:      game.heat.selection.Ruleset,
		NoteIndex:    0,
		CueToken:     game.cueTokenLocked(run, 0, opens, expires),
		ActionBits:   note.Action,
	}
	if valid.Ruleset == RulesetOsu {
		valid.PointerX = &note.X
		valid.PointerY = &note.Y
	}
	game.mu.Unlock()

	invalid := valid
	invalid.Sequence = 2
	invalid.CueToken = "invalid-cue"
	target := time.UnixMilli(revealed.Heat.StartAt + note.AtMS)
	if _, err := game.SubmitFrames(session.Token, []InputFrame{valid, invalid}, target); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("mixed batch returned %v", err)
	}

	game.mu.Lock()
	if len(run.frames) != 0 || run.lastSequence != 0 || run.noteFrames[0] != 0 {
		frames := len(run.frames)
		sequence := run.lastSequence
		noteFrames := run.noteFrames[0]
		game.mu.Unlock()
		t.Fatalf("rejected batch mutated run: frames=%d sequence=%d note_frames=%d", frames, sequence, noteFrames)
	}
	game.mu.Unlock()

	response, err := game.SubmitFrames(session.Token, []InputFrame{valid}, target)
	if err != nil {
		t.Fatalf("valid retry after rejected batch failed: %v", err)
	}
	if response.Accepted != 1 {
		t.Fatalf("valid retry accepted %d frames", response.Accepted)
	}
}

func TestInputDigestKeepsCanonicalWireFormat(t *testing.T) {
	x := uint16(42)
	frames := []StampedFrame{
		{InputFrame: InputFrame{Sequence: 7, Ruleset: RulesetOsu, NoteIndex: 3, CueToken: "cue-a", ActionBits: 5, PointerX: &x}},
		{InputFrame: InputFrame{Sequence: 8, Ruleset: RulesetMania, NoteIndex: 12, CueToken: "cue-b", ActionBits: 0}},
	}
	canonical := "7|osu|3|cue-a|5|42|-\n8|mania|12|cue-b|0|-|-\n"
	expected := fmt.Sprintf("%x", sha256.Sum256([]byte(canonical)))
	if got := inputDigest(frames); got != expected {
		t.Fatalf("input digest changed: got %s want %s", got, expected)
	}
}

func TestArenaHealthTracksSchedulerHeartbeat(t *testing.T) {
	game := newTestArena(t, 315)
	now := time.Now().UTC()
	game.lastTick.Store(now.Add(-heartbeatTolerance - time.Millisecond).UnixNano())
	if game.Healthy(now) {
		t.Fatal("stale scheduler heartbeat reported healthy")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go game.Run(ctx)
	deadline := time.Now().Add(time.Second)
	for !game.Healthy(time.Now().UTC()) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !game.Healthy(time.Now().UTC()) {
		t.Fatal("running scheduler did not refresh its heartbeat")
	}
}

func TestEventSubscriberCapacityIsBounded(t *testing.T) {
	game := newTestArena(t, 316)
	game.mu.Lock()
	for index := 0; index < maxSubscribers; index++ {
		game.subscribers[make(chan struct{}, 1)] = struct{}{}
	}
	game.mu.Unlock()
	if _, _, err := game.Subscribe(); !errors.Is(err, ErrConflict) {
		t.Fatalf("subscriber overflow returned %v", err)
	}

	game.mu.Lock()
	for ch := range game.subscribers {
		delete(game.subscribers, ch)
		break
	}
	game.mu.Unlock()
	_, cancel, err := game.Subscribe()
	if err != nil {
		t.Fatalf("subscriber slot did not recover: %v", err)
	}
	cancel()
}

func TestZeroActionTranscriptProducesExplicitIneligibleReceipt(t *testing.T) {
	game := newTestArena(t, 145)
	session, _ := game.CreateLocalSession("zero-team", "Zero Crew")
	initial := game.View(time.Now().UTC())
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealed := game.View(time.UnixMilli(initial.Heat.EnrollmentEnds + 1))
	if _, err := game.Ready(
		session.Token,
		enrollment.HeatTicketID,
		testEnvelope(revealed.Heat.Selection.Nonce, nil),
		time.UnixMilli(revealed.ServerNowUnixMS),
	); err != nil {
		t.Fatal(err)
	}

	note := game.heat.notes[0]
	target := time.UnixMilli(revealed.Heat.StartAt + note.AtMS)
	cues, err := game.Cues(session.Token, enrollment.HeatTicketID, target)
	if err != nil {
		t.Fatal(err)
	}
	cue, ok := cueByIndex(cues, 0)
	if !ok {
		t.Fatal("live cue was not available for the zero-action marker")
	}
	marker := testFrame(
		enrollment.HeatTicketID,
		revealed.Heat.Selection.Ruleset,
		note,
		0,
		1,
		cue.Token,
	)
	marker.ActionBits = 0
	input, err := game.SubmitFrames(session.Token, []InputFrame{marker}, target)
	if err != nil || input.Accepted != 1 {
		t.Fatalf("zero-action marker returned %+v, err=%v", input, err)
	}

	submissionAt := time.UnixMilli(revealed.Heat.EndAt + 1)
	result, _, err := game.Result(session.Token, enrollment.HeatTicketID, submissionAt)
	if err != nil {
		t.Fatal(err)
	}
	if result.HitCount != 0 || result.RawBasis != 0 || result.InputDigest == inputDigest(nil) {
		t.Fatalf("zero-action transcript changed the score or was not recorded: %+v", result)
	}
	response, err := game.SubmitManifest(
		session.Token,
		testManifest(t, game, session, enrollment, revealed, result, ModClaim{
			Acronym: "NM", RateMilli: 1000, SettingsHash: defaultSettingsHash,
		}),
		submissionAt,
	)
	if err != nil {
		t.Fatal(err)
	}
	if response.Settlement.Eligible || response.Settlement.SettledScore != 0 ||
		response.Settlement.Reason != "raw performance floor was not reached" {
		t.Fatalf("zero-action transcript did not produce an explicit zero receipt: %+v", response.Settlement)
	}
}

func TestIssuedPolicyViewsRemainWithinSupportedClaims(t *testing.T) {
	for _, ruleset := range AllRulesets {
		t.Run(string(ruleset), func(t *testing.T) {
			nonce := "f4e2c1a0d8b69785"
			spec := derivePolicySpec(nonce)
			raw, err := decodeModifierEnvelope(testEligibleEnvelope(nonce))
			if err != nil {
				t.Fatal(err)
			}
			locked, err := readyModifierProfile(raw, spec.program)
			if err != nil || len(locked) != 1 || locked[0] != "DT" {
				t.Fatalf("ready parser returned %v, err=%v", locked, err)
			}
			claim, err := executePolicyVM(ruleset, raw, spec.program)
			if err != nil || claim.Acronym != "HT" || claim.RateMilli != 750 {
				t.Fatalf("policy VM returned %+v, err=%v", claim, err)
			}

			freeForm, err := decodeModifierEnvelope(testEnvelope(nonce, []string{"TP"}))
			if err != nil {
				t.Fatal(err)
			}
			if _, err := executePolicyVM(ruleset, freeForm, spec.program); err == nil {
				t.Fatal("policy VM accepted a free-form low multiplier claim")
			}
		})
	}
}

func TestManifestRequiresExactServerCapsulesAndNativeProof(t *testing.T) {
	game := newTestArena(t, 2027)
	session, _ := game.CreateLocalSession("proof-team", "Proof Crew")
	initial := game.View(time.Now().UTC())
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealed := game.View(time.UnixMilli(initial.Heat.EnrollmentEnds + 1))
	mustReadyAndPlay(t, game, session, enrollment, revealed)
	submissionAt := time.UnixMilli(revealed.Heat.EndAt + 1)
	result, _, err := game.Result(session.Token, enrollment.HeatTicketID, submissionAt)
	if err != nil {
		t.Fatal(err)
	}
	claim := ModClaim{Acronym: "HT", RateMilli: 750, SettingsHash: defaultSettingsHash}
	manifest := testManifest(t, game, session, enrollment, revealed, result, claim)

	tamperedCapsule := manifest
	tamperedCapsule.ReadyCapsule = manifest.ReadyCapsule[:len(manifest.ReadyCapsule)-1] + "A"
	if _, err := game.SubmitManifest(session.Token, tamperedCapsule, submissionAt); !errors.Is(err, ErrBadRequest) {
		t.Fatalf("tampered ready capsule returned %v", err)
	}

	tamperedProof := manifest
	proofBytes, err := hex.DecodeString(manifest.NativeProof)
	if err != nil {
		t.Fatal(err)
	}
	proofBytes[0] ^= 0x80
	tamperedProof.NativeProof = hex.EncodeToString(proofBytes)
	if _, err := game.SubmitManifest(session.Token, tamperedProof, submissionAt); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("tampered native proof returned %v", err)
	}

	if _, err := game.SubmitManifest(session.Token, manifest, submissionAt); err != nil {
		t.Fatalf("valid capsule-bound manifest failed: %v", err)
	}
}

func TestReadyOnlyAfterSharedRevealAndOnePerformerPerTeam(t *testing.T) {
	game := newTestArena(t, 18)
	now := time.Now().UTC()
	first, _ := game.CreateLocalSession("shared-team", "First")
	second, _ := game.CreateLocalSession("shared-team", "Second")
	enrollment := mustEnroll(t, game, first, now)
	if _, err := game.Enroll(second.Token, now); !errors.Is(err, ErrConflict) {
		t.Fatalf("second performer returned %v", err)
	}
	if _, err := game.Ready(first.Token, enrollment.HeatTicketID, testEnvelope(game.heat.selection.Nonce, nil), now); !errors.Is(err, ErrWrongPhase) {
		t.Fatalf("early ready returned %v", err)
	}
	revealAt := time.UnixMilli(game.View(now).Heat.EnrollmentEnds + 1)
	if _, err := game.Ready(first.Token, enrollment.HeatTicketID, testEnvelope(game.heat.selection.Nonce, []string{"DT"}), revealAt); err != nil {
		t.Fatalf("ready after reveal failed: %v", err)
	}
}

func TestBalancedRulesetBagHasNoBoundaryRepeat(t *testing.T) {
	game := newTestArena(t, 2026)
	now := time.Now().UTC()
	seen := make([]Ruleset, 0, 12)
	for index := 0; index < 12; index++ {
		view := game.View(now)
		revealed := game.View(time.UnixMilli(view.Heat.EnrollmentEnds + 1))
		seen = append(seen, revealed.Heat.Selection.Ruleset)
		now = time.UnixMilli(view.Heat.NextHeatAt + 1)
	}
	for start := 0; start < len(seen); start += 4 {
		counts := map[Ruleset]int{}
		for _, ruleset := range seen[start : start+4] {
			counts[ruleset]++
		}
		for _, ruleset := range AllRulesets {
			if counts[ruleset] != 1 {
				t.Fatalf("series modes %v did not contain exactly one %s", seen[start:start+4], ruleset)
			}
		}
		if start > 0 && seen[start] == seen[start-1] {
			t.Fatalf("bag boundary repeated %s in %v", seen[start], seen)
		}
	}
}

func TestPoolRejectsGeneratedOrEasyCharts(t *testing.T) {
	pool := testPool()
	pool.Schema = "rythme-pool-v1"
	if err := ValidatePool(pool); err == nil {
		t.Fatal("legacy generated pool schema was accepted")
	}
	pool = testPool()
	pool.Songs[0].StarRating = 3.49
	if err := ValidatePool(pool); err == nil {
		t.Fatal("easy chart was accepted")
	}
	pool = testPool()
	pool.Songs[0].Notes = pool.Songs[0].Notes[:99]
	pool.Songs[0].NoteCount = 99
	if err := ValidatePool(pool); err == nil {
		t.Fatal("undersized chart was accepted")
	}
}

func completeEligibleLeg(t *testing.T, game *Arena, session Session, now time.Time) (Settlement, ArenaView) {
	t.Helper()
	initial := game.View(now)
	enrollment := mustEnroll(t, game, session, time.UnixMilli(initial.ServerNowUnixMS))
	revealAt := time.UnixMilli(initial.Heat.EnrollmentEnds + 1)
	revealed := game.View(revealAt)
	mustReadyAndPlay(t, game, session, enrollment, revealed)
	settlement := submitEligibleManifest(t, game, session, enrollment, revealed)
	final := game.View(time.UnixMilli(revealed.Heat.SubmissionEnds + 1))
	return settlement, final
}

func mustEnroll(t *testing.T, game *Arena, session Session, now time.Time) EnrollResponse {
	t.Helper()
	enrollment, err := game.Enroll(session.Token, now)
	if err != nil {
		t.Fatal(err)
	}
	return enrollment
}

func mustReadyAndPlay(t *testing.T, game *Arena, session Session, enrollment EnrollResponse, revealed ArenaView) {
	t.Helper()
	if _, err := game.Ready(
		session.Token,
		enrollment.HeatTicketID,
		testEligibleEnvelope(revealed.Heat.Selection.Nonce),
		time.UnixMilli(revealed.ServerNowUnixMS),
	); err != nil {
		t.Fatal(err)
	}
	game.mu.Lock()
	notes := append([]Note(nil), game.heat.notes...)
	game.mu.Unlock()
	playPerfect(t, game, session.Token, enrollment.HeatTicketID, revealed.Heat.Selection.Ruleset, notes, time.UnixMilli(revealed.Heat.StartAt), 1500)
}

func submitEligibleManifest(t *testing.T, game *Arena, session Session, enrollment EnrollResponse, revealed ArenaView) Settlement {
	t.Helper()
	submissionAt := time.UnixMilli(revealed.Heat.EndAt + 1)
	result, _, err := game.Result(session.Token, enrollment.HeatTicketID, submissionAt)
	if err != nil {
		t.Fatal(err)
	}
	claim := ModClaim{Acronym: "HT", RateMilli: 750, SettingsHash: defaultSettingsHash}
	response, err := game.SubmitManifest(
		session.Token,
		testManifest(t, game, session, enrollment, revealed, result, claim),
		submissionAt,
	)
	if err != nil {
		t.Fatal(err)
	}
	if !response.Settlement.Eligible {
		t.Fatalf("expected eligible settlement: %+v", response.Settlement)
	}
	return response.Settlement
}

func playPerfect(t *testing.T, game *Arena, token, ticket string, ruleset Ruleset, notes []Note, startAt time.Time, rateMilli int64) {
	t.Helper()
	var sequence uint64
	for noteIndex, note := range notes {
		wallAtMS := note.AtMS * 1000 / rateMilli
		target := startAt.Add(time.Duration(wallAtMS) * time.Millisecond)

		game.mu.Lock()
		currentRun, err := game.authorizedRunLocked(token, ticket)
		if err != nil {
			game.mu.Unlock()
			t.Fatal(err)
		}
		opens, expires := game.cueWindowLocked(currentRun, note)
		cue := game.cueTokenLocked(currentRun, noteIndex, opens, expires)
		game.mu.Unlock()

		sequence++
		down := testFrame(ticket, ruleset, note, noteIndex, sequence, cue)
		if _, err := game.SubmitFrames(token, []InputFrame{down}, target); err != nil {
			t.Fatalf("note %d down: %v", noteIndex, err)
		}
		sequence++
		up := down
		up.Sequence = sequence
		up.ActionBits = 0
		if _, err := game.SubmitFrames(token, []InputFrame{up}, target.Add(time.Millisecond)); err != nil {
			t.Fatalf("note %d up: %v", noteIndex, err)
		}
	}
}

func testFrame(ticket string, ruleset Ruleset, note Note, noteIndex int, sequence uint64, cue string) InputFrame {
	frame := InputFrame{
		HeatTicketID: ticket,
		Sequence:     sequence,
		Ruleset:      ruleset,
		NoteIndex:    noteIndex,
		CueToken:     cue,
		ActionBits:   note.Action,
	}
	if ruleset == RulesetOsu {
		x, y := note.X, note.Y
		frame.PointerX, frame.PointerY = &x, &y
	}
	return frame
}

func cueByIndex(response CueResponse, noteIndex int) (Cue, bool) {
	for _, cue := range response.Cues {
		if cue.NoteIndex == noteIndex {
			return cue, true
		}
	}
	return Cue{}, false
}

func performerByTeam(t *testing.T, view ArenaView, teamID string) PerformerView {
	t.Helper()
	for _, performer := range view.Heat.Performers {
		if performer.TeamID == teamID {
			return performer
		}
	}
	t.Fatalf("performer %q is missing", teamID)
	return PerformerView{}
}

func testManifest(
	t *testing.T,
	game *Arena,
	session Session,
	enrollment EnrollResponse,
	revealed ArenaView,
	judgeResult JudgeResult,
	claim ModClaim,
) RunManifest {
	t.Helper()
	game.mu.Lock()
	currentRun := game.heat.runs[session.TeamID]
	if currentRun == nil {
		game.mu.Unlock()
		t.Fatal("test run is unavailable")
	}
	readyCapsule := currentRun.readyCapsule
	game.mu.Unlock()

	_, readyRaw, err := openReadyCapsule(readyCapsule)
	if err != nil {
		t.Fatal(err)
	}
	_, judgeRaw, err := openJudgeCapsule(judgeResult.JudgeCapsule)
	if err != nil {
		t.Fatal(err)
	}
	return RunManifest{
		Schema: manifestSchema, TeamID: session.TeamID, HeatID: enrollment.HeatID,
		HeatTicketID: enrollment.HeatTicketID, SongID: revealed.Heat.Selection.SongID,
		Ruleset: revealed.Heat.Selection.Ruleset, ChartDigest: revealed.Heat.Selection.ChartDigest,
		StartAtUnixMS: revealed.Heat.StartAt, InputDigest: judgeResult.InputDigest,
		AttestedMods: claim, ClientBuild: clientBuild,
		ReadyCapsule: readyCapsule, JudgeCapsule: judgeResult.JudgeCapsule,
		NativeProof: hex.EncodeToString(nativeProof(readyRaw, judgeRaw, claim)),
	}
}

func testEligibleEnvelope(nonce string) string {
	spec := derivePolicySpec(nonce)
	profiles := [][]string{nil, nil, nil}
	serverIndex := (int(spec.selector) + int(spec.stride)) % len(profiles)
	nativeIndex := (int(int8(spec.selector)) + int(spec.stride)) % len(profiles)
	if nativeIndex < 0 {
		nativeIndex += len(profiles)
	}
	profiles[serverIndex] = []string{"DT"}
	profiles[nativeIndex] = []string{"HT"}
	return testEnvelope(nonce, profiles...)
}

func testEnvelope(nonce string, profiles ...[]string) string {
	if len(profiles) == 0 {
		profiles = [][]string{nil}
	}
	spec := derivePolicySpec(nonce)
	raw := append([]byte(nil), spec.magic[:]...)
	raw = append(raw, modifierEnvelopeVersion, byte(len(profiles)))
	for _, profile := range profiles {
		value := []byte{byte(len(profile))}
		for _, acronym := range profile {
			value = append(value, byte(len(acronym)))
			value = append(value, []byte(acronym)...)
		}
		entry := []byte{spec.tag, 0, 0}
		binary.LittleEndian.PutUint16(entry[1:], uint16(len(value)))
		raw = append(raw, entry...)
		raw = append(raw, value...)
	}
	return base64.StdEncoding.EncodeToString(raw)
}

func newTestArena(t *testing.T, seed int64) *Arena {
	t.Helper()
	game, err := New(Config{
		EnrollmentDuration: time.Second,
		ReadyDuration:      time.Second,
		SubmissionDuration: time.Second,
		ResultsDuration:    time.Second,
		CrownThreshold:     2_100_000,
		RawScoreFloor:      700_000,
		Seed:               seed,
	}, testPool())
	if err != nil {
		t.Fatal(err)
	}
	return game
}

func testPool() Pool {
	pool := Pool{Schema: "rythme-pool-v2", Source: "deterministic unit-test fixtures"}
	mapIndex := int64(0)
	for _, ruleset := range AllRulesets {
		for difficulty := 0; difficulty < 4; difficulty++ {
			mapIndex++
			notes := make([]Note, 100)
			for index := range notes {
				action := uint16(1 << (index % 4))
				if ruleset == RulesetOsu {
					action = uint16(1 << (index % 2))
				} else if ruleset == RulesetFruits {
					action = uint16(1 << (index % 2))
				}
				notes[index] = Note{
					AtMS: int64(200 + index*80), Action: action,
					X: uint16(4000 + index*521), Y: uint16(8000 + index*317),
				}
			}
			id := fmt.Sprintf("test-%s-%d", ruleset, difficulty)
			pool.Songs = append(pool.Songs, Song{
				ID: id, Title: "Hard Test", Artist: "Test Unit", Ruleset: ruleset,
				BPM: 180, DurationMS: 9_000, LeadInMS: 200, NoteCount: len(notes),
				Origin: "test-only", BeatmapsetID: 10_000 + mapIndex, BeatmapID: 20_000 + mapIndex,
				Difficulty: "Expert", StarRating: 5.25, Status: "ranked", KeyCount: map[bool]int{true: 4}[ruleset == RulesetMania],
				Mapper: "Fixture", SourceURL: fmt.Sprintf("https://catboy.best/d/%d", 10_000+mapIndex),
				OfficialURL:   fmt.Sprintf("https://osu.ppy.sh/beatmapsets/%d#%s/%d", 10_000+mapIndex, ruleset, 20_000+mapIndex),
				ArchiveSHA256: strings.Repeat("a", 64), Notes: notes,
			})
		}
	}
	return pool
}
