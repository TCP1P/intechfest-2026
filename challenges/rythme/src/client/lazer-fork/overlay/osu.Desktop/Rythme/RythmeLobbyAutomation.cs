using System;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// Keeps the player's next-heat intent independent from short-lived heat
    /// state. Claims make polling idempotent while the armed preference
    /// survives heat rollovers. Per-ruleset mod loadouts are owned separately.
    /// </summary>
    internal sealed class RythmeLobbyAutomation
    {
        private readonly object sync = new object();
        private bool armed;
        private string observedHeatId = string.Empty;
        private bool enrollmentClaimed;
        private bool readyClaimed;

        public RythmeLobbyAutomation(bool armed = false)
        {
            this.armed = armed;
        }

        public bool Armed
        {
            get
            {
                lock (sync)
                    return armed;
            }
        }

        public bool SetArmed(bool value)
        {
            lock (sync)
                return armed = value;
        }

        public bool ToggleArmed()
        {
            lock (sync)
                return armed = !armed;
        }

        public void ObserveHeat(string heatId)
        {
            if (string.IsNullOrWhiteSpace(heatId))
                throw new ArgumentException("A heat ID is required.", nameof(heatId));

            lock (sync)
            {
                if (string.Equals(observedHeatId, heatId, StringComparison.Ordinal))
                    return;

                observedHeatId = heatId;
                enrollmentClaimed = false;
                readyClaimed = false;
            }
        }

        public bool TryClaimEnrollment(string heatId, string phase, bool alreadyEnrolled)
        {
            lock (sync)
            {
                observeHeatLocked(heatId);
                if (!armed || alreadyEnrolled || phase != "enrollment" || enrollmentClaimed)
                    return false;

                enrollmentClaimed = true;
                return true;
            }
        }

        public bool TryClaimReady(string heatId, string phase, bool prepared, bool alreadyRunning)
        {
            lock (sync)
            {
                observeHeatLocked(heatId);
                if (!armed || !prepared || alreadyRunning || phase != "reveal" || readyClaimed)
                    return false;

                readyClaimed = true;
                return true;
            }
        }

        public void ResetConnection()
        {
            lock (sync)
            {
                observedHeatId = string.Empty;
                enrollmentClaimed = false;
                readyClaimed = false;
            }
        }

        private void observeHeatLocked(string heatId)
        {
            if (string.IsNullOrWhiteSpace(heatId))
                throw new ArgumentException("A heat ID is required.", nameof(heatId));
            if (string.Equals(observedHeatId, heatId, StringComparison.Ordinal))
                return;

            observedHeatId = heatId;
            enrollmentClaimed = false;
            readyClaimed = false;
        }
    }
}
