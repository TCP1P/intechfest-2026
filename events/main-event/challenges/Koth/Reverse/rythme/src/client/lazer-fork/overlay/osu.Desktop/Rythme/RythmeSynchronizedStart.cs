namespace osu.Desktop.Rythme
{
    internal readonly record struct RythmeStartDecision(
        bool CanStart,
        bool Missed,
        long WaitMilliseconds,
        long LatenessMilliseconds);

    internal static class RythmeSynchronizedStart
    {
        // A scheduler callback can arrive a frame late under normal desktop
        // load. Beyond this small allowance, entering at chart time zero would
        // no longer be synchronized and seeking forward would skip objects.
        public const long MaximumAcceptedLatenessMs = 100;

        public static RythmeStartDecision Decide(long localTargetUnixMs, long localNowUnixMs)
        {
            if (localNowUnixMs < localTargetUnixMs)
            {
                return new RythmeStartDecision(
                    CanStart: false,
                    Missed: false,
                    WaitMilliseconds: localTargetUnixMs - localNowUnixMs,
                    LatenessMilliseconds: 0);
            }

            long lateness = localNowUnixMs - localTargetUnixMs;
            return new RythmeStartDecision(
                CanStart: lateness <= MaximumAcceptedLatenessMs,
                Missed: lateness > MaximumAcceptedLatenessMs,
                WaitMilliseconds: 0,
                LatenessMilliseconds: lateness);
        }
    }
}
