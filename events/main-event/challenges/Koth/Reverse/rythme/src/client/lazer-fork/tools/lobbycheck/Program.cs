using osu.Desktop.Rythme;
using Rythme.Desktop;

string? originalServer = Environment.GetEnvironmentVariable("RYTHME_SERVER");
string? originalAutoJoin = Environment.GetEnvironmentVariable("RYTHME_AUTO_JOIN");
string? originalToken = Environment.GetEnvironmentVariable("RYTHME_TOKEN");
try
{
    Environment.SetEnvironmentVariable("RYTHME_SERVER", null);
    Environment.SetEnvironmentVariable("RYTHME_AUTO_JOIN", null);
    RythmeOptions defaults = RythmeOptions.Parse([]);
    require(
        defaults.Server == new Uri("https://rythme-private.tcp.1pc.tf/"),
        "release default did not use the production coordinator");
    require(!defaults.AutoJoin, "auto-start must require an explicit player choice");

    Environment.SetEnvironmentVariable("RYTHME_TOKEN", "koth_test_secret");
    RythmeOptions tokenOptions = RythmeOptions.Parse([]);
    require(tokenOptions.TakeToken() == "koth_test_secret", "initial token handoff failed");
    require(tokenOptions.Token == string.Empty && tokenOptions.TakeToken() == string.Empty, "token was retained after handoff");
    require(!tokenOptions.HasAuthenticatedToken, "an unverified token was retained for reconnect");
    require(!tokenOptions.TryGetAuthenticatedToken(out _), "an unverified reconnect token was exposed");
    tokenOptions.RememberAuthenticatedToken("koth_authenticated_secret");
    require(tokenOptions.HasAuthenticatedToken, "an authenticated token was not retained for reconnect");
    require(
        tokenOptions.TryGetAuthenticatedToken(out string rememberedToken) &&
        rememberedToken == "koth_authenticated_secret",
        "the authenticated reconnect token changed");
    tokenOptions.ForgetAuthenticatedToken("koth_different_secret");
    require(tokenOptions.HasAuthenticatedToken, "a stale failed login erased a newer authenticated token");
    tokenOptions.ForgetAuthenticatedToken("koth_authenticated_secret");
    require(!tokenOptions.HasAuthenticatedToken, "a rejected authenticated token was retained");
    tokenOptions.RememberAuthenticatedToken("koth_replacement_secret");
    tokenOptions.ForgetAuthenticatedToken();
    require(!tokenOptions.HasAuthenticatedToken, "explicit token change did not forget the retained credential");
    Environment.SetEnvironmentVariable("RYTHME_TOKEN", null);

    Environment.SetEnvironmentVariable("RYTHME_SERVER", "http://127.0.0.1:1337");
    require(
        RythmeOptions.Parse([]).Server == new Uri("http://127.0.0.1:1337/"),
        "loopback development override stopped working");

    var automation = new RythmeLobbyAutomation();
    automation.ObserveHeat("heat-1");
    require(!automation.TryClaimEnrollment("heat-1", "enrollment", false), "disarmed lobby claimed enrollment");
    require(automation.SetArmed(true), "lobby did not arm");
    require(automation.TryClaimEnrollment("heat-1", "enrollment", false), "armed lobby missed enrollment");
    require(!automation.TryClaimEnrollment("heat-1", "enrollment", false), "enrollment was claimed twice");
    require(!automation.TryClaimReady("heat-1", "enrollment", true, false), "lobby readied before reveal");
    require(!automation.TryClaimReady("heat-1", "reveal", false, false), "lobby readied before map preparation");
    require(automation.TryClaimReady("heat-1", "reveal", true, false), "prepared reveal was not auto-readied");
    require(!automation.TryClaimReady("heat-1", "reveal", true, false), "ready was claimed twice");

    automation.ObserveHeat("heat-2");
    require(automation.Armed, "armed preference did not survive a wave rollover");
    require(automation.TryClaimEnrollment("heat-2", "enrollment", false), "new wave retained the old enrollment claim");
    automation.ResetConnection();
    require(automation.Armed, "reconnect lost the auto-start preference");

    var reconnect = new RythmeReconnectPolicy();
    require(
        new[] { 500, 1000, 2000, 4000, 8000, 8000 }
            .SequenceEqual(Enumerable.Range(0, 6).Select(_ => reconnect.NextDelayMilliseconds())),
        "automatic reconnect backoff is not bounded");
    reconnect.Reset();
    require(reconnect.NextDelayMilliseconds() == 500, "successful reconnect did not reset the backoff");

    require(RythmeLobbyPhase.STEP_COUNT == 4, "lobby phase rail changed its step count");
    require(RythmeLobbyPhase.StepIndex("enrollment") == 0, "enrollment was not the first lobby step");
    require(RythmeLobbyPhase.StepIndex("reveal") == 1, "reveal was not the second lobby step");
    require(RythmeLobbyPhase.StepIndex("running") == 2, "play was not the third lobby step");
    require(RythmeLobbyPhase.StepIndex("submission") == 3, "submission was not the scoring step");
    require(RythmeLobbyPhase.StepIndex("results") == 3, "results was not the scoring step");
    require(RythmeLobbyPhase.StepIndex("offline") == -1, "an unknown phase was shown as active");
    require(RythmeLobbyPhase.DisplayName("running") == "PLAY", "running phase copy was unclear");
    require(RythmeLobbyPhase.DisplayName("unknown") == "SYNCING", "unknown phase did not have safe copy");

    var loadouts = new RythmeModLoadouts();
    require(loadouts.Describe("osu") == "NM" && loadouts.Describe("mania") == "NM", "loadouts did not default to NM");
    loadouts.Set("osu", ["dt", "HD"]);
    loadouts.Set("mania", ["4k", "hd"]);
    require(loadouts.Describe("osu") == "DT,HD", "osu! loadout was not canonicalized");
    require(loadouts.Describe("mania") == "4K,HD", "mania loadout was not independent");
    require(loadouts.Describe("taiko") == "NM", "saving one mode changed another mode");
    require(RythmeModLoadouts.Move("osu", 1) == "taiko", "forward loadout navigation failed");
    require(RythmeModLoadouts.Move("osu", -1) == "mania", "reverse loadout navigation did not wrap");
    expectFailure(() => loadouts.Set("osu", ["HD", "HD"]), "duplicate mods were accepted");
    expectFailure(() => loadouts.Set("osu", ["NM", "HD"]), "NM was combined with another mod");
    expectFailure(() => loadouts.Set("osu", ["H-D"]), "unsafe mod acronym was accepted");
    expectFailure(
        () => loadouts.Set("osu", Enumerable.Range(0, RythmeModLoadouts.MaximumMods + 1).Select(index => $"M{index}")),
        "an oversized mod loadout was accepted");

    require(
        RythmeHeatPreparation.CanCommit("heat-2", "heat-2", "reveal", 20_000, 19_999),
        "current reveal was rejected");
    require(
        !RythmeHeatPreparation.CanCommit("heat-2", "heat-3", "reveal", 20_000, 19_000),
        "a rolled-over heat was accepted");
    require(
        !RythmeHeatPreparation.CanCommit("heat-2", "heat-2", "running", 20_000, 20_000),
        "a closed reveal was accepted");
    require(
        !RythmeHeatPreparation.CanCommit("heat-2", "heat-2", "reveal", 20_000, 20_000),
        "the exact start deadline was accepted");

    RythmeStartDecision waitingStart = RythmeSynchronizedStart.Decide(20_000, 19_000);
    require(!waitingStart.CanStart && !waitingStart.Missed && waitingStart.WaitMilliseconds == 1_000,
        "future synchronized start did not wait for its target");
    RythmeStartDecision onTimeStart = RythmeSynchronizedStart.Decide(20_000, 20_000);
    require(onTimeStart.CanStart && !onTimeStart.Missed && onTimeStart.LatenessMilliseconds == 0,
        "on-time synchronized start was rejected");
    RythmeStartDecision frameLateStart = RythmeSynchronizedStart.Decide(
        20_000,
        20_000 + RythmeSynchronizedStart.MaximumAcceptedLatenessMs);
    require(frameLateStart.CanStart && !frameLateStart.Missed,
        "normal scheduler jitter was treated as a missed heat");
    RythmeStartDecision missedStart = RythmeSynchronizedStart.Decide(
        20_000,
        20_001 + RythmeSynchronizedStart.MaximumAcceptedLatenessMs);
    require(!missedStart.CanStart && missedStart.Missed && missedStart.WaitMilliseconds == 0,
        "late playfield was allowed to seek into an active song");

    var lifetime = new RythmeCancellationLifetime();
    CancellationToken lifetimeToken = lifetime.Token;
    lifetime.Cancel();
    lifetime.Cancel();
    lifetime.Dispose();
    lifetime.Dispose();
    require(lifetimeToken.IsCancellationRequested, "disposed lifetime lost its cancellation state");
    require(lifetime.Token.IsCancellationRequested, "token observation after lifetime disposal failed");

    var concurrentLifetime = new RythmeCancellationLifetime();
    Parallel.For(0, 64, _ => concurrentLifetime.Dispose());
    require(concurrentLifetime.Token.IsCancellationRequested, "concurrent lifetime disposal did not cancel");

    var reentrantLifetime = new RythmeCancellationLifetime();
    using (reentrantLifetime.Token.Register(reentrantLifetime.Dispose))
    {
        Task cancellation = Task.Run(reentrantLifetime.Cancel);
        require(
            cancellation.Wait(TimeSpan.FromSeconds(2)),
            "a reentrant disposal callback deadlocked cancellation");
    }
    reentrantLifetime.Dispose();
    require(reentrantLifetime.Token.IsCancellationRequested, "reentrant cancellation lost its terminal state");

    Exception? activeFailure = null;
    require(
        BackgroundFailure.CaptureUnlessStopping(
            ref activeFailure,
            new InvalidOperationException("active failure"),
            CancellationToken.None),
        "an active background failure was ignored");
    require(activeFailure?.Message == "active failure", "the active background failure changed");
    using (var stopping = new CancellationTokenSource())
    {
        stopping.Cancel();
        Exception? teardownFailure = null;
        require(
            !BackgroundFailure.CaptureUnlessStopping(
                ref teardownFailure,
                new InvalidOperationException("late teardown response"),
                stopping.Token),
            "a normal teardown response was recorded as a link failure");
        require(teardownFailure == null, "teardown polluted the persistent failure state");
    }

    using (var parent = new CancellationTokenSource())
    {
        var linkedLifetime = new RythmeCancellationLifetime(parent.Token);
        parent.Cancel();
        require(linkedLifetime.Token.IsCancellationRequested, "parent cancellation did not reach linked lifetime");
        linkedLifetime.Dispose();
        linkedLifetime.Dispose();
    }

    Console.WriteLine("lobby coordinator and auto-start checks passed");
}
finally
{
    Environment.SetEnvironmentVariable("RYTHME_SERVER", originalServer);
    Environment.SetEnvironmentVariable("RYTHME_AUTO_JOIN", originalAutoJoin);
    Environment.SetEnvironmentVariable("RYTHME_TOKEN", originalToken);
}

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static void expectFailure(Action action, string message)
{
    try
    {
        action();
    }
    catch (ArgumentException)
    {
        return;
    }

    throw new InvalidOperationException(message);
}
