using osu.Framework.Platform;
using osu.Game;
using osu.Game.Configuration;
using osu.Game.Online;
using osu.Game.Online.API;
using osu.Game.Rulesets.Catch;
using osu.Game.Rulesets.Mania;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Taiko;
using osu.Game.Screens;
using osu.Game.Screens.Menu;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeGame : OsuGame
    {
        private readonly RythmeOptions options;

        public override string Version => "1.0.0-rythme";

        public RythmeGame(RythmeOptions options, string[] args)
            : base(args)
        {
            this.options = options;

            // RulesetStore discovers loaded assemblies. Single-file publishing
            // is lazy, so make all four built-in rulesets visible before the
            // store performs its startup scan.
            _ = new OsuRuleset();
            _ = new TaikoRuleset();
            _ = new CatchRuleset();
            _ = new ManiaRuleset();

            // Keep lazer's local shell in its normal usable state. This is a
            // dummy provider (not osu!'s live API), while CreateEndpoints()
            // still hard-disables every official network service.
            API = new DummyAPIAccess();
        }

        public override EndpointConfiguration CreateEndpoints() => new EndpointConfiguration
        {
            WebsiteUrl = "http://127.0.0.1:9/disabled",
            APIUrl = "http://127.0.0.1:9/disabled",
            SpectatorUrl = "http://127.0.0.1:9/disabled",
            MultiplayerUrl = "http://127.0.0.1:9/disabled",
            MetadataUrl = "http://127.0.0.1:9/disabled",
            LivenessProbeUrl = null,
        };

        protected override Loader CreateLoader() => new RythmeLoader(options);

        protected override void LoadComplete()
        {
            LocalConfig.SetValue(OsuSetting.ShowFirstRunSetup, false);
            // Event maps are judged on gameplay objects, not decorative media.
            // Standardising these options avoids a video, storyboard, or
            // beatmap skin extending the synchronized playfield load.
            LocalConfig.SetValue(OsuSetting.PreferNoVideo, true);
            LocalConfig.SetValue(OsuSetting.ShowStoryboard, false);
            LocalConfig.SetValue(OsuSetting.BeatmapSkins, false);
            LocalConfig.SetValue(OsuSetting.BeatmapColours, false);
            base.LoadComplete();
        }

        public override void SetHost(GameHost host)
        {
            base.SetHost(host);
            if (host.Window != null)
                host.Window.Title = "Rythme // Syncore Crown Arena";
        }
    }

    internal partial class RythmeLoader : Loader
    {
        private readonly RythmeOptions options;

        public RythmeLoader(RythmeOptions options)
        {
            this.options = options;
        }

        protected override MainMenu CreateMainMenu() => new RythmeMainMenu(options);
    }
}
