using System.Net;
using System.Text;
using Rythme.Desktop;

await recoversWhenRequestNeverArrived();
await recoversWhenAcceptedResponseWasLost();
await recoversFromUnsignedGatewayFailure();
await recoversFromUnboundCoordinatorResponse();
await recoversFromMismatchedAcknowledgement();
await recoversFromSilentAttemptTimeout();
await preservesSignedCoordinatorFailure();
await stopsAfterBoundedFailures();
await stopsAfterBoundedUnboundResponses();
await honoursCallerCancellation();
await honoursCancellationDuringBackoff();
Console.WriteLine("secure request interruption recovery checks passed");

static async Task recoversWhenRequestNeverArrived()
{
    var handler = new ScriptedHandler(
        _ => throw new HttpRequestException("connection reset before send"),
        _ => boundResponse(HttpStatusCode.OK, "accepted"));
    using var client = new HttpClient(handler);

    SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None);
    require(response.Success && text(response) == "accepted", "request-before-send recovery failed");
    require(handler.Attempts == 2, "request-before-send failure was not retried exactly once");
    require(handler.Identities.Distinct(StringComparer.Ordinal).Count() == 1, "retry changed the signed request identity");
}

static async Task recoversWhenAcceptedResponseWasLost()
{
    int applications = 0;
    string? acceptedIdentity = null;
    var handler = new ScriptedHandler(
        identity =>
        {
            acceptedIdentity = identity;
            applications++;
            throw new HttpRequestException("response stream reset after accept");
        },
        identity =>
        {
            require(identity == acceptedIdentity, "response-loss retry changed the signed request");
            return boundResponse(HttpStatusCode.Created, "cached response");
        });
    using var client = new HttpClient(handler);

    SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None);
    require(response.StatusCode == HttpStatusCode.Created, "lost accepted response was not recovered");
    require(applications == 1, "fault fixture applied the original request more than once");
}

static async Task recoversFromUnsignedGatewayFailure()
{
    foreach (HttpStatusCode status in new[]
             {
                 HttpStatusCode.BadGateway,
                 HttpStatusCode.ServiceUnavailable,
                 HttpStatusCode.GatewayTimeout
             })
    {
        var handler = new ScriptedHandler(
            _ => new HttpResponseMessage(status)
            {
                Content = new StringContent("upstream reset")
            },
            _ => boundResponse(HttpStatusCode.OK, "recovered"));
        using var client = new HttpClient(handler);

        SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
            client,
            request,
            CancellationToken.None);
        require(response.Success && handler.Attempts == 2, $"unsigned {(int)status} was not recovered");
    }
}

static async Task recoversFromUnboundCoordinatorResponse()
{
    foreach (HttpStatusCode status in new[]
             {
                 HttpStatusCode.OK,
                 HttpStatusCode.Unauthorized,
                 HttpStatusCode.Conflict,
                 HttpStatusCode.TooManyRequests,
                 HttpStatusCode.InternalServerError
             })
    {
        var handler = new ScriptedHandler(
            _ => new HttpResponseMessage(status)
            {
                Content = new StringContent("unbound coordinator response")
            },
            _ => boundResponse(HttpStatusCode.OK, "recovered"));
        using var client = new HttpClient(handler);

        SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
            client,
            request,
            CancellationToken.None);
        require(response.Success && handler.Attempts == 2, $"unbound {(int)status} was not recovered");
        require(
            handler.Identities.Distinct(StringComparer.Ordinal).Count() == 1,
            $"unbound {(int)status} recovery changed the signed request identity");
    }
}

static async Task recoversFromMismatchedAcknowledgement()
{
    var handler = new ScriptedHandler(
        _ => boundResponse(HttpStatusCode.OK, "wrong response", "6"),
        _ => boundResponse(HttpStatusCode.OK, "recovered"));
    using var client = new HttpClient(handler);

    SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None);
    require(response.Success && text(response) == "recovered", "mismatched acknowledgement was accepted");
    require(handler.Attempts == 2, "mismatched acknowledgement was not retried exactly once");
}

static async Task recoversFromSilentAttemptTimeout()
{
    var handler = new TimeoutOnceHandler();
    using var client = new HttpClient(handler);

    SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None);
    require(response.Success && handler.Attempts == 2, "silent transport timeout was not recovered");
    require(handler.Identities.Distinct(StringComparer.Ordinal).Count() == 1, "timeout recovery changed the signed request");
}

static async Task preservesSignedCoordinatorFailure()
{
    foreach (HttpStatusCode status in new[] { HttpStatusCode.Conflict, HttpStatusCode.BadGateway })
    {
        var handler = new ScriptedHandler(
            _ => boundResponse(status, "signed failure"));
        using var client = new HttpClient(handler);

        SecureHttpResponse response = await SecureRequestRecovery.SendAsync(
            client,
            request,
            CancellationToken.None);
        require(response.StatusCode == status, "signed coordinator failure changed");
        require(handler.Attempts == 1, "signed coordinator failure was incorrectly retried");
    }
}

