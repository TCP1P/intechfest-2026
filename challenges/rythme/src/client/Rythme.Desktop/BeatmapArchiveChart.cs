using System.Globalization;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace Rythme.Desktop;

internal static class BeatmapArchiveChart
{
    private const long maximumBeatmapBytes = 8L << 20;

    public static Note[] Extract(byte[] archive, Selection selection)
    {
        ArgumentNullException.ThrowIfNull(archive);
        ArgumentNullException.ThrowIfNull(selection);
        using var stream = new MemoryStream(archive, writable: false);
        using var zip = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: false);
        ParsedBeatmap beatmap = Select(zip, selection).Beatmap;

        int keys = selection.Ruleset == "mania" ? KeyCount(beatmap) : 0;
        int previousFruitX = -1;
        var notes = new List<(Note Note, int Order)>();
        for (int order = 0; order < beatmap.HitObjects.Count; order++)
        {
            string[] fields = beatmap.HitObjects[order].Split(',');
            if (fields.Length < 5 ||
                !int.TryParse(fields[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out int x) ||
                !int.TryParse(fields[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out int y) ||
                !long.TryParse(fields[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out long atMs) ||
                !int.TryParse(fields[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out int objectType) ||
                !int.TryParse(fields[4], NumberStyles.Integer, CultureInfo.InvariantCulture, out int hitSound))
                throw new InvalidOperationException("The revealed difficulty contains a malformed hit object.");

            bool circle = (objectType & 1) != 0;
            bool slider = (objectType & 2) != 0;
            bool hold = (objectType & 128) != 0;
            if ((selection.Ruleset is "osu" or "fruits" && !circle && !slider) ||
                (selection.Ruleset == "taiko" && !circle) ||
                (selection.Ruleset == "mania" && !circle && !hold))
                continue;

            ushort action;
            ushort scaledX = 0;
            ushort scaledY = 0;
            switch (selection.Ruleset)
            {
                case "osu":
                    action = 0x3;
                    scaledX = Scale(x, 512);
                    scaledY = Scale(y, 384);
                    break;

                case "taiko":
                    action = (hitSound & (2 | 8)) != 0 ? (ushort)0x3 : (ushort)0xc;
                    break;

                case "fruits":
                    action = previousFruitX < 0 || x < previousFruitX ? (ushort)1 : (ushort)2;
                    previousFruitX = x;
                    break;

                case "mania":
                    int lane = Math.Clamp(x * keys / 512, 0, keys - 1);
                    action = checked((ushort)(1 << lane));
                    break;

                default:
                    throw new InvalidOperationException("The revealed ruleset is unsupported.");
            }

            notes.Add((new Note { AtMs = atMs, Action = action, X = scaledX, Y = scaledY }, order));
        }

        Note[] result = notes.OrderBy(item => item.Note.AtMs).ThenBy(item => item.Order).Select(item => item.Note).ToArray();
        if (result.Length != selection.NoteCount)
            throw new InvalidOperationException("The downloaded difficulty has an unexpected object count.");
        return result;
    }

    public static string SelectedBeatmapMd5(byte[] archive, Selection selection)
    {
        ArgumentNullException.ThrowIfNull(archive);
        ArgumentNullException.ThrowIfNull(selection);
        return SelectedBeatmapMd5(
            archive,
            selection.BeatmapId,
            selection.BeatmapsetId,
            selection.Ruleset);
    }

    public static string SelectedBeatmapMd5(
        byte[] archive,
        long beatmapId,
        long beatmapsetId,
        string ruleset)
    {
        ArgumentNullException.ThrowIfNull(archive);
        ArgumentException.ThrowIfNullOrWhiteSpace(ruleset);
        using var stream = new MemoryStream(archive, writable: false);
        using var zip = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: false);
        SelectedBeatmap selected = Select(zip, beatmapId, beatmapsetId, ruleset);
        using Stream raw = selected.Entry.Open();
        return Convert.ToHexString(MD5.HashData(raw)).ToLowerInvariant();
    }

    private static SelectedBeatmap Select(ZipArchive archive, Selection selection) => Select(
        archive,
        selection.BeatmapId,
        selection.BeatmapsetId,
        selection.Ruleset);

    private static SelectedBeatmap Select(
        ZipArchive archive,
        long beatmapId,
        long beatmapsetId,
        string ruleset)
    {
        SelectedBeatmap[] matches = archive.Entries
                                             .Where(entry => entry.FullName.EndsWith(".osu", StringComparison.OrdinalIgnoreCase))
                                             .Select(entry => new SelectedBeatmap(entry, Parse(entry)))
                                             .Where(candidate => candidate.Beatmap.Metadata.GetValueOrDefault("BeatmapID") ==
                                                                 beatmapId.ToString(CultureInfo.InvariantCulture))
                                             .ToArray();
        if (matches.Length != 1)
            throw new InvalidOperationException("The package does not contain one exact revealed difficulty.");

        SelectedBeatmap selected = matches[0];
        if (selected.Beatmap.Metadata.GetValueOrDefault("BeatmapSetID") !=
            beatmapsetId.ToString(CultureInfo.InvariantCulture) ||
            selected.Beatmap.General.GetValueOrDefault("Mode") != Mode(ruleset))
            throw new InvalidOperationException("The downloaded difficulty metadata does not match the reveal.");
        return selected;
    }

    private static ParsedBeatmap Parse(ZipArchiveEntry entry)
    {
        if (entry.Length < 1 || entry.Length > maximumBeatmapBytes)
            throw new InvalidOperationException("A beatmap file in the package is oversized.");

        var result = new ParsedBeatmap();
        using Stream raw = entry.Open();
        using var reader = new StreamReader(raw, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
        string section = "";
        while (reader.ReadLine() is { } source)
        {
            string line = source.Trim().TrimStart('\uFEFF');
            if (line.StartsWith('[') && line.EndsWith(']'))
            {
                section = line[1..^1];
                continue;
            }
            if (line.Length == 0 || line.StartsWith("//", StringComparison.Ordinal))
                continue;
            if (section == "HitObjects")
            {
                result.HitObjects.Add(line);
                continue;
            }
            int separator = line.IndexOf(':');
            if (separator < 0)
                continue;
            string key = line[..separator].Trim();
            string value = line[(separator + 1)..].Trim();
            if (section == "General")
                result.General[key] = value;
            else if (section == "Metadata")
                result.Metadata[key] = value;
            else if (section == "Difficulty")
                result.Difficulty[key] = value;
        }
        return result;
    }

    private static int KeyCount(ParsedBeatmap beatmap)
    {
        if (!double.TryParse(
                beatmap.Difficulty.GetValueOrDefault("CircleSize"),
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out double raw))
            throw new InvalidOperationException("The mania difficulty has no key count.");
        int keys = checked((int)Math.Round(raw, MidpointRounding.AwayFromZero));
        if (keys is < 1 or > 16)
            throw new InvalidOperationException("The mania difficulty has an invalid key count.");
        return keys;
    }

    private static ushort Scale(int value, int maximum)
    {
        int clamped = Math.Clamp(value, 0, maximum);
        return checked((ushort)Math.Floor(clamped * (double)ushort.MaxValue / maximum + 0.5));
    }

    private static string Mode(string ruleset) => ruleset switch
    {
        "osu" => "0",
        "taiko" => "1",
        "fruits" => "2",
        "mania" => "3",
        _ => throw new InvalidOperationException("The revealed ruleset is unsupported.")
    };

    private sealed class ParsedBeatmap
    {
        public Dictionary<string, string> General { get; } = new(StringComparer.Ordinal);
        public Dictionary<string, string> Metadata { get; } = new(StringComparer.Ordinal);
        public Dictionary<string, string> Difficulty { get; } = new(StringComparer.Ordinal);
        public List<string> HitObjects { get; } = [];
    }

    private sealed record SelectedBeatmap(ZipArchiveEntry Entry, ParsedBeatmap Beatmap);
}
