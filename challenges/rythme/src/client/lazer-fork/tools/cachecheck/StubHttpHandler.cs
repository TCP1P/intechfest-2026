internal sealed class StubHttpHandler : HttpMessageHandler
{
    private readonly Func<HttpRequestMessage, HttpResponseMessage> response;

    public StubHttpHandler(Func<HttpRequestMessage, HttpResponseMessage> response)
    {
        this.response = response;
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken) => Task.FromResult(response(request));
}
