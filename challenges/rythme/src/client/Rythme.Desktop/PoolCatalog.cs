using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Rythme.Desktop;

internal sealed record Pool
{
    [JsonPropertyName("schema")]
    public string Schema { get; init; } = "";

    [JsonPropertyName("songs")]
    public Song[] Songs { get; init; } = [];
}

internal sealed record Song
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("title")]
    public string Title { get; init; } = "";

    [JsonPropertyName("artist")]
    public string Artist { get; init; } = "";

    [JsonPropertyName("ruleset")]
    public string Ruleset { get; init; } = "";

    [JsonPropertyName("bpm")]
    public int Bpm { get; init; }

    [JsonPropertyName("duration_ms")]
    public long DurationMs { get; init; }

    [JsonPropertyName("lead_in_ms")]
    public long LeadInMs { get; init; }

    [JsonPropertyName("note_count")]
    public int NoteCount { get; init; }

    [JsonPropertyName("origin")]
    public string Origin { get; init; } = "";

    [JsonPropertyName("beatmapset_id")]
    public long BeatmapsetId { get; init; }

    [JsonPropertyName("beatmap_id")]
    public long BeatmapId { get; init; }

    [JsonPropertyName("difficulty")]
    public string Difficulty { get; init; } = "";

    [JsonPropertyName("star_rating")]
    public double StarRating { get; init; }

    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("key_count")]
    public int KeyCount { get; init; }

    [JsonPropertyName("mapper")]
    public string Mapper { get; init; } = "";

    [JsonPropertyName("source_url")]
    public string SourceUrl { get; init; } = "";

    [JsonPropertyName("official_url")]
    public string OfficialUrl { get; init; } = "";

    [JsonPropertyName("license")]
    public string License { get; init; } = "";

    [JsonPropertyName("archive_sha256")]
    public string ArchiveSha256 { get; init; } = "";

    [JsonPropertyName("notes")]
    public Note[] Notes { get; init; } = [];
}

internal sealed record Note
{
    [JsonPropertyName("at_ms")]
    public long AtMs { get; init; }

    [JsonPropertyName("action")]
    public ushort Action { get; init; }

    [JsonPropertyName("x")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public ushort X { get; init; }

    [JsonPropertyName("y")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public ushort Y { get; init; }
}

internal static class PoolCatalog
{
    public static Pool Load()
    {
        using Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Rythme.pool.json")
                              ?? throw new InvalidOperationException("Embedded tournament pool is missing.");
        Pool pool = JsonSerializer.Deserialize(stream, RythmeJsonContext.Default.Pool)
                    ?? throw new InvalidOperationException("Embedded tournament pool is invalid.");
        Validate(pool);
        return pool;
    }

    internal static void Validate(Pool pool)
    {
        ArgumentNullException.ThrowIfNull(pool);
        if (string.IsNullOrWhiteSpace(pool.Schema))
            throw new InvalidOperationException("Embedded tournament pool has no schema.");
        if (pool.Songs == null || pool.Songs.Length == 0)
            throw new InvalidOperationException("Embedded tournament pool has no songs.");

        var songIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (Song? song in pool.Songs)
        {
            if (song == null)
                throw new InvalidOperationException("Embedded tournament pool contains a null song.");
            if (string.IsNullOrWhiteSpace(song.Id) || !songIds.Add(song.Id))
                throw new InvalidOperationException("Embedded tournament pool contains an empty or duplicate song ID.");
            if (string.IsNullOrWhiteSpace(song.Title) ||
                string.IsNullOrWhiteSpace(song.Artist) ||
                string.IsNullOrWhiteSpace(song.Difficulty) ||
                string.IsNullOrWhiteSpace(song.ArchiveSha256) ||
                song.Bpm <= 0 ||
                song.DurationMs <= 0 ||
                song.NoteCount <= 0 ||
                song.BeatmapsetId <= 0 ||
                song.BeatmapId <= 0 ||
                song.Ruleset is not ("osu" or "taiko" or "fruits" or "mania"))
            {
                throw new InvalidOperationException($"Embedded tournament song {song.Id} has invalid metadata.");
            }
            if (song.ArchiveSha256.Length != SHA256.HashSizeInBytes * 2 ||
                song.ArchiveSha256.Any(character => !Uri.IsHexDigit(character)))
                throw new InvalidOperationException($"Embedded tournament song {song.Id} has an invalid archive checksum.");
        }
    }

    public static Song Resolve(Pool pool, Selection selection)
    {
        ArgumentNullException.ThrowIfNull(pool);
        ArgumentNullException.ThrowIfNull(selection);
        if (pool.Songs == null)
            throw new InvalidOperationException("Embedded tournament pool song data is unavailable.");
        Song song = pool.Songs.SingleOrDefault(candidate => candidate.Id == selection.SongId)
                    ?? throw new InvalidOperationException("The revealed exact difficulty is absent from the embedded pool.");
        if (song.Bpm <= 0 ||
            song.DurationMs <= 0 ||
            song.NoteCount <= 0 ||
            song.Ruleset != selection.Ruleset ||
            song.BeatmapId != selection.BeatmapId ||
            song.BeatmapsetId != selection.BeatmapsetId)
            throw new InvalidOperationException("The revealed exact difficulty metadata is inconsistent.");
        return song;
    }

    public static Note[] ExtractedChart(Song song)
    {
        ArgumentNullException.ThrowIfNull(song);
        return song.Notes?.ToArray()
               ?? throw new InvalidOperationException("Embedded tournament song chart data is unavailable.");
    }

    public static string ChartDigest(Song song, string ruleset, Note[] notes)
    {
        ArgumentNullException.ThrowIfNull(song);
        ArgumentException.ThrowIfNullOrWhiteSpace(ruleset);
        ArgumentNullException.ThrowIfNull(notes);
        var payload = new ChartPayload
        {
            Schema = "rythme-chart-v2",
            SongId = song.Id,
            BeatmapId = song.BeatmapId,
            Ruleset = ruleset,
            Notes = notes
        };
        byte[] encoded = JsonSerializer.SerializeToUtf8Bytes(payload, RythmeJsonContext.Default.ChartPayload);
        return Convert.ToHexString(SHA256.HashData(encoded)).ToLowerInvariant();
    }

}

internal sealed record ChartPayload
{
    [JsonPropertyName("schema")]
    public string Schema { get; init; } = "";

    [JsonPropertyName("song_id")]
    public string SongId { get; init; } = "";

    [JsonPropertyName("beatmap_id")]
    public long BeatmapId { get; init; }

    [JsonPropertyName("ruleset")]
    public string Ruleset { get; init; } = "";

    [JsonPropertyName("notes")]
    public Note[] Notes { get; init; } = [];
}
