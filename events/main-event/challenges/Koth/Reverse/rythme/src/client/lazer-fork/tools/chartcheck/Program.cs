using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Rythme.Desktop;

internal static class Program
{
    public static int Main(string[] args)
    {
        if (args.Length != 2)
        {
            Console.Error.WriteLine("usage: chartcheck <server-pool.json> <beatmap-cache-directory>");
            return 2;
        }

        Pool pool = JsonSerializer.Deserialize<Pool>(File.ReadAllBytes(args[0]))
                    ?? throw new InvalidDataException("pool could not be decoded");
        if (pool.Songs.Length == 0)
            throw new InvalidDataException("pool has no songs");

        long checkedNotes = 0;
        foreach (Song song in pool.Songs)
        {
            string archivePath = Path.Combine(args[1], $"{song.BeatmapsetId}.osz");
            byte[] archive = File.ReadAllBytes(archivePath);
            ValidatedBeatmap files = validateFiles(archivePath, song.BeatmapId);
            var selection = new Selection
            {
                Ruleset = song.Ruleset,
                BeatmapsetId = song.BeatmapsetId,
                BeatmapId = song.BeatmapId,
                NoteCount = song.NoteCount,
            };
            Note[] extracted = BeatmapArchiveChart.Extract(
                archive,
                selection);
            string selectedMd5 = BeatmapArchiveChart.SelectedBeatmapMd5(archive, selection);
            string poolSelectedMd5 = BeatmapArchiveChart.SelectedBeatmapMd5(
                archive,
                song.BeatmapId,
                song.BeatmapsetId,
                song.Ruleset);
            if (!string.Equals(selectedMd5, poolSelectedMd5, StringComparison.Ordinal))
                throw new InvalidDataException($"{song.Id}: pre-import and reveal selected different charts");
            if (!string.Equals(selectedMd5, files.ChartMd5, StringComparison.Ordinal))
                throw new InvalidDataException($"{song.Id}: selected chart MD5 changed");
            if (selectedMd5.Length != 32 || selectedMd5.Any(character => !char.IsAsciiHexDigit(character) || char.IsUpper(character)))
                throw new InvalidDataException($"{song.Id}: selected chart MD5 is not canonical lowercase hex");

            if (extracted.Length != song.Notes.Length)
                throw new InvalidDataException($"{song.Id}: note count changed");
            for (int index = 0; index < extracted.Length; index++)
            {
                Note expected = song.Notes[index];
                Note actual = extracted[index];
                if (actual.AtMs != expected.AtMs ||
                    actual.Action != expected.Action ||
                    actual.X != expected.X ||
                    actual.Y != expected.Y)
                    throw new InvalidDataException($"{song.Id}: note {index} changed");
            }

            checkedNotes += extracted.Length;
            Console.WriteLine($"[+] {song.Id}: {extracted.Length} notes match; chart {selectedMd5}; audio {files.Audio}");
        }

        Console.WriteLine($"[+] verified {pool.Songs.Length} exact difficulties and {checkedNotes} notes");
        return 0;
    }

    private static ValidatedBeatmap validateFiles(string archivePath, long beatmapId)
    {
        using var archive = ZipFile.OpenRead(archivePath);
        ZipArchiveEntry? selectedChart = null;
        string? selectedChartText = null;
        foreach (ZipArchiveEntry entry in archive.Entries.Where(entry =>
                     entry.FullName.EndsWith(".osu", StringComparison.OrdinalIgnoreCase)))
        {
            using var reader = new StreamReader(entry.Open(), Encoding.UTF8, true);
            string text = reader.ReadToEnd();
            if (!text.Split('\n').Any(line => metadataValue(line, "BeatmapID") == beatmapId.ToString()))
                continue;

            if (selectedChart != null)
                throw new InvalidDataException($"{archivePath}: multiple charts have BeatmapID {beatmapId}");
            selectedChart = entry;
            selectedChartText = text;
        }

        if (selectedChart == null || selectedChartText == null)
            throw new InvalidDataException($"{archivePath}: BeatmapID {beatmapId} is missing");

        string section = string.Empty;
        string? audioFilename = null;
        foreach (string rawLine in selectedChartText.Split('\n'))
        {
            string line = rawLine.Trim();
            if (line.StartsWith('[') && line.EndsWith(']'))
            {
                section = line;
                continue;
            }
            if (section == "[General]")
                audioFilename ??= metadataValue(line, "AudioFilename");
        }
        if (string.IsNullOrWhiteSpace(audioFilename))
            throw new InvalidDataException($"{archivePath}: selected chart has no AudioFilename");

        string normalized = audioFilename.Replace('\\', '/').Trim();
        if (normalized.StartsWith('/') || normalized.Split('/').Any(part => part is "" or "." or ".."))
            throw new InvalidDataException($"{archivePath}: selected chart has an unsafe AudioFilename");

        ZipArchiveEntry? audio = archive.Entries.SingleOrDefault(entry =>
            string.Equals(entry.FullName.Replace('\\', '/'), normalized, StringComparison.OrdinalIgnoreCase));
        if (audio == null || audio.Length is <= 0 or > 64 * 1024 * 1024)
            throw new InvalidDataException($"{archivePath}: selected audio {normalized} is missing or invalid");

        string extension = Path.GetExtension(audio.FullName).ToLowerInvariant();
        if (extension is not (".mp3" or ".ogg" or ".wav"))
            throw new InvalidDataException($"{archivePath}: unsupported selected audio format {extension}");

        using Stream rawChart = selectedChart.Open();
        string chartMd5 = Convert.ToHexString(MD5.HashData(rawChart)).ToLowerInvariant();
        return new ValidatedBeatmap($"{audio.FullName} ({audio.Length:N0} bytes)", chartMd5);
    }

    private static string? metadataValue(string line, string key)
    {
        int separator = line.IndexOf(':');
        if (separator <= 0 || !line[..separator].Trim().Equals(key, StringComparison.Ordinal))
            return null;
        return line[(separator + 1)..].Trim();
    }
}

internal sealed record ValidatedBeatmap(string Audio, string ChartMd5);

internal sealed record Pool
{
    [JsonPropertyName("songs")]
    public Song[] Songs { get; init; } = [];
}

internal sealed record Song
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("ruleset")]
    public string Ruleset { get; init; } = "";

    [JsonPropertyName("note_count")]
    public int NoteCount { get; init; }

    [JsonPropertyName("beatmapset_id")]
    public long BeatmapsetId { get; init; }

    [JsonPropertyName("beatmap_id")]
    public long BeatmapId { get; init; }

    [JsonPropertyName("notes")]
    public Note[] Notes { get; init; } = [];
}

internal sealed record Selection
{
    public string Ruleset { get; init; } = "";
    public int NoteCount { get; init; }
    public long BeatmapsetId { get; init; }
    public long BeatmapId { get; init; }
}

internal sealed record Note
{
    [JsonPropertyName("at_ms")]
    public long AtMs { get; init; }

    [JsonPropertyName("action")]
    public ushort Action { get; init; }

    [JsonPropertyName("x")]
    public ushort X { get; init; }

    [JsonPropertyName("y")]
    public ushort Y { get; init; }
}
