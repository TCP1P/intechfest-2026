using Rythme.Desktop;

await expiredCueRejectionDoesNotPoisonInput();
await expectedCuePollingClosureDoesNotPoisonFinalization();
await endOfSongDrainCompletesAnInFlightCueRequest();
await unrelatedCuePollingConflictRemainsFatal();
await unrelatedAuthenticationFailureRemainsFatal();
await gameplayProbeDoesNotThrowAfterBackgroundFailure();
await fullGameplayQueueIsReportedAsARecoverableMiss();
await zeroHitRunSendsOneNonScoringTranscriptMarker();
await drainCancellationStopsTheInputWorker();
Console.WriteLine("input cue delivery regression checks passed");

static async Task endOfSongDrainCompletesAnInFlightCueRequest()
{
    var transport = new InFlightCueTransport();
    await using var sender = new InputSender(
        transport,
        "heat-1",
        "ticket-1",
        "osu",
        0,
        CancellationToken.None);

    await transport.Started.WaitAsync(TimeSpan.FromSeconds(2));
    Task drain = sender.DrainAsync();
    await Task.Delay(75);

    require(!drain.IsCompleted, "end-of-song drain abandoned the in-flight cue request");
    require(!transport.WasCancelled, "end-of-song drain cancelled a reserved secure-channel request");

    transport.Complete();
    await drain.WaitAsync(TimeSpan.FromSeconds(2));
    require(transport.Completed, "end-of-song drain returned before the cue response completed");
    require(!transport.WasCancelled, "completed cue request observed unexpected cancellation");
}

static async Task expectedCuePollingClosureDoesNotPoisonFinalization()
{
    require(
        CueDeliveryPolicy.IsExpectedPollingClosure(
            new ArenaProtocolException(409, "input transcript is already sealed")),
        "sealed transcript was not classified as a normal cue-poll closure");
    require(
        CueDeliveryPolicy.IsExpectedPollingClosure(
            new ArenaProtocolException(409, "operation is not available in this heat phase")),
        "completed heat was not classified as a normal cue-poll closure");
    var transport = new ClosedCueTransport("input transcript is already sealed");
    await using var sender = new InputSender(
        transport,
        "heat-1",
        "ticket-1",
        "osu",
        0,
        CancellationToken.None);

    await transport.Polled;
    await sender.DrainAsync();
}

static async Task unrelatedCuePollingConflictRemainsFatal()
{
    require(
        !CueDeliveryPolicy.IsExpectedPollingClosure(
            new ArenaProtocolException(409, "modifier profile was already locked")),
        "unrelated cue-poll conflict was classified as a normal closure");
    var transport = new ClosedCueTransport("modifier profile was already locked");
    await using var sender = new InputSender(
        transport,
        "heat-1",
        "ticket-1",
        "osu",
        0,
        CancellationToken.None);

    await transport.Polled;
    long deadline = Environment.TickCount64 + 2_000;
    while (Environment.TickCount64 < deadline)
    {
        _ = sender.TryPulse(0, 1, 32_768, 32_768, out string? failure);
        if (failure?.Contains("modifier profile was already locked", StringComparison.Ordinal) == true)
            return;
        await Task.Delay(10);
    }

    throw new InvalidOperationException("an unrelated cue-poll conflict was hidden from gameplay");
}

static async Task expiredCueRejectionDoesNotPoisonInput()
{
    var transport = new ScriptedTransport(
        (attempt, _) => attempt == 1
            ? Task.FromException<InputResponse>(
                new ArenaProtocolException(401, "unauthorized: frame 0 has an unavailable or expired cue"))
            : Task.FromResult(new InputResponse()));
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    await enqueueWhenCueArrives(sender, 0);
    await waitUntil(() => transport.FrameAttempts >= 1, "first input attempt was not sent");
    await enqueueWhenCueArrives(sender, 1);
    await waitUntil(() => transport.FrameAttempts >= 2, "input did not continue after an expired cue rejection");

    await sender.DrainAsync();
    require(transport.FrameAttempts == 2, "expired cue recovery sent an unexpected number of requests");
}

