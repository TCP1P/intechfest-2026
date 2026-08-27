using System.Threading.Channels;

namespace Rythme.Desktop;

internal interface IArenaInputTransport
{
    Task<InputResponse> SendFramesAsync(IReadOnlyList<InputFrame> frames, CancellationToken cancellationToken);

    Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken);
}

internal sealed class InputSender : IAsyncDisposable
{
    private readonly IArenaInputTransport api;
    private readonly string ticket;
    private readonly string ruleset;
    private readonly Channel<PendingInput> queue;
    private readonly LiveCueBuffer cues;
    private readonly RythmeCancellationLifetime lifetime;
    private readonly Task worker;
    private ulong sequence;
    private int transcriptEvidenceState;
    private Exception? failure;

    public InputSender(
        IArenaInputTransport api,
        string heatId,
        string ticket,
        string ruleset,
        long serverOffsetMS,
        CancellationToken cancellationToken)
    {
        this.api = api;
        this.ticket = ticket;
        this.ruleset = ruleset;
        lifetime = new RythmeCancellationLifetime(cancellationToken);
        cues = new LiveCueBuffer(api, heatId, ticket, serverOffsetMS, lifetime.Token);
        queue = Channel.CreateBounded<PendingInput>(new BoundedChannelOptions(64)
        {
            SingleReader = true,
            SingleWriter = true,
            FullMode = BoundedChannelFullMode.Wait
        });
        worker = Task.Run(SendLoopAsync, CancellationToken.None);
    }

    private CancellationToken CancellationToken => lifetime.Token;

    public async ValueTask PulseAsync(int noteIndex, ushort action, ushort? x, ushort? y)
    {
        ThrowIfFailed();
        if (!cues.TryTake(noteIndex, out CueLease cue))
            throw new ArenaProtocolException(0, "The live cue for this object is unavailable.");
        InputFrame down = Frame(++sequence, noteIndex, cue.Token, action, x, y);
        InputFrame up = Frame(++sequence, noteIndex, cue.Token, 0, x, y);
        await queue.Writer.WriteAsync(new PendingInput([down, up], cue.ExpiresUnixMs, false), CancellationToken)
                   .ConfigureAwait(false);
    }

    public bool TryPulse(
        int noteIndex,
        ushort action,
        ushort? x,
        ushort? y,
        out string? failureMessage)
    {
        try
        {
            ThrowIfFailed();
            if (!cues.TryTake(noteIndex, out CueLease cue))
            {
                failureMessage = null;
                return false;
            }
            InputFrame down = Frame(++sequence, noteIndex, cue.Token, action, x, y);
            InputFrame up = Frame(++sequence, noteIndex, cue.Token, 0, x, y);
            bool queued = queue.Writer.TryWrite(new PendingInput([down, up], cue.ExpiresUnixMs, false));
            // A full bounded queue means this hit becomes a miss; it does not
            // invalidate earlier accepted input or a later official receipt.
            // DrainAsync remains the authority for a genuinely failed link.
            failureMessage = null;
            return queued;
        }
        catch (ArenaProtocolException error)
        {
            // A background transport failure must not escape a gameplay event
            // callback and terminate osu!'s update thread. Finalization still
            // rejects the run because DrainAsync surfaces the stored failure.
            failureMessage = error.Message;
            return false;
        }
    }

