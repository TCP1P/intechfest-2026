namespace Rythme.Desktop;

internal sealed class ArenaProtocolException : Exception
{
    public int StatusCode { get; }

    public ArenaProtocolException(int statusCode, string message, Exception? inner = null)
        : base(message, inner)
    {
        StatusCode = statusCode;
    }
}