static async Task unrelatedAuthenticationFailureRemainsFatal()
{
    var transport = new ScriptedTransport(
        (_, _) => Task.FromException<InputResponse>(
            new ArenaProtocolException(401, "unauthorized: invalid secure channel")));
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    await enqueueWhenCueArrives(sender, 0);
    await waitUntil(() => transport.FrameAttempts >= 1, "authentication failure fixture was not sent");

    try
    {
        await sender.DrainAsync();
    }
    catch (ArenaProtocolException error) when (
        error.Message.Contains("Input link failed", StringComparison.Ordinal) &&
        error.Message.Contains("invalid secure channel", StringComparison.Ordinal))
    {
        return;
    }

    throw new InvalidOperationException("unrelated authentication failure was incorrectly ignored");
}

static async Task gameplayProbeDoesNotThrowAfterBackgroundFailure()
{
    var transport = new ScriptedTransport(
        (_, _) => Task.FromException<InputResponse>(
            new ArenaProtocolException(401, "unauthorized: invalid secure channel")));
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    await enqueueWhenCueArrives(sender, 0);
    await waitUntil(() => transport.FrameAttempts >= 1, "background failure fixture was not sent");

    long deadline = Environment.TickCount64 + 2_000;
    while (Environment.TickCount64 < deadline)
    {
        bool queued = sender.TryPulse(1, 1, 32_768, 32_768, out string? failure);
        if (!queued && failure?.Contains("invalid secure channel", StringComparison.Ordinal) == true)
            return;
        await Task.Delay(10);
    }

    throw new InvalidOperationException("background input failure was not safely reported to gameplay");
}

static async Task drainCancellationStopsTheInputWorker()
{
    var transport = new ScriptedTransport(async (_, token) =>
    {
        await Task.Delay(Timeout.InfiniteTimeSpan, token);
        return new InputResponse();
    });
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    await enqueueWhenCueArrives(sender, 0);
    await waitUntil(() => transport.FrameAttempts >= 1, "cancellable input fixture was not sent");
    using var deadline = new CancellationTokenSource(TimeSpan.FromMilliseconds(75));

    try
    {
        await sender.DrainAsync(deadline.Token);
    }
    catch (OperationCanceledException) when (deadline.IsCancellationRequested)
    {
        return;
    }

    throw new InvalidOperationException("input drain ignored its finalization deadline");
}

static async Task fullGameplayQueueIsReportedAsARecoverableMiss()
{
    var transport = new ScriptedTransport(
        async (_, token) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return new InputResponse();
        },
        cueCount: 96);
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    await enqueueWhenCueArrives(sender, 0);
    bool saturated = false;
    for (int noteIndex = 1; noteIndex < 96; noteIndex++)
    {
        if (sender.TryPulse(noteIndex, 1, 32_768, 32_768, out string? failure))
            continue;

        require(failure == null, "a saturated input queue was reported as a fatal run-link failure");
        saturated = true;
        break;
    }

    require(saturated, "bounded input queue saturation fixture did not fill the queue");
}

static async Task zeroHitRunSendsOneNonScoringTranscriptMarker()
{
    var transport = new TranscriptMarkerTransport();
    await using var sender = new InputSender(transport, "heat-1", "ticket-1", "osu", 0, CancellationToken.None);

    long deadline = Environment.TickCount64 + 2_000;
    while (Environment.TickCount64 < deadline &&
           !sender.TryMarkMiss(0, 12_345, 54_321, out string? failure))
    {
        require(failure == null, $"miss marker failed: {failure}");
        await Task.Delay(10);
    }

    require(
        sender.TryMarkMiss(1, 1, 2, out string? duplicateFailure) && duplicateFailure == null,
        "a second miss did not reuse the pending transcript evidence");
    await waitUntil(() => transport.FrameAttempts == 1, "zero-hit transcript marker was not sent");
    await sender.DrainAsync();

    require(transport.FrameAttempts == 1, "a zero-hit run sent more than one transcript marker");
    InputFrame marker = transport.Frames.Single();
    require(marker.NoteIndex == 0, "miss marker targeted the wrong note");
    require(marker.ActionBits == 0, "miss marker could award a hit");
    require(marker.PointerX == 12_345 && marker.PointerY == 54_321, "osu miss marker lost its chart coordinates");
}

static async Task enqueueWhenCueArrives(InputSender sender, int noteIndex)
{
    long deadline = Environment.TickCount64 + 2_000;
    while (Environment.TickCount64 < deadline)
    {
        if (sender.TryPulse(noteIndex, 1, 32_768, 32_768, out _))
            return;
        await Task.Delay(10);
    }

    throw new InvalidOperationException($"cue {noteIndex} did not arrive");
}

