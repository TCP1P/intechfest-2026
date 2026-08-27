namespace Rythme.Desktop;

internal readonly record struct CueLease(string Token, long ExpiresUnixMs);

internal static class CueDeliveryPolicy
{
    // Leave enough time for the secured request to acquire the channel lock and
    // reach the coordinator. A cue that cannot meet this budget is already a
    // miss; sending it only creates a predictable 401 response.
    public const long SendSafetyMarginMs = 150;

    // Cues are published 2.5 seconds before their note. Polling more often than
    // this needlessly competes with gameplay input on the ordered secure channel.
    public const int PollIntervalMs = 200;

    public static bool HasSendBudget(long estimatedServerNowUnixMs, long expiresUnixMs) =>
        expiresUnixMs - estimatedServerNowUnixMs > SendSafetyMarginMs;

    public static bool IsRecoverableExpiredCue(ArenaProtocolException error) =>
        error.StatusCode == 401 &&
        error.Message.Contains("unavailable or expired cue", StringComparison.OrdinalIgnoreCase);

    public static bool IsExpectedPollingClosure(ArenaProtocolException error) =>
        error.StatusCode == 409 &&
        (error.Message.Contains("input transcript is already sealed", StringComparison.OrdinalIgnoreCase) ||
         error.Message.Contains("operation is not available in this heat phase", StringComparison.OrdinalIgnoreCase));
}
