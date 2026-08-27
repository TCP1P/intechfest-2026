#if RYTHME_UI_CAPTURE
using System;
using System.Linq;
using osu.Framework.Allocation;
using osu.Game.Online;
using osu.Game.Overlays;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeLobbyScreen
    {
        [Resolved(CanBeNull = true)]
        private INotificationOverlay? captureNotificationOverlay { get; set; }

        private bool applyLobbyCaptureFixture()
        {
            string? fixture = Environment.GetEnvironmentVariable("RYTHME_UI_CAPTURE");
            if (string.IsNullOrWhiteSpace(fixture))
                return false;

            scheduleCaptureNotificationCleanup();

            if (string.Equals(fixture, "login", StringComparison.OrdinalIgnoreCase))
                return true;
            if (!string.Equals(fixture, "reveal", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"Unknown Rythme lobby capture fixture: {fixture}");

            Scheduler.AddDelayed(applyRevealCaptureFixture, 250);
            return true;
        }

        private void scheduleCaptureNotificationCleanup()
        {
            for (int attempt = 1; attempt <= 5; attempt++)
            {
                Scheduler.AddDelayed(() =>
                {
                    if (captureNotificationOverlay == null)
                        return;

                    foreach (var notification in captureNotificationOverlay.AllNotifications.ToArray())
                        notification.Close(false);
                }, attempt * 500);
            }
        }

        private void applyRevealCaptureFixture()
        {
            connectionPanel.Hide();
            session = new SessionResponse
            {
                TeamId = "capture-team",
                TeamName = "Dimas",
            };
            roomTitle.Text = "CROWN ARENA • ROTATION 0042 • WAVE 3/4";
            phaseText.Text = "REVEAL  •  00:24";
            presentPhase("reveal");
            beatmapTitle.Text = "Kawaikute Gomen (I'm Too Cute For This World :3)";
            beatmapArtist.Text = "HoneyWorks feat. Capi";
            modeText.Text = "OSU!CATCH  •  HD+DT  •  1.50x RATE  •  1.46x SCORE";
            sourceText.Text = "OSU! COMMUNITY APPROVED CACHE  •  INSANE  •  4.84★  •  MAPPED BY SOTARK";
            commitmentText.Text = "SELECTION COMMITMENT  f8f1e4508dc61bbb694feaa730067727…";
            status.Text = "MAP READY  •  YOUR CATCH LOADOUT IS HD+DT\n" +
                          "AUTO START IS ARMED AND WILL LOCK THIS LOADOUT AT THE SHARED START.\n" +
                          "You can still open EDIT MODS before the timer reaches zero.";

            participantCount.Text = "2 / 3 READY";
            crownText.Text = "CROWN  •  BINARY BEAT BRIGADE";
            performerList.Clear();
            performerList.Add(new RythmeParticipantRow(
                new PerformerView
                {
                    TeamId = "capture-team",
                    TeamName = "Dimas",
                    Ready = true,
                    AppliedMods = captureMods("HD+DT"),
                },
                "reveal",
                true,
                false));
            performerList.Add(new RythmeParticipantRow(
                new PerformerView
                {
                    TeamId = "long-team",
                    TeamName = "An Intentionally Very Long Team Name For Truncation",
                },
                "reveal",
                false,
                true));
            performerList.Add(new RythmeParticipantRow(
                new PerformerView
                {
                    TeamId = "ready-team",
                    TeamName = "Hex Rhythm",
                    Ready = true,
                    AppliedMods = captureMods("NM"),
                },
                "reveal",
                false,
                false));

            automation.SetArmed(true);
            readyButton.Text = "AUTO START ARMED  •  00:24 LEFT";
            readyButton.Enabled.Value = true;
            loadoutModeButton.Text = "MODE  •  CATCH";
            loadoutModeButton.Enabled.Value = false;
            modsButton.Text = "EDIT MODS  •  HD+DT";
            modsButton.Enabled.Value = true;
            savedLoadoutText.Text = "OSU! HD  •  TAIKO NM  •  CATCH HD+DT  •  MANIA 4K";
            beatmapDownloadState.Value = DownloadState.LocallyAvailable;
            beatmapDownloadButton.Enabled.Value = false;
            beatmapDownloadButton.TooltipText = "Predownloaded beatmap ready in the local lazer library.";
            disconnectButton.Enabled.Value = true;
        }

        private static AppliedModProfile captureMods(string acronym) => new AppliedModProfile
        {
            Acronym = acronym,
            RateMilli = acronym.Contains("DT", StringComparison.Ordinal) ? 1500 : 1000,
            ScoreMultiplierMilli = acronym.Contains("DT", StringComparison.Ordinal) ? 1463 : 1000,
        };
    }
}
#endif
