using System.Net;
using System.Net.Http.Headers;

namespace Rythme.Desktop;

internal sealed class BeatmapMirrorClient : IDisposable
{
    private const string allowedMirrorHost = "catboy.best";

    private readonly HttpClient client;

    public BeatmapMirrorClient()
        : this(new HttpClientHandler
        {
            AllowAutoRedirect = false,
            UseCookies = false,
        })
    {
    }

    internal BeatmapMirrorClient(HttpMessageHandler handler)
    {
        client = new HttpClient(handler, disposeHandler: true)
        {
            Timeout = TimeSpan.FromMinutes(2),
        };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("Rythme", "1.0"));
    }

    public async Task<byte[]> DownloadAsync(
        BeatmapCacheEntry entry,
        Func<Action<double>?, CancellationToken, Task<byte[]>> coordinatorFallback,
        Action<double>? onProgress,
        CancellationToken cancellationToken)
    {
        if (tryPinnedMirrorUri(entry.SourceUrl, out Uri mirror))
        {
            try
            {
                byte[] archive = await downloadMirrorAsync(mirror, onProgress, cancellationToken).ConfigureAwait(false);
                if (BeatmapArchiveCache.MatchesExpectedHash(archive, entry.ArchiveSha256))
                    return archive;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception error) when (error is HttpRequestException or IOException or TaskCanceledException)
            {
                // A mirror failure must not keep a player out of the event. The
                // authenticated coordinator remains the trusted fallback.
            }
        }

        onProgress?.Invoke(0);
        return await coordinatorFallback(onProgress, cancellationToken).ConfigureAwait(false);
    }

    private async Task<byte[]> downloadMirrorAsync(
        Uri source,
        Action<double>? onProgress,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, source);
        using HttpResponseMessage response = await client
                                                   .SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                                                   .ConfigureAwait(false);
        if (response.StatusCode is >= HttpStatusCode.MultipleChoices and < HttpStatusCode.BadRequest)
            throw new HttpRequestException("The pinned beatmap mirror attempted an unapproved redirect.");
        response.EnsureSuccessStatusCode();

        long? contentLength = response.Content.Headers.ContentLength;
        if (contentLength > BeatmapArchiveCache.MaximumArchiveBytes)
            throw new HttpRequestException("The pinned beatmap mirror returned an oversized package.");

        using Stream sourceStream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var destination = contentLength is > 0 and <= int.MaxValue
            ? new MemoryStream((int)contentLength.Value)
            : new MemoryStream();
        byte[] buffer = new byte[81920];
        long received = 0;
        onProgress?.Invoke(0);

        while (true)
        {
            int read = await sourceStream.ReadAsync(buffer.AsMemory(), cancellationToken).ConfigureAwait(false);
            if (read == 0)
                break;
            received += read;
            if (received > BeatmapArchiveCache.MaximumArchiveBytes)
                throw new HttpRequestException("The pinned beatmap mirror returned an oversized package.");
            await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            if (contentLength is > 0)
                onProgress?.Invoke(Math.Clamp(received / (double)contentLength.Value, 0, 1));
        }

        onProgress?.Invoke(1);
        return destination.ToArray();
    }

    private static bool tryPinnedMirrorUri(string value, out Uri source)
    {
        source = null!;
        if (!Uri.TryCreate(value, UriKind.Absolute, out Uri? candidate) ||
            candidate.Scheme != Uri.UriSchemeHttps ||
            !string.Equals(candidate.IdnHost, allowedMirrorHost, StringComparison.OrdinalIgnoreCase) ||
            !candidate.IsDefaultPort ||
            candidate.UserInfo.Length != 0 ||
            !candidate.AbsolutePath.StartsWith("/d/", StringComparison.Ordinal) ||
            candidate.Query.Length != 0 ||
            candidate.Fragment.Length != 0)
        {
            return false;
        }

        if (candidate.AbsolutePath[3..].Length == 0 || !candidate.AbsolutePath[3..].All(char.IsAsciiDigit))
            return false;

        source = candidate;
        return true;
    }

    public void Dispose() => client.Dispose();
}
