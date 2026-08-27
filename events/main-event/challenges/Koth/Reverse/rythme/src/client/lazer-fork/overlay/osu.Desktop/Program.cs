// Rythme is a CTF fork of osu!lazer. The upstream licence remains in LICENCE.

using System;
using osu.Desktop.Rythme;
using osu.Framework;
using osu.Framework.Platform;
using Rythme.Desktop;

namespace osu.Desktop
{
    public static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            StartupFailureReporter.InstallGlobalHandler();
            try
            {
                AppDomain.CurrentDomain.SetData("REGEX_DEFAULT_MATCH_TIMEOUT", TimeSpan.FromMilliseconds(1000));
                Environment.SetEnvironmentVariable("RYTHME_DISABLE_OFFICIAL_SERVICES", "1");
                ExecutableIntegrityVerifier.VerifyCurrentProcess();
                ClientIntegrityGuard.Initialize();

                RythmeOptions options = RythmeOptions.Parse(args);
                var hostOptions = new HostOptions
                {
                    IPCPipeName = "rythme-arena",
                    FriendlyGameName = "Rythme",
                };

                using DesktopGameHost host = Host.GetSuitableDesktopHost("rythme", hostOptions);
                host.Run(new RythmeGame(options, args));
                return 0;
            }
            catch (Exception error)
            {
                StartupFailureReporter.Report(error);
                return 1;
            }
        }
    }
}
