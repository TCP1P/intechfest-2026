using System.IO.Compression;
using System.Text.Json;
using System.Text.Json.Serialization;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Formats;
using osu.Game.IO;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Catch;
using osu.Game.Rulesets.Mania;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Objects;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Taiko;

if (args.Length != 2)
    throw new ArgumentException("usage: playablecheck <server-pool.json> <beatmap-cache-directory>");

Pool pool = JsonSerializer.Deserialize<Pool>(File.ReadAllBytes(args[0]))
            ?? throw new InvalidDataException("pool could not be decoded");
long totalJudgements = 0;

foreach (Song song in pool.Songs)
{
    string archivePath = Path.Combine(args[1], $"{song.BeatmapsetId}.osz");
    Beatmap decoded = loadSelectedBeatmap(archivePath, song.BeatmapId);
    Ruleset ruleset = song.Ruleset switch
    {
        "osu" => new OsuRuleset(),
        "taiko" => new TaikoRuleset(),
        "fruits" => new CatchRuleset(),
        "mania" => new ManiaRuleset(),
        _ => throw new InvalidDataException($"{song.Id}: unsupported ruleset {song.Ruleset}"),
    };
    IBeatmap playable = new FlatWorkingBeatmap(decoded)
                        .GetPlayableBeatmap(ruleset.RulesetInfo, Array.Empty<Mod>());
    HitObject[] judgements = flatten(playable.HitObjects).ToArray();
    if (judgements.Length == 0)
        throw new InvalidDataException($"{song.Id}: playable chart has no judgements");
    verifyArenaNoteMapping(song, judgements);

    double latestEnd = judgements.Max(item => item.GetEndTime());
    double latestMiss = judgements.Max(item => item.GetEndTime() + Math.Max(0, item.MaximumJudgementOffset));
    if (!double.IsFinite(latestEnd) || !double.IsFinite(latestMiss))
        throw new InvalidDataException($"{song.Id}: playable chart has a non-finite completion time");
    if (latestMiss >= song.DurationMs)
        throw new InvalidDataException(
            $"{song.Id}: latest miss at {latestMiss:N0} ms exceeds the {song.DurationMs:N0} ms arena deadline");

    totalJudgements += judgements.Length;
    Console.WriteLine(
        $"[+] {song.Id}: {judgements.Length:N0} judgements; " +
        $"last object {latestEnd:N0} ms; completion margin {song.DurationMs - latestMiss:N0} ms");
}

Console.WriteLine($"[+] verified completion bounds for {pool.Songs.Length} maps and {totalJudgements:N0} judgements");

static void verifyArenaNoteMapping(Song song, IEnumerable<HitObject> judgements)
{
    var availableByTime = judgements
                          .GroupBy(item => (long)Math.Round(item.StartTime))
                          .ToDictionary(group => group.Key, group => group.Count());

    for (int noteIndex = 0; noteIndex < song.Notes.Length; noteIndex++)
    {
        long target = song.Notes[noteIndex].AtMs;
        bool matched = false;
        for (int distance = 0; distance <= 5 && !matched; distance++)
        {
            matched = tryConsume(target - distance);
            if (!matched && distance != 0)
                matched = tryConsume(target + distance);
        }
        if (!matched)
            throw new InvalidDataException(
                $"{song.Id}: arena note {noteIndex} at {target} ms has no matching lazer judgement");
    }

    bool tryConsume(long time)
    {
        if (!availableByTime.TryGetValue(time, out int count) || count == 0)
            return false;
        availableByTime[time] = count - 1;
        return true;
    }
}

static Beatmap loadSelectedBeatmap(string archivePath, long beatmapId)
{
    using var archive = ZipFile.OpenRead(archivePath);
    Beatmap? selected = null;
    foreach (ZipArchiveEntry entry in archive.Entries.Where(item =>
                 item.FullName.EndsWith(".osu", StringComparison.OrdinalIgnoreCase)))
    {
        using Stream stream = entry.Open();
        using var reader = new LineBufferedReader(stream);
        var decoder = new LegacyBeatmapDecoder { ApplyOffsets = false };
        Beatmap candidate = decoder.Decode(reader);
        if (candidate.BeatmapInfo.OnlineID != beatmapId)
            continue;
        if (selected != null)
            throw new InvalidDataException($"{archivePath}: duplicate BeatmapID {beatmapId}");
        selected = candidate;
    }
    return selected ?? throw new InvalidDataException($"{archivePath}: BeatmapID {beatmapId} is missing");
}

static IEnumerable<HitObject> flatten(IEnumerable<HitObject> roots)
{
    foreach (HitObject root in roots)
    {
        foreach (HitObject nested in flatten(root.NestedHitObjects))
            yield return nested;
        yield return root;
    }
}

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

    [JsonPropertyName("duration_ms")]
    public long DurationMs { get; init; }

    [JsonPropertyName("beatmapset_id")]
    public long BeatmapsetId { get; init; }

    [JsonPropertyName("beatmap_id")]
    public long BeatmapId { get; init; }

    [JsonPropertyName("notes")]
    public Note[] Notes { get; init; } = [];
}

internal sealed record Note
{
    [JsonPropertyName("at_ms")]
    public long AtMs { get; init; }
}