static async Task waitUntil(Func<bool> predicate, string failure)
{
    long deadline = Environment.TickCount64 + 2_000;
    while (Environment.TickCount64 < deadline)
    {
        if (predicate())
            return;
        await Task.Delay(10);
    }

    throw new InvalidOperationException(failure);
}

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

internal sealed class ScriptedTransport : IArenaInputTransport
{
    private readonly Func<int, CancellationToken, Task<InputResponse>> send;
    private readonly int cueCount;
    private int frameAttempts;

    public int FrameAttempts => Volatile.Read(ref frameAttempts);

    public ScriptedTransport(Func<int, CancellationToken, Task<InputResponse>> send, int cueCount = 2)
    {
        this.send = send;
        this.cueCount = cueCount;
    }

    public Task<InputResponse> SendFramesAsync(
        IReadOnlyList<InputFrame> frames,
        CancellationToken cancellationToken)
    {
        if (frames.Count != 2)
            throw new InvalidOperationException("a pulse must contain its down and up frames");
        int attempt = Interlocked.Increment(ref frameAttempts);
        return send(attempt, cancellationToken);
    }

    public Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken)
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        return Task.FromResult(new CueResponse
        {
            HeatId = "heat-1",
            ServerNowUnixMs = now,
            Cues = Enumerable.Range(0, cueCount)
                             .Select(index => new Cue
                             {
                                 NoteIndex = index,
                                 ExpiresUnixMs = now + 5_000,
                                 Token = $"cue-{index}"
                             })
                             .ToArray()
        });
    }
}

internal sealed class ClosedCueTransport : IArenaInputTransport
{
    private readonly string message;
    private readonly TaskCompletionSource polled = new(TaskCreationOptions.RunContinuationsAsynchronously);

    public Task Polled => polled.Task;

    public ClosedCueTransport(string message)
    {
        this.message = message;
    }

    public Task<InputResponse> SendFramesAsync(
        IReadOnlyList<InputFrame> frames,
        CancellationToken cancellationToken) =>
        throw new InvalidOperationException("closed-cue fixture must not send input");

    public Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken)
    {
        polled.TrySetResult();
        return Task.FromException<CueResponse>(new ArenaProtocolException(409, message));
    }
}

internal sealed class InFlightCueTransport : IArenaInputTransport
{
    private readonly TaskCompletionSource started = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly TaskCompletionSource release = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int completed;
    private int wasCancelled;

    public Task Started => started.Task;
    public bool Completed => Volatile.Read(ref completed) != 0;
    public bool WasCancelled => Volatile.Read(ref wasCancelled) != 0;

    public Task<InputResponse> SendFramesAsync(
        IReadOnlyList<InputFrame> frames,
        CancellationToken cancellationToken) =>
        throw new InvalidOperationException("in-flight cue fixture must not send input");

    public async Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken)
    {
        started.TrySetResult();
        try
        {
            await release.Task.WaitAsync(cancellationToken);
        }
        catch (OperationCanceledException)
        {
            Interlocked.Exchange(ref wasCancelled, 1);
            throw;
        }

        Interlocked.Exchange(ref completed, 1);
        return new CueResponse
        {
            HeatId = "heat-1",
            ServerNowUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            Cues = []
        };
    }

    public void Complete() => release.TrySetResult();
}

internal sealed class TranscriptMarkerTransport : IArenaInputTransport
{
    private int frameAttempts;

    public int FrameAttempts => Volatile.Read(ref frameAttempts);
    public InputFrame[] Frames { get; private set; } = [];

    public Task<InputResponse> SendFramesAsync(
        IReadOnlyList<InputFrame> frames,
        CancellationToken cancellationToken)
    {
        Frames = frames.ToArray();
        Interlocked.Increment(ref frameAttempts);
        return Task.FromResult(new InputResponse { Accepted = frames.Count });
    }

    public Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken)
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        return Task.FromResult(new CueResponse
        {
            HeatId = "heat-1",
            ServerNowUnixMs = now,
            Cues =
            [
                new Cue { NoteIndex = 0, ExpiresUnixMs = now + 5_000, Token = "cue-0" },
                new Cue { NoteIndex = 1, ExpiresUnixMs = now + 5_000, Token = "cue-1" }
            ]
        });
    }
}
