using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using osu.Framework.Logging;
using osu.Framework.Screens;
using osu.Game.Online.API;
using osu.Game.Online.Rooms;
using osu.Game.Rulesets.Judgements;
using osu.Game.Scoring;
using osu.Game.Screens.Play;
using osu.Game.Screens.Ranking;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal partial class RythmePlayer : SoloPlayer
    {
        private readonly RythmeRunContext context;
        private readonly InputSender input;
        private readonly Dictionary<long, Queue<int>> noteIndexesByTime = new Dictionary<long, Queue<int>>();
        private RythmeMatchCountdown? matchCountdown;
        private RythmeFinalizingOverlay? finalizingOverlay;
        private ArenaGameplayCompletion? gameplayCompletion;
        private bool gameplayClockStarted;
        private readonly object finalizationGate = new object();
        private Task? officialFinalizationTask;
        private int forcedReturnStarted;
        private int missedStartReturnStarted;

        protected override bool PauseOnFocusLost => false;

        // Arena scoring ends with the last judged object. Community beatmaps
        // can contain malformed or overlong storyboards whose clocks never
        // report completion after the audio stops; those visuals must not hold
        // the official result screen hostage.
        protected override bool WaitForStoryboardAtResults => false;

        public RythmePlayer(RythmeRunContext context)
            : base(new PlayerConfiguration
            {
                AllowPause = false,
                AllowRestart = false,
                AllowSkipping = false,
                ShowLeaderboard = false,
                ShowResults = true,
                ShowFailingOverlay = false,
            })
        {
            this.context = context;
            Configuration.ShowLeaderboard = false;
            for (int index = 0; index < context.Notes.Length; index++)
            {
                long atMS = context.Notes[index].AtMs;
                if (!noteIndexesByTime.TryGetValue(atMS, out Queue<int>? indexes))
                    noteIndexesByTime.Add(atMS, indexes = new Queue<int>());
                indexes.Enqueue(index);
            }
            input = new InputSender(
                context.API,
                context.Enrollment.HeatId,
                context.Enrollment.HeatTicketId,
                context.Selection.Ruleset,
                context.ServerOffsetMS,
                context.Lifetime.Token);
        }

        protected override void LoadComplete()
        {
            base.LoadComplete();
            DrawableRuleset.NewResult += onNewResult;
        }

        protected override bool CheckModsAllowFailure() => false;

        // Rythme settlements are submitted through the arena protocol, never
        // through osu!'s score API. SoloPlayer otherwise queues a token request
        // against the intentionally inert DummyAPIAccess and blocks its
        // background load for the full 30-second upstream timeout.
        protected override APIRequest<APIScoreToken> CreateTokenRequest() => null!;

        protected override void StartGameplay()
        {
            long localTarget = context.Heat.StartAtUnixMs - context.ServerOffsetMS;
            gameplayCompletion = new ArenaGameplayCompletion(
                localTarget,
                context.Selection.DurationMs,
                context.AppliedMods.RateMilli);

            AddInternal(matchCountdown = new RythmeMatchCountdown(
                context.Selection.SongTitle,
                context.Selection.Ruleset,
                localTarget));

            attemptSynchronizedStart(localTarget);
        }

        private void attemptSynchronizedStart(long localTargetUnixMs)
        {
            if (gameplayClockStarted)
                return;

            RythmeStartDecision decision = RythmeSynchronizedStart.Decide(
                localTargetUnixMs,
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            if (decision.WaitMilliseconds > 0)
            {
                Scheduler.AddDelayed(
                    () => attemptSynchronizedStart(localTargetUnixMs),
                    decision.WaitMilliseconds);
                return;
            }

            if (decision.Missed)
            {
                returnAfterMissedStart(decision.LatenessMilliseconds);
                return;
            }

            gameplayClockStarted = true;
            matchCountdown?.Complete();
            GameplayClockContainer.Reset(0, startClock: true);
            if (decision.LatenessMilliseconds > 0)
            {
                Logger.Log(
                    $"Rythme synchronized start callback arrived {decision.LatenessMilliseconds} ms late; " +
                    "starting from chart time zero without skipping objects.");
            }
        }

        private void returnAfterMissedStart(long latenessMilliseconds)
        {
            if (Interlocked.Exchange(ref missedStartReturnStarted, 1) != 0)
                return;

            matchCountdown?.Complete();
            input.Stop();
            context.ReturnMessage =
                "WAVE SKIPPED — SYNCHRONIZED START MISSED\n" +
                $"The playfield became ready {latenessMilliseconds:N0} ms after the official start. " +
                "Rythme did not seek into the song or submit a partial result.\n" +
                "Return to the lobby for the next wave; your saved loadouts are unchanged.";
            Logger.Log(
                $"Rythme skipped a playfield that loaded {latenessMilliseconds} ms after the synchronized start.",
                level: LogLevel.Important);
            Scheduler.Add(() => PerformExit(skipTransition: true));
        }

        protected override void Update()
        {
            base.Update();

            if (!gameplayClockStarted || gameplayCompletion == null)
                return;

            long nowUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            bool scoreCompleted = ScoreProcessor.HasCompleted.Value;
            if (gameplayCompletion.TryStartRecovery(nowUnixMs, scoreCompleted))
            {
                if (GameplayClockContainer is MasterGameplayClockContainer masterClock)
                    masterClock.StopUsingBeatmapClock();

                Logger.Log(
                    "Rythme end-of-song recovery started; advancing the exhausted gameplay clock " +
                    $"in {ArenaGameplayCompletion.MaximumClockStepMs:N0} ms frame-stable steps.",
                    level: LogLevel.Important);
                showFinalizingOverlay(
                    "Closing the local map tail. The official receipt will return to the lobby automatically if needed.");
            }

            if (!gameplayCompletion.RecoveryStarted || scoreCompleted)
                return;

            double currentClockMs = GameplayClockContainer.CurrentTime;
            double nextClockMs = gameplayCompletion.NextClockTime(currentClockMs);
            if (nextClockMs > currentClockMs)
                GameplayClockContainer.Seek(nextClockMs);

            if (gameplayCompletion.ShouldReturnToLobby(
                    nowUnixMs,
                    ScoreProcessor.HasCompleted.Value,
                    currentClockMs))
                beginForcedReturnToLobby();
        }

        private void onNewResult(JudgementResult result)
        {
            long resultTime = (long)Math.Round(result.HitObject.StartTime);
            int noteIndex = takeNoteIndex(resultTime);
            if (noteIndex < 0)
                return;
            Note note = context.Notes[noteIndex];

            ushort? x = context.Selection.Ruleset == "osu" ? note.X : null;
            ushort? y = context.Selection.Ruleset == "osu" ? note.Y : null;
            if (!result.IsHit)
            {
                if (!input.TryMarkMiss(noteIndex, x, y, out string? missFailure) && missFailure != null)
                    context.Failure ??= missFailure;
                return;
            }

            if (!input.TryPulse(noteIndex, note.Action, x, y, out string? failureMessage))
            {
                if (failureMessage == null)
                    context.Warning ??= "One or more hits arrived without a usable live cue and were scored as misses.";
                else
                    context.Failure ??= failureMessage;
            }
        }

        private int takeNoteIndex(long resultTime)
        {
            for (int distance = 0; distance <= 5; distance++)
            {
                if (tryTake(resultTime - distance, out int index))
                    return index;
                if (distance != 0 && tryTake(resultTime + distance, out index))
                    return index;
            }
            return -1;
        }

        private bool tryTake(long atMS, out int index)
        {
            if (noteIndexesByTime.TryGetValue(atMS, out Queue<int>? indexes) && indexes.Count > 0)
            {
                index = indexes.Dequeue();
                return true;
            }
            index = -1;
            return false;
        }

        protected override async Task PrepareScoreForResultsAsync(Score score)
        {
            Schedule(() => showFinalizingOverlay(
                "The playfield is complete. Securing your signed result; this step has a 12-second watchdog."));
            await ensureOfficialFinalizationAsync().ConfigureAwait(false);
        }

        private Task ensureOfficialFinalizationAsync()
        {
            lock (finalizationGate)
                return officialFinalizationTask ??= finalizeOfficialRunAsync();
        }

        private async Task finalizeOfficialRunAsync()
        {
            try
            {
                context.Receipt = await ArenaRunFinalizer.CompleteAsync(
                    input.DrainAsync,
                    requestSealedResultAsync,
                    context.API.SubmitCompletionAsync,
                    context.Lifetime.Token).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                context.Failure ??= error.Message;
            }
            finally
            {
                try
                {
                    await input.DisposeAsync().ConfigureAwait(false);
                }
                catch (Exception error)
                {
                    context.Failure ??= $"Input shutdown failed: {error.Message}";
                }
            }
        }

        private void beginForcedReturnToLobby()
        {
            if (Interlocked.Exchange(ref forcedReturnStarted, 1) != 0)
                return;

            _ = finalizeAndReturnToLobbyAsync();
        }

        private async Task finalizeAndReturnToLobbyAsync()
        {
            await ensureOfficialFinalizationAsync().ConfigureAwait(false);
            Schedule(() =>
            {
                // If lazer completed while the receipt was being sealed, its
                // normal results transition owns the screen. Otherwise leave
                // the broken local playfield and show the receipt in the lobby.
                if (!ScoreProcessor.HasCompleted.Value)
                {
                    Logger.Log(
                        context.Receipt != null
                            ? "Rythme preserved the accepted official receipt after the local results view remained unavailable; returning to the lobby."
                            : "Rythme local results remained unavailable and official finalization did not produce a receipt; returning to the lobby with diagnostics.",
                        level: LogLevel.Important);
                    PerformExit(skipTransition: true);
                }
            });
        }

        private void showFinalizingOverlay(string detail)
        {
            if (finalizingOverlay != null)
                return;
            AddInternal(finalizingOverlay = new RythmeFinalizingOverlay(detail));
        }

        private async Task requestSealedResultAsync(CancellationToken cancellationToken)
        {
            // The server seals each transcript after that run's final cue window,
            // which can precede the shared slow-mod heat deadline by minutes. A
            // short bounded retry absorbs clock skew without trapping the player
            // behind the global phase timer if the coordinator is unhealthy.
            long retryUntil = Math.Min(
                context.Heat.SubmissionEndsUnixMs - context.ServerOffsetMS,
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 5000);

            while (true)
            {
                try
                {
                    _ = await context.API
                                     .ResultAsync(context.Enrollment.HeatTicketId, cancellationToken)
                                     .ConfigureAwait(false);
                    return;
                }
                catch (ArenaProtocolException error) when (
                    error.StatusCode == 409 &&
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < retryUntil)
                {
                    await Task.Delay(75, cancellationToken).ConfigureAwait(false);
                }
            }
        }

        protected override ResultsScreen CreateResults(ScoreInfo score) => new RythmeResultsScreen(score, context)
        {
            AllowRetry = false,
            AllowWatchingReplay = false,
            IsLocalPlay = true,
        };

        protected override void Dispose(bool isDisposing)
        {
            if (DrawableRuleset != null)
                DrawableRuleset.NewResult -= onNewResult;
            input.Stop();
            base.Dispose(isDisposing);
        }
    }

    internal partial class RythmeResultsScreen : SoloResultsScreen
    {
        private const double automatic_return_delay_ms = 4500;
        private readonly string returnTitle;
        private readonly string returnDetail;

        public override bool HideOverlaysOnEnter => true;

        public RythmeResultsScreen(ScoreInfo score, RythmeRunContext context)
            : base(score)
        {
            if (context.Receipt != null)
            {
                returnTitle = "OFFICIAL RUN ACCEPTED";
                returnDetail =
                    $"Settled score {context.Receipt.Settlement.SettledScore:N0}. " +
                    "Returning to the Crown lobby in 4 seconds; press Back to return now.";
            }
            else
            {
                returnTitle = "OFFICIAL RECEIPT UNAVAILABLE";
                returnDetail =
                    "Returning to the Crown lobby in 4 seconds to show recovery details; press Back to return now.";
            }
        }

        protected override void LoadComplete()
        {
            base.LoadComplete();
            AddInternal(new RythmeResultsReturnBanner(returnTitle, returnDetail));
            Scheduler.AddDelayed(returnToLobbyWhenCurrent, automatic_return_delay_ms);
        }

        private void returnToLobbyWhenCurrent()
        {
            if (this.IsCurrentScreen())
            {
                this.Exit();
                return;
            }

            Scheduler.AddDelayed(returnToLobbyWhenCurrent, 250);
        }
    }
}
