using System;
using System.Collections.Generic;
using System.Linq;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// Keeps one canonical native-mod loadout for each built-in ruleset. The
    /// upcoming ruleset stays hidden, so auto-start applies the matching saved
    /// loadout only after the coordinator reveals it.
    /// </summary>
    internal sealed class RythmeModLoadouts
    {
        public const int MaximumMods = 12;
        private const int maximum_acronym_length = 8;

        private static readonly string[] ruleset_order = ["osu", "taiko", "fruits", "mania"];

        private readonly object sync = new object();
        private readonly Dictionary<string, string[]> loadouts = ruleset_order.ToDictionary(
            ruleset => ruleset,
            _ => Array.Empty<string>(),
            StringComparer.Ordinal);

        public static string FirstRuleset => ruleset_order[0];

        public IReadOnlyList<string> Get(string ruleset)
        {
            string normalizedRuleset = normalizeRuleset(ruleset);
            lock (sync)
                return loadouts[normalizedRuleset].ToArray();
        }

        public string Describe(string ruleset)
        {
            IReadOnlyList<string> mods = Get(ruleset);
            return mods.Count == 0 ? "NM" : string.Join(",", mods);
        }

        public void Set(string ruleset, IEnumerable<string> acronyms)
        {
            ArgumentNullException.ThrowIfNull(acronyms);
            string normalizedRuleset = normalizeRuleset(ruleset);
            string[] normalized = acronyms.Select(normalizeAcronym).ToArray();
            if (normalized.Length > MaximumMods)
                throw new ArgumentException($"A loadout can contain at most {MaximumMods} mods.", nameof(acronyms));
            if (normalized.Length == 1 && normalized[0] == "NM")
                normalized = [];
            else if (normalized.Contains("NM", StringComparer.Ordinal))
                throw new ArgumentException("Normal cannot be combined with another mod.", nameof(acronyms));

            Array.Sort(normalized, StringComparer.Ordinal);
            if (normalized.Distinct(StringComparer.Ordinal).Count() != normalized.Length)
                throw new ArgumentException("A loadout cannot contain the same mod twice.", nameof(acronyms));

            lock (sync)
                loadouts[normalizedRuleset] = normalized;
        }

        public static string Move(string ruleset, int direction)
        {
            string normalized = normalizeRuleset(ruleset);
            int current = Array.IndexOf(ruleset_order, normalized);
            int next = ((current + Math.Sign(direction)) % ruleset_order.Length + ruleset_order.Length) % ruleset_order.Length;
            return ruleset_order[next];
        }

        public static string DisplayName(string ruleset) => normalizeRuleset(ruleset) switch
        {
            "osu" => "OSU!",
            "taiko" => "TAIKO",
            "fruits" => "CATCH",
            "mania" => "MANIA",
            _ => throw new InvalidOperationException(),
        };

        private static string normalizeRuleset(string ruleset)
        {
            string normalized = ruleset?.Trim().ToLowerInvariant()
                                ?? throw new ArgumentNullException(nameof(ruleset));
            if (!ruleset_order.Contains(normalized, StringComparer.Ordinal))
                throw new ArgumentOutOfRangeException(nameof(ruleset), ruleset, "Unknown built-in ruleset.");
            return normalized;
        }

        private static string normalizeAcronym(string acronym)
        {
            string normalized = acronym?.Trim().ToUpperInvariant()
                                ?? throw new ArgumentNullException(nameof(acronym));
            if (normalized.Length is < 1 or > maximum_acronym_length ||
                normalized.Any(character => !char.IsAsciiLetterOrDigit(character)))
                throw new ArgumentException("Mod acronyms must contain 1-8 ASCII letters or digits.", nameof(acronym));
            return normalized;
        }
    }
}