    public bool TryMarkMiss(
        int noteIndex,
        ushort? x,
        ushort? y,
        out string? failureMessage)
    {
        try
        {
            ThrowIfFailed();

            // A real accepted hit already proves that the client processed the
            // live chart. Otherwise reserve one miss marker so a zero-hit run
            // can still produce a signed, explicitly ineligible receipt.
            if (Interlocked.CompareExchange(ref transcriptEvidenceState, 1, 0) != 0)
            {
                failureMessage = null;
                return true;
            }

            if (!cues.TryTake(noteIndex, out CueLease cue))
            {
                Interlocked.CompareExchange(ref transcriptEvidenceState, 0, 1);
                failureMessage = null;
                return false;
            }

            InputFrame marker = Frame(++sequence, noteIndex, cue.Token, 0, x, y);
            bool queued = queue.Writer.TryWrite(new PendingInput([marker], cue.ExpiresUnixMs, true));
            if (!queued)
                Interlocked.CompareExchange(ref transcriptEvidenceState, 0, 1);
            failureMessage = null;
            return queued;
        }
        catch (ArenaProtocolException error)
        {
            Interlocked.CompareExchange(ref transcriptEvidenceState, 0, 1);
            failureMessage = error.Message;
            return false;
        }
    }

    public void Stop()
    {
        queue.Writer.TryComplete();
        if (!lifetime.IsCancellationRequested)
            lifetime.Cancel();
    }

    public async Task DrainAsync(CancellationToken cancellationToken = default)
    {
        using CancellationTokenRegistration registration = cancellationToken.Register(
            static state => ((RythmeCancellationLifetime)state!).Cancel(),
            lifetime);
        await cues.StopAsync().ConfigureAwait(false);
        queue.Writer.TryComplete();
        await worker.ConfigureAwait(false);
        cancellationToken.ThrowIfCancellationRequested();
        ThrowIfFailed();
    }

    private InputFrame Frame(
        ulong frameSequence,
        int noteIndex,
        string cueToken,
        ushort action,
        ushort? x,
        ushort? y) => new()
        {
            HeatTicketId = ticket,
            Sequence = frameSequence,
            Ruleset = ruleset,
            NoteIndex = noteIndex,
            CueToken = cueToken,
            ActionBits = action,
            PointerX = ruleset == "osu" ? x : null,
            PointerY = ruleset == "osu" ? y : null
        };

    private async Task SendLoopAsync()
    {
        try
        {
            await foreach (PendingInput pending in queue.Reader.ReadAllAsync(CancellationToken).ConfigureAwait(false))
            {
                if (!cues.HasSendBudget(pending.ExpiresUnixMs))
                {
                    releaseMissMarker(pending);
                    continue;
                }

                try
                {
                    InputResponse response = await api
                                                   .SendFramesAsync(pending.Frames, CancellationToken)
                                                   .ConfigureAwait(false);
                    if (response.Accepted > 0)
                        Volatile.Write(ref transcriptEvidenceState, 2);
                }
                catch (ArenaProtocolException error) when (CueDeliveryPolicy.IsRecoverableExpiredCue(error))
                {
                    // Network or channel-lock delay can consume the remaining
                    // lease after the local check. The coordinator correctly
                    // rejected the hit, so treat it as a miss and keep playing.
                    releaseMissMarker(pending);
                }
            }
        }
        catch (Exception error)
        {
            BackgroundFailure.CaptureUnlessStopping(ref failure, error, CancellationToken);
            queue.Writer.TryComplete(error);
        }
    }

    private void releaseMissMarker(PendingInput pending)
    {
        if (pending.IsTranscriptMarker)
            Interlocked.CompareExchange(ref transcriptEvidenceState, 0, 1);
    }

    private void ThrowIfFailed()
    {
        Exception? error = Volatile.Read(ref failure);
        if (error != null)
            throw new ArenaProtocolException(0, $"Input link failed: {error.Message}", error);
    }

    public async ValueTask DisposeAsync()
    {
        Stop();
        await cues.StopAsync().ConfigureAwait(false);
        await worker.ConfigureAwait(false);
        lifetime.Dispose();
    }

    private readonly record struct PendingInput(
        InputFrame[] Frames,
        long ExpiresUnixMs,
        bool IsTranscriptMarker);
}

