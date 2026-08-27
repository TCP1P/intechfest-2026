using System;
using System.Diagnostics;
using osu.Framework.Logging;
using osu.Framework.Screens;
using osu.Game.Screens.Play;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// Arena gameplay must enter as soon as its drawable is ready. The stock
    /// loader intentionally waits for focus, pointer-idle, and a presentation
    /// delay; those safeguards can consume a synchronized 30-second window.
    /// </summary>
    internal partial class RythmePlayerLoader : PlayerLoader
    {
        private readonly Stopwatch loadTime = Stopwatch.StartNew();

        protected override double PlayerPushDelay => 0;

        protected override bool ReadyForGameplay => true;

        public RythmePlayerLoader(Func<Player> createPlayer)
            : base(createPlayer)
        {
            WindowShouldBeActiveForGameplayStart = false;
        }

        protected override void OnPlayerLoaded()
        {
            base.OnPlayerLoaded();
            Logger.Log($"Rythme arena playfield loaded in {loadTime.ElapsedMilliseconds} ms.");
        }

        public override void OnResuming(ScreenTransitionEvent e)
        {
            base.OnResuming(e);

            // Rythme has no retry path. PlayerLoader is only an implementation
            // detail between the persistent Crown lobby and one official run;
            // exposing it after a result looks like a frozen screen and makes
            // the player press Back a second time.
            Schedule(() =>
            {
                if (this.IsCurrentScreen())
                {
                    Logger.Log("Rythme match loader completed; returning to the Crown lobby.");
                    this.Exit();
                }
            });
        }
    }
}
