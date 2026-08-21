using osu.Game.Beatmaps;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Catch;
using osu.Game.Rulesets.Mania;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Scoring;
using osu.Game.Rulesets.Taiko;
using osu.Game.Utils;

namespace Rythme.Desktop;

internal sealed record ModEntry(
    string Acronym,
    string Name,
    string Type,
    double DefaultMultiplier,
    double PlaybackRate,
    bool Implemented,
    bool UserPlayable);

internal static class UpstreamModCatalog
{
    private const int maximumArenaMods = 12;

    public static Ruleset CreateRuleset(string id) => id switch
    {
        "osu" => new OsuRuleset(),
        "taiko" => new TaikoRuleset(),
        "fruits" => new CatchRuleset(),
        "mania" => new ManiaRuleset(),
        _ => throw new ArgumentOutOfRangeException(nameof(id), id, "Unknown built-in ruleset.")
    };

    public static IReadOnlyList<ModEntry> Read(string id)
    {
        Ruleset ruleset = CreateRuleset(id);
        ScoreMultiplierCalculator calculator = ruleset.CreateScoreMultiplierCalculator(
            new ScoreMultiplierContext(new BeatmapDifficulty()));

        return ruleset.AllMods
                      .OfType<Mod>()
                      .OrderBy(mod => mod.Type)
                      .ThenBy(mod => mod.Acronym, StringComparer.Ordinal)
                      .Select(mod => new ModEntry(
                          mod.Acronym,
                          mod.Name,
                          mod.Type.ToString(),
                          calculator.CalculateFor([mod]),
                          mod is ModRateAdjust rateAdjust ? rateAdjust.SpeedChange.Value : 1,
                          mod.HasImplementation,
                          mod.UserPlayable))
                      .ToArray();
    }

    public static bool IsArenaPlayable(Mod mod) =>
        mod.UserPlayable &&
        mod.HasImplementation &&
        mod.Type != ModType.Automation &&
        mod.Acronym != "AS" &&
        mod.Acronym != "WU" &&
        mod.Acronym != "WD";

    public static Mod[] CreateArenaLoadout(RulesetInfo rulesetInfo, IReadOnlyList<string> acronyms)
    {
        ArgumentNullException.ThrowIfNull(rulesetInfo);
        ArgumentNullException.ThrowIfNull(acronyms);
        string[] normalized = acronyms.Select(acronym =>
            acronym?.Trim().ToUpperInvariant()
            ?? throw new InvalidOperationException("A mod loadout contains a null acronym.")).ToArray();
        if (normalized.Length > maximumArenaMods)
            throw new InvalidOperationException($"A synchronized loadout can contain at most {maximumArenaMods} mods.");
        if (normalized.Any(acronym => acronym.Length is < 1 or > 8 ||
                                      acronym.Any(character => !char.IsAsciiLetterOrDigit(character))))
            throw new InvalidOperationException("A mod loadout contains an invalid acronym.");
        if (normalized.Length == 0 || normalized is ["NM"])
            return [];
        if (normalized.Any(string.IsNullOrWhiteSpace) || normalized.Contains("NM", StringComparer.Ordinal))
            throw new InvalidOperationException("Normal cannot be combined with another mod.");
        if (normalized.Distinct(StringComparer.Ordinal).Count() != normalized.Length)
            throw new InvalidOperationException("A mod loadout cannot contain duplicate entries.");

        Dictionary<string, Mod> available = rulesetInfo
                                            .CreateInstance()
                                            .AllMods
                                            .OfType<Mod>()
                                            .Where(IsArenaPlayable)
                                            .ToDictionary(mod => mod.Acronym, StringComparer.Ordinal);
        string[] unavailable = normalized.Where(acronym => !available.ContainsKey(acronym)).ToArray();
        if (unavailable.Length > 0)
            throw new InvalidOperationException(
                $"The {string.Join(",", unavailable)} loadout is unavailable for {rulesetInfo.Name}.");

        Mod[] selected = normalized.Select(acronym => available[acronym]).ToArray();
        if (!ModUtils.CheckCompatibleSet(selected, out _))
            throw new InvalidOperationException(
                $"The {string.Join(",", normalized)} loadout contains incompatible mods for {rulesetInfo.Name}.");
        return selected;
    }

    public static AppliedModSet DescribeApplied(
        RulesetInfo rulesetInfo,
        BeatmapDifficulty difficulty,
        IReadOnlyList<Mod> mods)
    {
        ArgumentNullException.ThrowIfNull(rulesetInfo);
        ArgumentNullException.ThrowIfNull(difficulty);
        ArgumentNullException.ThrowIfNull(mods);
        if (mods.Any(mod => mod == null || !IsArenaPlayable(mod)) || !ModUtils.CheckCompatibleSet(mods, out _))
            throw new InvalidOperationException("The selected mods are not valid for synchronized arena play.");

        string acronym = mods.Count == 0
            ? "NM"
            : string.Join(",", mods.Select(mod => mod.Acronym).OrderBy(value => value, StringComparer.Ordinal));
        int rateMilli = (int)Math.Round(ModUtils.CalculateRateWithMods(mods) * 1000);
        Ruleset ruleset = rulesetInfo.CreateInstance();
        double multiplier = ruleset
                            .CreateScoreMultiplierCalculator(new ScoreMultiplierContext(difficulty))
                            .CalculateFor(mods);

        return new AppliedModSet(
            acronym,
            rateMilli,
            ProtocolConstants.EmptySettingsHash,
            (int)Math.Round(multiplier * 1000));
    }
}
