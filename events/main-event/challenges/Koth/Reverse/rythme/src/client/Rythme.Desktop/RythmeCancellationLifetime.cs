namespace Rythme.Desktop;

/// <summary>
/// Owns one cancellation source whose token remains safe to observe after teardown.
/// UI navigation, disconnect, and game-host shutdown can all converge on the same
/// cleanup path, so cancellation and disposal must be idempotent.
/// </summary>
internal sealed class RythmeCancellationLifetime : IDisposable
{
    private readonly object gate = new();
    private readonly CancellationTokenSource source;
    private readonly CancellationToken token;
    private bool cancellationRequested;
    private bool cancellationInProgress;
    private bool disposeRequested;
    private bool disposed;

    public CancellationToken Token => token;

    public bool IsCancellationRequested => token.IsCancellationRequested;

    public RythmeCancellationLifetime(CancellationToken parent = default)
    {
        source = parent.CanBeCanceled
            ? CancellationTokenSource.CreateLinkedTokenSource(parent)
            : new CancellationTokenSource();
        token = source.Token;
    }

    public void Cancel()
    {
        lock (gate)
        {
            if (cancellationRequested || disposed)
                return;

            cancellationRequested = true;
            cancellationInProgress = true;
        }

        cancelAndFinish();
    }

    public void Dispose()
    {
        bool cancel = false;
        bool dispose = false;
        lock (gate)
        {
            if (disposed || disposeRequested)
                return;

            disposeRequested = true;
            if (!cancellationRequested)
            {
                cancellationRequested = true;
                cancellationInProgress = true;
                cancel = true;
            }
            else if (!cancellationInProgress)
            {
                disposed = true;
                dispose = true;
            }
        }

        if (cancel)
            cancelAndFinish();
        else if (dispose)
            source.Dispose();
    }

    private void cancelAndFinish()
    {
        try
        {
            // Cancellation invokes arbitrary callbacks synchronously. It must
            // happen outside gate so a callback can safely call Dispose().
            source.Cancel();
        }
        finally
        {
            bool dispose = false;
            lock (gate)
            {
                cancellationInProgress = false;
                if (disposeRequested && !disposed)
                {
                    disposed = true;
                    dispose = true;
                }
            }

            if (dispose)
                source.Dispose();
        }
    }
}
