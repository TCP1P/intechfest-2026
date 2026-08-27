namespace Rythme.Desktop;

internal static class BackgroundFailure
{
    public static bool CaptureUnlessStopping(
        ref Exception? destination,
        Exception error,
        CancellationToken stopping)
    {
        if (stopping.IsCancellationRequested)
            return false;

        return Interlocked.CompareExchange(ref destination, error, null) == null;
    }
}
