namespace Rythme.Desktop;

internal static class ArenaRunFinalizer
{
    public static readonly TimeSpan MaximumDuration = TimeSpan.FromSeconds(12);

    public static async Task<TReceipt> CompleteAsync<TReceipt>(
        Func<CancellationToken, Task> drainInput,
        Func<CancellationToken, Task> sealResult,
        Func<CancellationToken, Task<TReceipt>> submitCompletion,
        CancellationToken cancellationToken,
        TimeSpan? maximumDuration = null)
    {
        ArgumentNullException.ThrowIfNull(drainInput);
        ArgumentNullException.ThrowIfNull(sealResult);
        ArgumentNullException.ThrowIfNull(submitCompletion);

        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(maximumDuration ?? MaximumDuration);

        try
        {
            await drainInput(deadline.Token).WaitAsync(deadline.Token).ConfigureAwait(false);
            await sealResult(deadline.Token).WaitAsync(deadline.Token).ConfigureAwait(false);
            return await submitCompletion(deadline.Token).WaitAsync(deadline.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException error) when (
            !cancellationToken.IsCancellationRequested && deadline.IsCancellationRequested)
        {
            throw new ArenaProtocolException(
                0,
                "Arena score finalization timed out. The local result is still available, but no official receipt was accepted.",
                error);
        }
    }
}
