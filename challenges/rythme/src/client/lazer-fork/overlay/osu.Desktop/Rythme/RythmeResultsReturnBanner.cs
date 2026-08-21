using osu.Framework.Allocation;
using osu.Framework.Extensions.Color4Extensions;
using osu.Framework.Graphics;
using osu.Framework.Graphics.Containers;
using osu.Framework.Graphics.Shapes;
using osu.Game.Graphics;
using osu.Game.Graphics.Containers;
using osu.Game.Graphics.Sprites;
using osuTK;
using osuTK.Graphics;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeResultsReturnBanner : CompositeDrawable
    {
        private readonly string title;
        private readonly string detail;

        public RythmeResultsReturnBanner(string title, string detail)
        {
            this.title = title;
            this.detail = detail;
            Anchor = Anchor.TopCentre;
            Origin = Anchor.TopCentre;
            Y = 28;
            Size = new Vector2(720, 112);
            Masking = true;
            CornerRadius = 14;
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
                    Colour = new Color4(20, 23, 37, 248),
                },
                new Box
                {
                    RelativeSizeAxes = Axes.Y,
                    Width = 7,
                    Colour = colours.Pink1,
                },
                new FillFlowContainer
                {
                    RelativeSizeAxes = Axes.X,
                    AutoSizeAxes = Axes.Y,
                    Padding = new MarginPadding { Left = 28, Right = 24, Top = 18 },
                    Direction = FillDirection.Vertical,
                    Spacing = new Vector2(0, 8),
                    Children =
                    [
                        new OsuSpriteText
                        {
                            Text = title,
                            Font = OsuFont.GetFont(size: 21, weight: FontWeight.Bold),
                            Colour = colours.Pink1,
                        },
                        new OsuTextFlowContainer(text =>
                        {
                            text.Font = OsuFont.GetFont(size: 14, weight: FontWeight.Medium);
                            text.Colour = Color4.White;
                        })
                        {
                            RelativeSizeAxes = Axes.X,
                            Height = 48,
                            Text = detail,
                        },
                    ],
                },
            ];

            this.FadeInFromZero(180, Easing.OutQuint);
        }
    }
}
