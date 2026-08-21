using System.Diagnostics;
using Rythme.Desktop;

completionDeadlineTracksPlaybackRate();
completionRecoveryIsLateBoundAndRepeatable();
await completesAllStagesInOrder();
await timeoutCancelsWorkAndReturnsControl();
await callerCancellationIsPreserved();
await stageFailureStopsFinalization();
Console.WriteLine("end-of-song finalization regression checks passed");

static void completionDeadlineTracksPlaybackRate()
{
    const long start = 1_000_000;
    const long duration = 219_853;

    var normal = new ArenaGameplayCompletion(start, duration, 1_000);
    var halfTime = new ArenaGameplayCompletion(start, duration, 750);
    var doubleTime = new ArenaGameplayCompletion(start, duration, 1_500);

    require(normal.DeadlineUnixMs == start + duration, "normal-rate completion deadline drifted");
    require(halfTime.DeadlineUnixMs == start + 293_138, "slow-rate completion deadline rounded incorrectly");
    require(doubleTime.DeadlineUnixMs == start + 146_569, "fast-rate completion deadline rounded incorrectly");
    require(normal.RecoveryTargetMs > duration, "recovery target did not clear the chart tail");
}

static void completionRecoveryIsLateBoundAndRepeatable()
{
    var guard = new ArenaGameplayCompletion(10_000, 5_000, 1_000);

    require(!guard.TryStartRecovery(14_999, false), "completion recovery ran before its deadline");
    require(!guard.TryStartRecovery(15_000, true), "completion recovery changed an already-complete score");
    require(guard.TryStartRecovery(15_000, false), "completion recovery did not run at its deadline");
    require(!guard.TryStartRecovery(15_001, false), "completion recovery was started twice");

    require(
        ArenaGameplayCompletion.MaximumClockStepMs < 500,
        "recovery step can trigger lazer's invalid audio-clock guard");
    double current = 4_900;
    int steps = 0;
    while (current < guard.RecoveryTargetMs)
    {
        double next = guard.NextClockTime(current);
        require(next > current, "recovery clock stopped before its target");
        require(next - current <= ArenaGameplayCompletion.MaximumClockStepMs, "recovery clock jumped too far");
        current = next;
        steps++;
    }
    require(current == guard.RecoveryTargetMs, "recovery clock overshot its target");
    require(steps > 1, "recovery did not exercise incremental clock advancement");
    require(
        !guard.ShouldReturnToLobby(60_000, false, guard.RecoveryTargetMs - 1),
        "a low-frame-rate recovery was abandoned before reaching the map tail");
    require(
        !guard.ShouldReturnToLobby(60_000, false, guard.RecoveryTargetMs),
        "recovery did not give lazer a post-drain completion frame");
    require(
        !guard.ShouldReturnToLobby(
            60_000 + ArenaGameplayCompletion.PostDrainCompletionGraceMs - 1,
            false,
            guard.RecoveryTargetMs),
        "recovery abandoned local results during the post-drain grace period");
    require(
        guard.ShouldReturnToLobby(
            60_000 + ArenaGameplayCompletion.PostDrainCompletionGraceMs,
            false,
            guard.RecoveryTargetMs),
        "a genuinely stalled local result can still trap the player");
    require(
        !guard.ShouldReturnToLobby(
            60_000 + ArenaGameplayCompletion.PostDrainCompletionGraceMs,
            true,
            guard.RecoveryTargetMs),
        "completed results were routed away from lazer");
}

static async Task completesAllStagesInOrder()
{
    var stages = new List<string>();
    string receipt = await ArenaRunFinalizer.CompleteAsync(
        _ =>
        {
            stages.Add("drain");
            return Task.CompletedTask;
        },
        _ =>
        {
            stages.Add("seal");
            return Task.CompletedTask;
        },
        _ =>
        {
            stages.Add("submit");
            return Task.FromResult("receipt");
        },
        CancellationToken.None,
        TimeSpan.FromSeconds(1));

    require(receipt == "receipt", "finalizer changed the accepted receipt");
    require(stages.SequenceEqual(["drain", "seal", "submit"]), "finalizer stages ran out of order");
}

static async Task timeoutCancelsWorkAndReturnsControl()
{
    bool sealCalled = false;
    bool submitCalled = false;
    var elapsed = Stopwatch.StartNew();

    try
    {
        _ = await ArenaRunFinalizer.CompleteAsync(
            _ => new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously).Task,
            _ =>
            {
                sealCalled = true;
                return Task.CompletedTask;
            },
            _ =>
            {
                submitCalled = true;
                return Task.FromResult("receipt");
            },
            CancellationToken.None,
            TimeSpan.FromMilliseconds(75));
    }
    catch (ArenaProtocolException error) when (
        error.Message.Contains("finalization timed out", StringComparison.OrdinalIgnoreCase))
    {
        require(elapsed.Elapsed < TimeSpan.FromSeconds(1), "finalization watchdog returned too slowly");
        require(!sealCalled && !submitCalled, "finalization continued after its deadline");
        return;
    }

    throw new InvalidOperationException("stalled finalization did not return a bounded failure");
}

static async Task callerCancellationIsPreserved()
{
    using var caller = new CancellationTokenSource();
    caller.Cancel();

    try
    {
        _ = await ArenaRunFinalizer.CompleteAsync(
            token => Task.Delay(Timeout.InfiniteTimeSpan, token),
            _ => Task.CompletedTask,
            _ => Task.FromResult("receipt"),
            caller.Token,
            TimeSpan.FromSeconds(1));
    }
    catch (OperationCanceledException)
    {
        return;
    }

    throw new InvalidOperationException("caller cancellation was disguised as a finalization timeout");
}

static async Task stageFailureStopsFinalization()
{
    bool sealCalled = false;
    bool submitCalled = false;

    try
    {
        _ = await ArenaRunFinalizer.CompleteAsync(
            _ => Task.FromException(new InvalidOperationException("drain failed")),
            _ =>
            {
                sealCalled = true;
                return Task.CompletedTask;
            },
            _ =>
            {
                submitCalled = true;
                return Task.FromResult("receipt");
            },
            CancellationToken.None,
            TimeSpan.FromSeconds(1));
    }
    catch (InvalidOperationException error) when (error.Message == "drain failed")
    {
        require(!sealCalled && !submitCalled, "finalization continued after a failed stage");
        return;
    }

    throw new InvalidOperationException("finalization stage failure was not preserved");
}

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}
