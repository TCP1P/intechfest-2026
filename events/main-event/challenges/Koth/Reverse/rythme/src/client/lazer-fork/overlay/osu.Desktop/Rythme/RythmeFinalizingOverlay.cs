using osu.Framework.Allocation;
using osu.Framework.Extensions.Color4Extensions;
using osu.Framework.Graphics;
using osu.Framework.Graphics.Containers;
using osu.Framework.Graphics.Shapes;
using osu.Game.Graphics;
using osu.Game.Graphics.Sprites;
using osuTK;
using osuTK.Graphics;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeFinalizingOverlay : CompositeDrawable
    {
        private readonly string detail;

        public RythmeFinalizingOverlay(string detail)
        {
            this.detail = detail;
            RelativeSizeAxes = Axes.Both;
            Depth = float.MinValue;
        }

        [BackgroundDependencyLoader]
        private void load(OsuColour colours)
        {
            InternalChildren =
            [
                new Box
                {
                    RelativeSizeAxes = Axes.Both,
                    Colour = Color4.Black.Opacity(0.68f),
                },
                new Container
                {
                    Anchor = Anchor.Centre,
                    Origin = Anchor.Centre,
                    Size = new Vector2(620, 132),
                    Masking = true,
                    CornerRadius = 16,
                    Children =
                    [
                        new Box
                        {
                            RelativeSizeAxes = Axes.Both,
                            Colour = new Color4(28, 31, 46, 250),
                        },
                        new FillFlowContainer
                        {
                            Anchor = Anchor.Centre,
                            Origin = Anchor.Centre,
                            AutoSizeAxes = Axes.Both,
                            Direction = FillDirection.Vertical,
                            Spacing = new Vector2(0, 10),
                            Children =
                            [
                                new OsuSpriteText
                                {
                                    Anchor = Anchor.TopCentre,
                                    Origin = Anchor.TopCentre,
                                    Text = "FINALIZING OFFICIAL RUN",
                                    Font = OsuFont.GetFont(size: 24, weight: FontWeight.Bold),
                                    Colour = colours.Pink1,
                                },
                                new OsuSpriteText
                                {
                                    Anchor = Anchor.TopCentre,
                                    Origin = Anchor.TopCentre,
                                    Text = detail,
                                    Font = OsuFont.GetFont(size: 15, weight: FontWeight.Medium),
                                    Colour = Color4.White,
                                },
                            ],
                        },
                    ],
                },
            ];

            this.FadeInFromZero(180, Easing.OutQuint);
        }
    }
}
