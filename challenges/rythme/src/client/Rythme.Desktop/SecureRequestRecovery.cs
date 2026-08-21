using System.Net;

namespace Rythme.Desktop;

internal static class SecureRequestRecovery
{
    private const int maximumAttempts = 4;
    private static readonly TimeSpan attemptTimeout = TimeSpan.FromMilliseconds(1500);

    public static async Task<SecureHttpResponse> SendAsync(
        HttpClient client,
        Func<HttpRequestMessage> createRequest,
        CancellationToken cancellationToken)
    {
        Exception? interruption = null;
        for (int attempt = 0; attempt < maximumAttempts; attempt++)
        {
            using HttpRequestMessage request = createRequest();
            string? expectedAck = request.Headers.TryGetValues(
                "X-Rythme-Sequence",
                out IEnumerable<string>? sequenceValues)
                ? sequenceValues.SingleOrDefault()
                : null;
            if (string.IsNullOrWhiteSpace(expectedAck))
                throw new InvalidOperationException("The secure request has no sequence binding.");

            using var attemptLifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            attemptLifetime.CancelAfter(attemptTimeout);
            try
            {
                using HttpResponseMessage response = await client
                                                           .SendAsync(
                                                               request,
                                                               HttpCompletionOption.ResponseHeadersRead,
                                                               attemptLifetime.Token)
                                                           .ConfigureAwait(false);
                byte[] body = await response.Content
                                            .ReadAsByteArrayAsync(attemptLifetime.Token)
                                            .ConfigureAwait(false);
                string? ack = response.Headers.TryGetValues(
                    "X-Rythme-Ack",
                    out IEnumerable<string>? ackValues)
                    ? ackValues.SingleOrDefault()
                    : null;
                string? signature = response.Headers.TryGetValues(
                    "X-Rythme-Response-Signature",
                    out IEnumerable<string>? signatureValues)
                    ? signatureValues.SingleOrDefault()
                    : null;

                bool responseIsBound = string.Equals(ack, expectedAck, StringComparison.Ordinal) &&
                                       !string.IsNullOrWhiteSpace(signature);
                if (responseIsBound)
                {
                    return new SecureHttpResponse(
                        response.StatusCode,
                        response.IsSuccessStatusCode,
                        body,
                        ack,
                        signature);
                }

                interruption = new HttpRequestException(
                    $"The arena transport returned an unbound HTTP {(int)response.StatusCode} response.");
            }
            catch (Exception error) when (
                !cancellationToken.IsCancellationRequested &&
                error is HttpRequestException or IOException or OperationCanceledException)
            {
                interruption = error;
            }

            if (attempt + 1 < maximumAttempts)
            {
                int delayMS = 100 << attempt;
                await Task.Delay(delayMS, cancellationToken).ConfigureAwait(false);
            }
        }

        throw new HttpRequestException(
            "The secure arena transport did not recover after four attempts.",
            interruption);
    }
}

internal sealed record SecureHttpResponse(
    HttpStatusCode StatusCode,
    bool Success,
    byte[] Body,
    string? Ack,
    string? Signature);