internal sealed class LiveCueBuffer : IAsyncDisposable
{
    private readonly IArenaInputTransport api;
    private readonly string heatId;
    private readonly string ticket;
    private readonly object gate = new();
    private readonly Dictionary<int, Cue> available = new();
    private readonly RythmeCancellationLifetime lifetime;
    private readonly Task worker;
    private long serverOffsetMS;
    private int stopRequested;
    private Exception? failure;

    public LiveCueBuffer(
        IArenaInputTransport api,
        string heatId,
        string ticket,
        long serverOffsetMS,
        CancellationToken cancellationToken)
    {
        this.api = api;
        this.heatId = heatId;
        this.ticket = ticket;
        this.serverOffsetMS = serverOffsetMS;
        lifetime = new RythmeCancellationLifetime(cancellationToken);
        worker = Task.Run(PollAsync, CancellationToken.None);
    }

    public bool TryTake(int noteIndex, out CueLease lease)
    {
        Exception? error = Volatile.Read(ref failure);
        if (error != null)
            throw new ArenaProtocolException(0, $"Live cue link failed: {error.Message}", error);
        lock (gate)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + Interlocked.Read(ref serverOffsetMS);
            if (available.Remove(noteIndex, out Cue? cue) &&
                CueDeliveryPolicy.HasSendBudget(now, cue.ExpiresUnixMs))
            {
                lease = new CueLease(cue.Token, cue.ExpiresUnixMs);
                return true;
            }
        }
        lease = default;
        return false;
    }

    public bool HasSendBudget(long expiresUnixMs)
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + Interlocked.Read(ref serverOffsetMS);
        return CueDeliveryPolicy.HasSendBudget(now, expiresUnixMs);
    }

    private async Task PollAsync()
    {
        try
        {
            while (!lifetime.IsCancellationRequested && Volatile.Read(ref stopRequested) == 0)
            {
                long sent = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                CueResponse response;
                try
                {
                    response = await api.CuesAsync(ticket, lifetime.Token).ConfigureAwait(false);
                }
                catch (ArenaProtocolException error) when (CueDeliveryPolicy.IsExpectedPollingClosure(error))
                {
                    // The score processor may finish on the same frame that the
                    // coordinator seals this run (or advances the heat). This is
                    // a normal terminal state, not a broken live-input link.
                    return;
                }
                long received = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (response.HeatId != heatId)
                    throw new ArenaProtocolException(0, "Coordinator returned cues for a different heat.");
                long midpoint = sent + (received - sent) / 2;
                Interlocked.Exchange(ref serverOffsetMS, response.ServerNowUnixMs - midpoint);
                lock (gate)
                {
                    long now = received + Interlocked.Read(ref serverOffsetMS);
                    foreach (int expired in available
                                 .Where(pair => !CueDeliveryPolicy.HasSendBudget(now, pair.Value.ExpiresUnixMs))
                                 .Select(pair => pair.Key)
                                 .ToArray())
                        available.Remove(expired);
                    foreach (Cue cue in response.Cues)
                        available[cue.NoteIndex] = cue;
                }

                if (Volatile.Read(ref stopRequested) != 0)
                    return;
                await Task.Delay(CueDeliveryPolicy.PollIntervalMs, lifetime.Token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested)
        {
        }
        catch (Exception error)
        {
            BackgroundFailure.CaptureUnlessStopping(ref failure, error, lifetime.Token);
        }
    }

    public async Task StopAsync()
    {
        // A secure request reserves the native channel's next sequence before
        // it enters HttpClient and clears that reservation only after the
        // signed response has been verified. Cancelling an in-flight cue poll
        // during normal end-of-song drain would therefore strand the channel
        // in a pending state and make result submission fail with native code
        // 11. Stop scheduling new polls, but let the one already in flight
        // finish. InputSender.Stop() and a DrainAsync deadline still cancel the
        // lifetime for real disconnects and bounded failure recovery.
        Interlocked.Exchange(ref stopRequested, 1);
        await worker.ConfigureAwait(false);
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        lifetime.Dispose();
    }
}
