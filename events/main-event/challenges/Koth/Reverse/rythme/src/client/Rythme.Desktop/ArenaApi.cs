using System.Diagnostics;
using System.Globalization;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;

namespace Rythme.Desktop;

internal sealed class ArenaApi : IDisposable, IArenaInputTransport
{
    private readonly HttpClient client;
    private readonly SemaphoreSlim channelGate = new(1, 1);
    private SecureChannel? channel;
    private readonly JsonSerializerOptions json = new(JsonSerializerDefaults.Web)
    {
        TypeInfoResolver = RythmeJsonContext.Default
    };

    public ArenaApi(Uri server)
    {
        client = new HttpClient
        {
            BaseAddress = server,
            Timeout = TimeSpan.FromMinutes(2)
        };
    }

    public async Task<SessionResponse> CreateLocalSessionAsync(string teamId, string teamName, CancellationToken cancellationToken)
    {
        using ClientHandshake handshake = ClientHandshake.Create();
        SessionResponse session = await PostPublicAsync<SessionResponse, LocalSessionRequest>("/api/v2/session/local", new LocalSessionRequest
        {
            TeamId = teamId,
            TeamName = teamName,
            ClientPublicKey = handshake.PublicKey,
            ClientNonce = handshake.Nonce
        }, cancellationToken).ConfigureAwait(false);
        channel = handshake.Complete(session);
        session.Channel = channel;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", session.AccessToken);
        return session;
    }

    public async Task<SessionResponse> CreateRctfSessionAsync(string teamToken, CancellationToken cancellationToken)
    {
        using ClientHandshake handshake = ClientHandshake.Create();
        SessionResponse session = await PostPublicAsync<SessionResponse, RctfSessionRequest>("/api/v2/session/rctf", new RctfSessionRequest
        {
            TeamToken = teamToken,
            ClientPublicKey = handshake.PublicKey,
            ClientNonce = handshake.Nonce
        }, cancellationToken).ConfigureAwait(false);
        channel = handshake.Complete(session);
        session.Channel = channel;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", session.AccessToken);
        return session;
    }

    public Task<ArenaView> StateAsync(CancellationToken cancellationToken) =>
        GetAsync<ArenaView>("/api/v2/state", cancellationToken);

    public async Task<byte[]> DownloadBeatmapAsync(
        string songId,
        Action<double>? onProgress,
        CancellationToken cancellationToken)
    {
        const int maximumArchiveBytes = 96 * 1024 * 1024;
        string path = $"/api/v1/beatmaps/{Uri.EscapeDataString(songId)}.osz";
        using HttpResponseMessage response = await client
                                                   .GetAsync(path, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                                                   .ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            byte[] errorBody = await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
            throw new ArenaProtocolException(
                (int)response.StatusCode,
                ReadProblemDetail(errorBody, "Coordinator rejected the beatmap download."));
        }

        long? contentLength = response.Content.Headers.ContentLength;
        if (contentLength > maximumArchiveBytes)
            throw new ArenaProtocolException((int)response.StatusCode, "Coordinator returned an oversized beatmap package.");

        using Stream source = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var destination = contentLength is > 0 and <= int.MaxValue
            ? new MemoryStream((int)contentLength.Value)
            : new MemoryStream();
        byte[] buffer = new byte[81920];
        long received = 0;
        onProgress?.Invoke(0);

        while (true)
        {
            int read = await source.ReadAsync(buffer.AsMemory(), cancellationToken).ConfigureAwait(false);
            if (read == 0)
                break;

            received += read;
            if (received > maximumArchiveBytes)
                throw new ArenaProtocolException((int)response.StatusCode, "Coordinator returned an oversized beatmap package.");

            await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            if (contentLength is > 0)
                onProgress?.Invoke(Math.Clamp(received / (double)contentLength.Value, 0, 1));
        }

        onProgress?.Invoke(1);
        return destination.ToArray();
    }

    public Task<EnrollResponse> EnrollAsync(CancellationToken cancellationToken) =>
        SendArenaFrameAsync(
            ArenaFrame.Empty(NativePolicyIds.FrameEnroll),
            NativePolicyIds.FrameEnroll,
            ArenaFrame.ReadEnroll,
            cancellationToken);

