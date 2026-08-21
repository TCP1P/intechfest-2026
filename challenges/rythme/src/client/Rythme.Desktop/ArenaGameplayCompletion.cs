namespace Rythme.Desktop;

internal sealed class ArenaGameplayCompletion
{
    // FrameStabilityContainer rejects release-mode reference-clock jumps larger
    // than 500 ms as invalid audio values. Stay comfortably below that guard so
    // every overdue drawable receives a normal update and becomes a miss.
    public const double MaximumClockStepMs = 250;
    public const long RecoveryTailMs = 10_000;
    public const long PostDrainCompletionGraceMs = 500;

    private readonly long durationMs;
    private long? recoveryTargetReachedAtUnixMs;

    public long DeadlineUnixMs { get; }

    public bool RecoveryStarted { get; private set; }

    public double RecoveryTargetMs => checked(durationMs + RecoveryTailMs);

    public ArenaGameplayCompletion(
        long localStartUnixMs,
        long durationMs,
        int playbackRateMilli)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(durationMs);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(playbackRateMilli);

        this.durationMs = durationMs;
        long wallDurationMs = checked((durationMs * 1_000L + playbackRateMilli - 1) / playbackRateMilli);
        DeadlineUnixMs = checked(localStartUnixMs + wallDurationMs);
    }

    public bool TryStartRecovery(long localNowUnixMs, bool scoreCompleted)
    {
        if (scoreCompleted || RecoveryStarted || localNowUnixMs < DeadlineUnixMs)
            return false;

        RecoveryStarted = true;
        return true;
    }

    public double NextClockTime(double currentClockMs)
    {
        if (!RecoveryStarted)
            throw new InvalidOperationException("End-of-song recovery has not started.");
        if (!double.IsFinite(currentClockMs))
            throw new ArgumentOutOfRangeException(nameof(currentClockMs));

        return Math.Min(RecoveryTargetMs, currentClockMs + MaximumClockStepMs);
    }

    public bool ShouldReturnToLobby(
        long localNowUnixMs,
        bool scoreCompleted,
        double currentClockMs)
    {
        if (!RecoveryStarted || scoreCompleted)
            return false;
        if (!double.IsFinite(currentClockMs))
            throw new ArgumentOutOfRangeException(nameof(currentClockMs));

        // Recovery advances in frame-stable steps. Starting the abandonment
        // timer at the wall-clock deadline makes a slow renderer lose the race
        // before it has actually presented enough frames to judge the map tail.
        // Give lazer a short grace period only after the requested clock target
        // has really propagated back through the gameplay clock.
        if (currentClockMs < RecoveryTargetMs)
            return false;

        recoveryTargetReachedAtUnixMs ??= localNowUnixMs;
        return localNowUnixMs >= checked(
            recoveryTargetReachedAtUnixMs.Value + PostDrainCompletionGraceMs);
    }
}
