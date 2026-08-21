using System;

namespace osu.Desktop.Rythme
{
    internal static class RythmeHeatPreparation
    {
        public static bool CanCommit(
            string expectedHeatId,
            string actualHeatId,
            string actualPhase,
            long startAtUnixMs,
            long serverNowUnixMs) =>
            !string.IsNullOrWhiteSpace(expectedHeatId) &&
            string.Equals(expectedHeatId, actualHeatId, StringComparison.Ordinal) &&
            string.Equals(actualPhase, "reveal", StringComparison.Ordinal) &&
            serverNowUnixMs < startAtUnixMs;
    }
}
