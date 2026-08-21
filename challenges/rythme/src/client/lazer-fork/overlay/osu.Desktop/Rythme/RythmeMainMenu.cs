using osu.Framework.Screens;
using osu.Game.Screens.Menu;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// The upstream lazer main menu with one additional native entry for the
    /// isolated Crown coordinator. All normal offline menu paths remain owned
    /// by <see cref="MainMenu"/>.
    /// </summary>
    internal partial class RythmeMainMenu : MainMenu
    {
        private readonly RythmeOptions options;

        public RythmeMainMenu(RythmeOptions options)
        {
            this.options = options;
        }

        protected override void LoadComplete()
        {
            base.LoadComplete();
            Buttons.OnRythme = () => this.Push(new RythmeLobbyScreen(options));
#if RYTHME_STARTUP_SMOKE
            Schedule(() => this.Push(new RythmeLobbyScreen(options)));
#endif
        }
    }
}
