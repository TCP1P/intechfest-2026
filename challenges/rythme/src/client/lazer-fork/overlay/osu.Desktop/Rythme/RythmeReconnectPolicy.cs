using System;
using System.Threading;

namespace osu.Desktop.Rythme
{
    internal sealed class RythmeReconnectPolicy
    {
        private const int initial_delay_ms = 500;
        private const int maximum_delay_ms = 8000;
        private int failures;

        public int NextDelayMilliseconds()
        {
            int attempt = Interlocked.Increment(ref failures) - 1;
            int shift = Math.Min(attempt, 4);
            return Math.Min(initial_delay_ms << shift, maximum_delay_ms);
        }

        public void Reset() => Interlocked.Exchange(ref failures, 0);
    }
}
