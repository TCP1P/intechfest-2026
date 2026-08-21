namespace Rythme.Desktop;

internal sealed record AppliedModSet(
    string Acronym,
    int RateMilli,
    string SettingsHash,
    int ScoreMultiplierMilli);

internal sealed record ReadyBinding(AppliedModSet AppliedMods);

internal static class ArenaPolicy
{
    public static AppliedModSet RequiredMods(Selection _) =>
        new("NM", 1000, ProtocolConstants.EmptySettingsHash, 1000);
}
