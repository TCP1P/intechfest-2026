using osu.Game.Overlays;
using osu.Game.Overlays.Mods;
using osu.Game.Rulesets.Mods;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// The stock lazer user mod selector, constrained to implemented,
    /// user-playable mods with default settings. Compatible multi-mod loadouts
    /// remain available while every synchronized profile stays verifiable.
    /// </summary>
    internal partial class RythmeModSelectOverlay : UserModSelectOverlay
    {
        protected override bool AllowCustomisation => false;

        public RythmeModSelectOverlay()
            : base(OverlayColourScheme.Plum)
        {
            IsValidMod = UpstreamModCatalog.IsArenaPlayable;
        }
    }
}
