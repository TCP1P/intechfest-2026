using System;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal sealed class RythmeRunContext : IDisposable
    {
        private readonly SealedRunState sealedRun;

        public ArenaApi API { get; }

        public SessionResponse Session { get; }

        public EnrollResponse Enrollment { get; }

        public HeatView Heat { get; }

        public Selection Selection => Heat.Selection ?? throw new InvalidOperationException("Heat selection was not revealed.");

        public Note[] Notes { get; }

        public long ServerOffsetMS { get; }

        public AppliedModSet AppliedMods => OpenRunState().AppliedMods;

        public RythmeCancellationLifetime Lifetime { get; } = new RythmeCancellationLifetime();

        public ManifestResponse? Receipt { get; set; }

        public string? Failure { get; set; }

        public string? Warning { get; set; }

        public string? ReturnMessage { get; set; }

        public RythmeRunContext(
            ArenaApi api,
            SessionResponse session,
            EnrollResponse enrollment,
            HeatView heat,
            Note[] notes,
            long serverOffsetMS,
            ReadyBinding ready)
        {
            API = api;
            Session = session;
            Enrollment = enrollment;
            Heat = heat;
            Notes = notes;
            ServerOffsetMS = serverOffsetMS;
            SecureChannel channel = session.Channel ??
                                    throw new InvalidOperationException("The arena session has no secure channel.");
            sealedRun = SealedRunState.Create(
                channel,
                session,
                enrollment,
                heat,
                Selection,
                ready);
        }

        public RunBinding OpenRunState()
        {
            SecureChannel channel = Session.Channel ??
                                    throw new InvalidOperationException("The arena session has no secure channel.");
            RunBinding binding = sealedRun.Open(channel);
            if (binding.TeamId != Session.TeamId ||
                binding.HeatId != Enrollment.HeatId ||
                binding.HeatTicketId != Enrollment.HeatTicketId ||
                binding.SongId != Selection.SongId ||
                binding.Ruleset != Selection.Ruleset ||
                binding.ChartDigest != Selection.ChartDigest ||
                binding.StartAtUnixMs != Heat.StartAtUnixMs)
                throw new ClientIntegrityException("Protected run state no longer matches the active heat.");
            return binding;
        }

        public void Dispose()
        {
            Lifetime.Cancel();
            sealedRun.Dispose();
            Lifetime.Dispose();
        }
    }
}