static async Task stopsAfterBoundedFailures()
{
    var handler = new ScriptedHandler(
        _ => throw new HttpRequestException("offline"),
        _ => throw new HttpRequestException("offline"),
        _ => throw new HttpRequestException("offline"),
        _ => throw new HttpRequestException("offline"));
    using var client = new HttpClient(handler);

    await expect<HttpRequestException>(() => SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None));
    require(handler.Attempts == 4, "recovery did not stop at the bounded attempt count");
    require(handler.Identities.Distinct(StringComparer.Ordinal).Count() == 1, "bounded retries changed request identity");
}

static async Task stopsAfterBoundedUnboundResponses()
{
    var handler = new ScriptedHandler(
        _ => unboundResponse(),
        _ => unboundResponse(),
        _ => unboundResponse(),
        _ => unboundResponse());
    using var client = new HttpClient(handler);

    await expect<HttpRequestException>(() => SecureRequestRecovery.SendAsync(
        client,
        request,
        CancellationToken.None));
    require(handler.Attempts == 4, "unbound response recovery did not stop at the bounded attempt count");
    require(
        handler.Identities.Distinct(StringComparer.Ordinal).Count() == 1,
        "unbound response retries changed request identity");
}

static async Task honoursCallerCancellation()
{
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();
    var handler = new ScriptedHandler(
        _ => throw new OperationCanceledException(cancellation.Token));
    using var client = new HttpClient(handler);

    await expect<OperationCanceledException>(() => SecureRequestRecovery.SendAsync(
        client,
        request,
        cancellation.Token));
    require(handler.Attempts <= 1, "caller cancellation was retried");
}

static async Task honoursCancellationDuringBackoff()
{
    using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(50));
    var handler = new ScriptedHandler(
        _ => throw new HttpRequestException("offline"));
    using var client = new HttpClient(handler);

    await expect<OperationCanceledException>(() => SecureRequestRecovery.SendAsync(
        client,
        request,
        cancellation.Token));
    require(handler.Attempts == 1, "cancellation during backoff started another attempt");
}

static HttpRequestMessage request()
{
    var result = new HttpRequestMessage(HttpMethod.Post, "https://arena.test/api/v3/frame")
    {
        Content = new ByteArrayContent([0x29, 0x9e, 0xd9, 0x46, 1, 0x3b])
    };
    result.Headers.TryAddWithoutValidation("X-Rythme-Channel", "channel-test");
    result.Headers.TryAddWithoutValidation("X-Rythme-Sequence", "7");
    result.Headers.TryAddWithoutValidation("X-Rythme-Timestamp", "1786017600000");
    result.Headers.TryAddWithoutValidation("X-Rythme-Previous", new string('a', 64));
    result.Headers.TryAddWithoutValidation("X-Rythme-Signature", new string('b', 64));
    return result;
}

static HttpResponseMessage boundResponse(HttpStatusCode status, string body, string ack = "7")
{
    var response = new HttpResponseMessage(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/octet-stream")
    };
    response.Headers.TryAddWithoutValidation("X-Rythme-Ack", ack);
    response.Headers.TryAddWithoutValidation("X-Rythme-Response-Signature", new string('c', 64));
    return response;
}

static HttpResponseMessage unboundResponse() => new(HttpStatusCode.OK)
{
    Content = new StringContent("unbound")
};

static string text(SecureHttpResponse response) => Encoding.UTF8.GetString(response.Body);

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static async Task expect<T>(Func<Task> action)
    where T : Exception
{
    try
    {
        await action();
    }
    catch (T)
    {
        return;
    }
    throw new InvalidOperationException($"expected {typeof(T).Name}");
}

internal sealed class ScriptedHandler : HttpMessageHandler
{
    private readonly Func<string, HttpResponseMessage>[] steps;
    private int next;

    public ScriptedHandler(params Func<string, HttpResponseMessage>[] steps)
    {
        this.steps = steps;
    }

    public int Attempts => next;

    public List<string> Identities { get; } = [];

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        byte[] body = request.Content == null
            ? []
            : await request.Content.ReadAsByteArrayAsync(cancellationToken);
        string identity = string.Join("\n", new[]
        {
            request.Method.Method,
            request.RequestUri?.AbsoluteUri ?? "",
            header(request, "X-Rythme-Channel"),
            header(request, "X-Rythme-Sequence"),
            header(request, "X-Rythme-Timestamp"),
            header(request, "X-Rythme-Previous"),
            header(request, "X-Rythme-Signature"),
            Convert.ToHexString(body)
        });
        Identities.Add(identity);
        int index = next++;
        if (index >= steps.Length)
            throw new InvalidOperationException("transport fixture received an unexpected attempt");
        return steps[index](identity);
    }

    private static string header(HttpRequestMessage request, string name) =>
        request.Headers.TryGetValues(name, out IEnumerable<string>? values)
            ? values.Single()
            : "";
}

internal sealed class TimeoutOnceHandler : HttpMessageHandler
{
    private int attempts;

    public int Attempts => attempts;

    public List<string> Identities { get; } = [];

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        Identities.Add(request.Headers.GetValues("X-Rythme-Signature").Single());
        if (Interlocked.Increment(ref attempts) == 1)
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);

        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("recovered")
        };
        response.Headers.TryAddWithoutValidation("X-Rythme-Ack", "7");
        response.Headers.TryAddWithoutValidation("X-Rythme-Response-Signature", new string('c', 64));
        return response;
    }
}
