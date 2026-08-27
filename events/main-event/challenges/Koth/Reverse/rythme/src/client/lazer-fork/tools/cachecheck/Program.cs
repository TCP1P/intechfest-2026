using System.Security.Cryptography;
using Rythme.Desktop;

string root = Path.Combine(Path.GetTempPath(), "rythme-cachecheck-" + Guid.NewGuid().ToString("N"));
try
{
    var payloads = new Dictionary<string, byte[]>(StringComparer.Ordinal)
    {
        ["osu-one"] = "first pinned archive"u8.ToArray(),
        ["taiko-two"] = "second pinned archive"u8.ToArray(),
        ["mania-three"] = "third pinned archive"u8.ToArray(),
    };
    BeatmapCacheEntry[] entries = payloads
        .Select(pair => new BeatmapCacheEntry(pair.Key, Convert.ToHexString(SHA256.HashData(pair.Value)).ToLowerInvariant()))
        .ToArray();
    var cache = new BeatmapArchiveCache(root);
    var requests = new Dictionary<string, int>(StringComparer.Ordinal);

    Task<byte[]> download(BeatmapCacheEntry entry, Action<double>? progress, CancellationToken _)
    {
        requests[entry.SongId] = requests.GetValueOrDefault(entry.SongId) + 1;
        progress?.Invoke(0.5);
        return Task.FromResult(payloads[entry.SongId].ToArray());
    }

    var progressEvents = new List<BeatmapPoolProgress>();
    var prepared = new List<string>();
    await cache.WarmAsync(
        entries,
        download,
        progressEvents.Add,
        CancellationToken.None,
        (entry, archive, _) =>
        {
            require(archive.SequenceEqual(payloads[entry.SongId]), "warm preparation received the wrong archive");
            prepared.Add(entry.SongId);
            return Task.CompletedTask;
        });
    require(requests.Values.Sum() == entries.Length, "first warm did not download each archive exactly once");
    require(prepared.SequenceEqual(entries.Select(entry => entry.SongId)),
        "warm preparation did not process every verified archive in order");
    require(progressEvents.Last().Completed == entries.Length && progressEvents.Last().Overall == 1,
        "first warm did not report completion");

    await cache.WarmAsync(entries, download, null, CancellationToken.None);
    require(requests.Values.Sum() == entries.Length, "warm cache triggered redundant downloads");

    try
    {
        await cache.WarmAsync([null!], download, null, CancellationToken.None);
        throw new InvalidOperationException("null beatmap cache entry was accepted");
    }
    catch (InvalidOperationException error) when (error.Message.Contains("null entry", StringComparison.Ordinal))
    {
    }

    await File.WriteAllTextAsync(cache.PathFor(entries[1].ArchiveSha256), "corrupted");
    byte[] repaired = await cache.GetAsync(entries[1], download, null, CancellationToken.None);
    require(repaired.SequenceEqual(payloads[entries[1].SongId]), "corrupted archive was not repaired");
    require(requests[entries[1].SongId] == 2, "corrupted archive did not trigger one replacement download");

    try
    {
        await cache.GetAsync(new BeatmapCacheEntry("bad", "not-a-sha256"), download, null, CancellationToken.None);
        throw new InvalidOperationException("invalid checksum was accepted");
    }
    catch (InvalidOperationException error) when (error.Message.Contains("invalid beatmap archive checksum", StringComparison.Ordinal))
    {
    }

    await verifyMirrorSecurityAndFallback();

    Console.WriteLine("beatmap cache and mirror checks passed");
}
finally
{
    if (Directory.Exists(root))
        Directory.Delete(root, true);
}

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static async Task verifyMirrorSecurityAndFallback()
{
    byte[] mirrorPayload = "verified mirror archive"u8.ToArray();
    string mirrorHash = Convert.ToHexString(SHA256.HashData(mirrorPayload)).ToLowerInvariant();
    int mirrorRequests = 0;
    int fallbackRequests = 0;
    var successHandler = new StubHttpHandler(request =>
    {
        mirrorRequests++;
        require(request.RequestUri == new Uri("https://catboy.best/d/12345"), "mirror request URI changed");
        require(request.Headers.Authorization == null, "coordinator authorization leaked to the public mirror");
        return new HttpResponseMessage(System.Net.HttpStatusCode.OK)
        {
            Content = new ByteArrayContent(mirrorPayload),
        };
    });
    using (var mirror = new BeatmapMirrorClient(successHandler))
    {
        byte[] result = await mirror.DownloadAsync(
            new BeatmapCacheEntry("mirror", mirrorHash, "https://catboy.best/d/12345"),
            (_, _) =>
            {
                fallbackRequests++;
                return Task.FromResult("unexpected fallback"u8.ToArray());
            },
            null,
            CancellationToken.None);
        require(result.SequenceEqual(mirrorPayload), "verified mirror payload was not returned");
    }
    require(mirrorRequests == 1 && fallbackRequests == 0, "healthy mirror did not stay on the direct path");

    byte[] fallbackPayload = "verified coordinator archive"u8.ToArray();
    string fallbackHash = Convert.ToHexString(SHA256.HashData(fallbackPayload)).ToLowerInvariant();
    fallbackRequests = 0;
    using (var mirror = new BeatmapMirrorClient(new StubHttpHandler(_ => new HttpResponseMessage(System.Net.HttpStatusCode.OK)
    {
        Content = new ByteArrayContent("tampered mirror archive"u8.ToArray()),
    })))
    {
        byte[] result = await mirror.DownloadAsync(
            new BeatmapCacheEntry("fallback", fallbackHash, "https://catboy.best/d/67890"),
            (_, _) =>
            {
                fallbackRequests++;
                return Task.FromResult(fallbackPayload);
            },
            null,
            CancellationToken.None);
        require(result.SequenceEqual(fallbackPayload), "checksum mismatch did not use the coordinator fallback");
    }
    require(fallbackRequests == 1, "checksum mismatch did not invoke exactly one fallback");

    int redirectRequests = 0;
    fallbackRequests = 0;
    using (var mirror = new BeatmapMirrorClient(new StubHttpHandler(_ =>
           {
               redirectRequests++;
               var response = new HttpResponseMessage(System.Net.HttpStatusCode.Redirect);
               response.Headers.Location = new Uri("https://untrusted.example/archive.osz");
               return response;
           })))
    {
        byte[] result = await mirror.DownloadAsync(
            new BeatmapCacheEntry("redirect", fallbackHash, "https://catboy.best/d/67890"),
            (_, _) =>
            {
                fallbackRequests++;
                return Task.FromResult(fallbackPayload);
            },
            null,
            CancellationToken.None);
        require(result.SequenceEqual(fallbackPayload), "redirect did not use the coordinator fallback");
    }
    require(redirectRequests == 1, "mirror redirect was followed");
    require(fallbackRequests == 1, "mirror redirect did not invoke exactly one fallback");

    int unapprovedRequests = 0;
    fallbackRequests = 0;
    using (var mirror = new BeatmapMirrorClient(new StubHttpHandler(_ =>
           {
               unapprovedRequests++;
               return new HttpResponseMessage(System.Net.HttpStatusCode.OK);
           })))
    {
        byte[] result = await mirror.DownloadAsync(
            new BeatmapCacheEntry("unapproved", fallbackHash, "https://untrusted.example/d/67890"),
            (_, _) =>
            {
                fallbackRequests++;
                return Task.FromResult(fallbackPayload);
            },
            null,
            CancellationToken.None);
        require(result.SequenceEqual(fallbackPayload), "unapproved source did not use the coordinator fallback");
    }
    require(unapprovedRequests == 0, "client contacted an unapproved mirror host");
    require(fallbackRequests == 1, "unapproved source did not invoke exactly one fallback");
}
