using System.Security.Cryptography;

namespace Rythme.Desktop;

internal sealed record BeatmapCacheEntry(string SongId, string ArchiveSha256, string SourceUrl = "");

internal sealed record BeatmapPoolProgress(int Completed, int Total, string SongId, double CurrentDownload)
{
    public double Overall => Total == 0
        ? 1
        : Math.Clamp((Completed + CurrentDownload) / Total, 0, 1);
}

internal sealed class BeatmapArchiveCache
{
    internal const int MaximumArchiveBytes = 96 * 1024 * 1024;

    private readonly string directory;

    public BeatmapArchiveCache(string directory)
    {
        if (string.IsNullOrWhiteSpace(directory))
            throw new ArgumentException("A beatmap cache directory is required.", nameof(directory));

        this.directory = Path.GetFullPath(directory);
    }

    public static string DefaultDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Rythme",
        "BeatmapCache",
        "v2");

    public async Task WarmAsync(
        IEnumerable<BeatmapCacheEntry> entries,
        Func<BeatmapCacheEntry, Action<double>?, CancellationToken, Task<byte[]>> download,
        Action<BeatmapPoolProgress>? onProgress,
        CancellationToken cancellationToken,
        Func<BeatmapCacheEntry, byte[], CancellationToken, Task>? prepare = null)
    {
        ArgumentNullException.ThrowIfNull(entries);
        ArgumentNullException.ThrowIfNull(download);
        BeatmapCacheEntry[] pool = entries.ToArray();
        if (pool.Length == 0)
            throw new InvalidOperationException("The event beatmap pool is empty.");
        if (pool.Any(entry => entry == null))
            throw new InvalidOperationException("The event beatmap pool contains a null entry.");
        if (pool.Any(entry => string.IsNullOrWhiteSpace(entry.SongId)))
            throw new InvalidOperationException("The event beatmap pool contains an empty song ID.");
        if (pool.Select(entry => entry.SongId).Distinct(StringComparer.Ordinal).Count() != pool.Length)
            throw new InvalidOperationException("The event beatmap pool contains duplicate song IDs.");

        for (int index = 0; index < pool.Length; index++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            BeatmapCacheEntry entry = pool[index];
            onProgress?.Invoke(new BeatmapPoolProgress(index, pool.Length, entry.SongId, 0));
            byte[] archive = await GetAsync(
                    entry,
                    download,
                    current => onProgress?.Invoke(new BeatmapPoolProgress(index, pool.Length, entry.SongId, current)),
                    cancellationToken)
                .ConfigureAwait(false);
            if (prepare != null)
                await prepare(entry, archive, cancellationToken).ConfigureAwait(false);
            onProgress?.Invoke(new BeatmapPoolProgress(index + 1, pool.Length, entry.SongId, 0));
        }
    }

    public async Task<byte[]> GetAsync(
        BeatmapCacheEntry entry,
        Func<BeatmapCacheEntry, Action<double>?, CancellationToken, Task<byte[]>> download,
        Action<double>? onProgress,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(entry);
        ArgumentNullException.ThrowIfNull(download);
        string expectedHash = normalizeHash(entry.ArchiveSha256);
        Directory.CreateDirectory(directory);
        string destination = Path.Combine(directory, expectedHash + ".osz");

        byte[]? cached = await readVerifiedAsync(destination, expectedHash, cancellationToken).ConfigureAwait(false);
        if (cached != null)
        {
            onProgress?.Invoke(1);
            return cached;
        }

        byte[] archive = await download(entry, onProgress, cancellationToken).ConfigureAwait(false)
                         ?? throw new InvalidOperationException("The beatmap download returned no package data.");
        validateArchive(archive, expectedHash);

        string temporary = Path.Combine(directory, $".{expectedHash}.{Guid.NewGuid():N}.tmp");
        try
        {
            await File.WriteAllBytesAsync(temporary, archive, cancellationToken).ConfigureAwait(false);
            File.Move(temporary, destination, true);
        }
        finally
        {
            if (File.Exists(temporary))
                File.Delete(temporary);
        }

        onProgress?.Invoke(1);
        return archive;
    }

    internal string PathFor(string archiveSha256) =>
        Path.Combine(directory, normalizeHash(archiveSha256) + ".osz");

    private static async Task<byte[]?> readVerifiedAsync(
        string path,
        string expectedHash,
        CancellationToken cancellationToken)
    {
        if (!File.Exists(path))
            return null;

        byte[] archive;
        try
        {
            var info = new FileInfo(path);
            if (info.Length is <= 0 or > MaximumArchiveBytes)
            {
                File.Delete(path);
                return null;
            }
            archive = await File.ReadAllBytesAsync(path, cancellationToken).ConfigureAwait(false);
        }
        catch (FileNotFoundException)
        {
            return null;
        }
        catch (DirectoryNotFoundException)
        {
            return null;
        }

        if (hash(archive) == expectedHash)
            return archive;

        File.Delete(path);
        return null;
    }

    private static void validateArchive(byte[] archive, string expectedHash)
    {
        if (archive.Length is <= 0 or > MaximumArchiveBytes)
            throw new InvalidOperationException("The downloaded beatmap package has an invalid size.");
        if (hash(archive) != expectedHash)
            throw new InvalidOperationException("The downloaded beatmap package failed its pool checksum.");
    }

    private static string normalizeHash(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
            throw new InvalidOperationException("The event pool contains an empty beatmap archive checksum.");
        string normalized = value.Trim().ToLowerInvariant();
        if (normalized.Length != SHA256.HashSizeInBytes * 2 || normalized.Any(character => !Uri.IsHexDigit(character)))
            throw new InvalidOperationException("The event pool contains an invalid beatmap archive checksum.");
        return normalized;
    }

    private static string hash(byte[] archive) =>
        Convert.ToHexString(SHA256.HashData(archive)).ToLowerInvariant();

    internal static bool MatchesExpectedHash(byte[] archive, string expectedHash)
    {
        try
        {
            return archive is not null &&
                   archive.Length is > 0 and <= MaximumArchiveBytes &&
                   hash(archive) == normalizeHash(expectedHash);
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }
}