    public async Task<ReadyBinding> ReadyAsync(
        string ticket,
        string ruleset,
        string nonce,
        AppliedModSet requested,
        CancellationToken cancellationToken)
    {
        SecureChannel active = channel ?? throw new ArenaProtocolException(0, "Connect before readying.");
        byte[] payload = active.BuildModifierEnvelope(ruleset, requested.Acronym, nonce);
        try
        {
            return await SendArenaFrameAsync(
                ArenaFrame.Ready(ticket, payload),
                NativePolicyIds.FrameReady,
                ArenaFrame.ReadReady,
                cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(payload);
        }
    }

    public Task<InputResponse> SendFramesAsync(IReadOnlyList<InputFrame> frames, CancellationToken cancellationToken) =>
        SendArenaFrameAsync(ArenaFrame.Input(frames), NativePolicyIds.FrameInput, ArenaFrame.ReadInput, cancellationToken);

    public Task<CueResponse> CuesAsync(string ticket, CancellationToken cancellationToken) =>
        SendArenaFrameAsync(ArenaFrame.Cues(ticket), NativePolicyIds.FrameCues, ArenaFrame.ReadCues, cancellationToken);

    public Task<ResultResponse> ResultAsync(string ticket, CancellationToken cancellationToken) =>
        SendArenaFrameAsync(ArenaFrame.Result(ticket), NativePolicyIds.FrameResult, ArenaFrame.ReadResult, cancellationToken);

    public async Task<ManifestResponse> SubmitCompletionAsync(CancellationToken cancellationToken)
    {
        SecureChannel active = channel ?? throw new ArenaProtocolException(0, "Connect before submitting a result.");
        byte[] request = active.CreateCompletionFrame();
        try
        {
            return await SendArenaFrameAsync(
                request,
                NativePolicyIds.FrameFinish,
                ArenaFrame.ReadFinish,
                cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(request);
        }
    }

    public async Task<ArenaView> WaitForPhaseAsync(string heatId, string phase, CancellationToken cancellationToken)
    {
        while (true)
        {
            ArenaView view = await StateAsync(cancellationToken).ConfigureAwait(false);
            if (view.Heat.Id != heatId)
                throw new InvalidOperationException("The enrolled heat ended before the client reached the requested phase.");
            if (view.Heat.Phase == phase)
                return view;
            await Task.Delay(100, cancellationToken).ConfigureAwait(false);
        }
    }

    public async Task<long> MeasureServerOffsetAsync(CancellationToken cancellationToken)
    {
        long bestRtt = long.MaxValue;
        long bestOffset = 0;

        for (int i = 0; i < 7; i++)
        {
            long sent = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var stopwatch = Stopwatch.StartNew();
            ArenaView state = await StateAsync(cancellationToken).ConfigureAwait(false);
            stopwatch.Stop();
            long received = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long rtt = stopwatch.ElapsedMilliseconds;
            long midpoint = sent + (received - sent) / 2;
            if (rtt < bestRtt)
            {
                bestRtt = rtt;
                bestOffset = state.ServerNowUnixMs - midpoint;
            }
            await Task.Delay(30, cancellationToken).ConfigureAwait(false);
        }
        return bestOffset;
    }

    private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
    {
        return await SendSecureAsync<T>(HttpMethod.Get, path, [], null, cancellationToken).ConfigureAwait(false);
    }

    private async Task<TResponse> PostAsync<TResponse, TRequest>(string path, TRequest body, CancellationToken cancellationToken)
    {
        byte[] raw = JsonSerializer.SerializeToUtf8Bytes(body, typeof(TRequest), json);
        return await SendSecureAsync<TResponse>(HttpMethod.Post, path, raw, "application/json", cancellationToken)
            .ConfigureAwait(false);
    }

    private async Task<T> SendArenaFrameAsync<T>(
        byte[] body,
        byte expectedAction,
        Func<byte[], T> decode,
        CancellationToken cancellationToken)
    {
        if (body.Length < 6 || body[5] != expectedAction)
            throw new ArenaProtocolException(0, "Arena request binding is invalid.");
        RawSecureResponse response = await SendSecureBytesAsync(
            HttpMethod.Post,
            "/api/v3/frame",
            body,
            "application/octet-stream",
            cancellationToken).ConfigureAwait(false);
        if (!response.Success)
            throw new ArenaProtocolException(response.StatusCode, ReadProblemDetail(response.Body, "Coordinator rejected the arena request."));
        return decode(response.Body);
    }

    private async Task<TResponse> PostPublicAsync<TResponse, TRequest>(
        string path,
        TRequest body,
        CancellationToken cancellationToken)
    {
        using HttpResponseMessage response = await client
                                                   .PostAsJsonAsync(path, body, json, cancellationToken)
                                                   .ConfigureAwait(false);
        byte[] raw = await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
        return ReadPayload<TResponse>(response, raw);
    }

    private async Task<T> SendSecureAsync<T>(
        HttpMethod method,
        string path,
        byte[] body,
        string? contentType,
        CancellationToken cancellationToken)
    {
        RawSecureResponse response = await SendSecureBytesAsync(method, path, body, contentType, cancellationToken)
                                           .ConfigureAwait(false);
        if (!response.Success)
            throw new ArenaProtocolException(
                response.StatusCode,
                ReadProblemDetail(response.Body, "Coordinator rejected the request."));
        T payload = (T?)JsonSerializer.Deserialize(response.Body, typeof(T), json)
                    ?? throw new ArenaProtocolException(response.StatusCode, "Coordinator returned an empty response.");
        return ProtocolValidation.Validate(payload, response.StatusCode);
    }

    private async Task<RawSecureResponse> SendSecureBytesAsync(
        HttpMethod method,
        string path,
        byte[] body,
        string? contentType,
        CancellationToken cancellationToken)
    {
        SecureChannel active = channel ??
                               throw new ArenaProtocolException(0, "Connect before using the secure arena channel.");

        await channelGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        ChannelRequestProof? binding = null;
        try
        {
            binding = active.CreateRequest(method.Method, path, body);
            SecureHttpResponse response;
            try
            {
                response = await SecureRequestRecovery.SendAsync(
                    client,
                    () =>
                    {
                        var request = new HttpRequestMessage(method, path);
                        request.Headers.TryAddWithoutValidation("X-Rythme-Channel", binding.ChannelId);
                        request.Headers.TryAddWithoutValidation(
                            "X-Rythme-Sequence",
                            binding.Sequence.ToString(CultureInfo.InvariantCulture));
                        request.Headers.TryAddWithoutValidation(
                            "X-Rythme-Timestamp",
                            binding.SentAtUnixMs.ToString(CultureInfo.InvariantCulture));
                        request.Headers.TryAddWithoutValidation("X-Rythme-Previous", binding.Previous);
                        request.Headers.TryAddWithoutValidation("X-Rythme-Signature", binding.Signature);
                        if (contentType != null)
                        {
                            request.Content = new ByteArrayContent(body);
                            request.Content.Headers.ContentType = new MediaTypeHeaderValue(contentType);
                        }
                        return request;
                    },
                    cancellationToken).ConfigureAwait(false);
            }
            catch (HttpRequestException error)
            {
                throw new ArenaProtocolException(
                    0,
                    "The secure arena channel could not recover automatically. Check your connection, then rejoin the next wave.",
                    error);
            }

            active.VerifyResponse(
                binding,
                response.StatusCode,
                response.Body,
                response.Ack,
                response.Signature);
            return new RawSecureResponse((int)response.StatusCode, response.Success, response.Body);
        }
        finally
        {
            if (binding != null)
                CryptographicOperations.ZeroMemory(binding.SignatureBytes);
            channelGate.Release();
        }
    }

    private T ReadPayload<T>(HttpResponseMessage response, byte[] raw)
    {
        if (!response.IsSuccessStatusCode)
            throw new ArenaProtocolException(
                (int)response.StatusCode,
                ReadProblemDetail(raw, "Coordinator rejected the request."));

        T payload = (T?)JsonSerializer.Deserialize(raw, typeof(T), json)
                    ?? throw new ArenaProtocolException((int)response.StatusCode, "Coordinator returned an empty response.");
        return ProtocolValidation.Validate(payload, (int)response.StatusCode);
    }

    private static string ReadProblemDetail(byte[] raw, string fallback)
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(raw);
            if (document.RootElement.TryGetProperty("detail", out JsonElement detail))
            {
                string? value = detail.GetString();
                if (!string.IsNullOrWhiteSpace(value))
                    return value;
            }
        }
        catch (JsonException)
        {
        }
        return fallback;
    }

    public void Dispose()
    {
        channel?.Dispose();
        channelGate.Dispose();
        client.Dispose();
    }
}

internal sealed record RawSecureResponse(int StatusCode, bool Success, byte[] Body);
