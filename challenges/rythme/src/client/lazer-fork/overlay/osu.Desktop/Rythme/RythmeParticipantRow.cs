using System.Globalization;
using osu.Framework.Allocation;
using osu.Framework.Extensions.Color4Extensions;
using osu.Framework.Graphics;
using osu.Framework.Graphics.Containers;
using osu.Framework.Graphics.Shapes;
using osu.Framework.Graphics.Sprites;
using osu.Game.Graphics;
using osu.Game.Graphics.Sprites;
using osuTK;
using osuTK.Graphics;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeParticipantRow : CompositeDrawable
    {
        private readonly PerformerView performer;
        private readonly string phase;
        private readonly bool local;
        private readonly bool crownOwner;

        public RythmeParticipantRow(PerformerView performer, string phase, bool local, bool crownOwner)
        {
            this.performer = performer;
            this.phase = phase;
            this.local = local;
            this.crownOwner = crownOwner;
            RelativeSizeAxes = Axes.X;
            Height = 49;
        }

        [BackgroundDependencyLoader]
        private void load(OsuColour colours)
        {
            (string label, IconUsage icon, Color4 colour) = status();

            InternalChildren =
            [
                new Container
                {
                    RelativeSizeAxes = Axes.Both,
                    Masking = true,
                    CornerRadius = 6,
                    Children =
                    [
                        new Box
                        {
                            RelativeSizeAxes = Axes.Both,
                            Colour = local
                                ? colours.Blue3.Opacity(0.82f)
                                : Color4Extensions.FromHex("#33413C").Opacity(0.86f),
                        },
                        new Circle
                        {
                            Anchor = Anchor.CentreLeft,
                            Origin = Anchor.CentreLeft,
                            X = 12,
                            Size = new Vector2(30),
                            Colour = local ? colours.Pink1 : colours.Gray8,
                        },
                        new OsuSpriteText
                        {
                            Anchor = Anchor.CentreLeft,
                            Origin = Anchor.Centre,
                            X = 27,
                            Text = teamInitial(performer.TeamName),
                            Font = OsuFont.GetFont(size: 15, weight: FontWeight.Bold),
                        },
                        new TruncatingSpriteText
                        {
                            Anchor = Anchor.CentreLeft,
                            Origin = Anchor.CentreLeft,
                            X = 52,
                            Width = 190,
                            Text = performer.TeamName + (local ? "  (you)" : ""),
                            Font = OsuFont.GetFont(size: 15, weight: FontWeight.Bold),
                        },
                        new FillFlowContainer
                        {
                            Anchor = Anchor.CentreRight,
                            Origin = Anchor.CentreRight,
                            AutoSizeAxes = Axes.Both,
                            Margin = new MarginPadding { Right = 11 },
                            Spacing = new Vector2(5, 0),
                            Direction = FillDirection.Horizontal,
                            Children =
                            [
                                new SpriteIcon
                                {
                                    Anchor = Anchor.Centre,
                                    Origin = Anchor.Centre,
                                    Size = new Vector2(12),
                                    Icon = icon,
                                    Colour = colour,
                                },
                                new OsuSpriteText
                                {
                                    Anchor = Anchor.Centre,
                                    Origin = Anchor.Centre,
                                    Text = label,
                                    Font = OsuFont.GetFont(size: 11, weight: FontWeight.SemiBold),
                                    Colour = colour,
                                },
                            ],
                        },
                    ],
                },
                new SpriteIcon
                {
                    Anchor = Anchor.CentreLeft,
                    Origin = Anchor.Centre,
                    X = 44,
                    Y = -17,
                    Size = new Vector2(12),
                    Icon = FontAwesome.Solid.Crown,
                    Colour = colours.Yellow,
                    Alpha = crownOwner ? 1 : 0,
                },
            ];
        }

        private (string Label, IconUsage Icon, Color4 Colour) status()
        {
            if (performer.Eligible)
                return ("scored", FontAwesome.Solid.Bolt, Color4Extensions.FromHex("#F7E65D"));
            if (performer.ManifestSent)
                return ("submitted", FontAwesome.Solid.ArrowAltCircleUp, Color4Extensions.FromHex("#66CCFF"));
            if (phase == "running" && performer.Frames > 0)
                return ("playing", FontAwesome.Solid.PlayCircle, Color4Extensions.FromHex("#66CCFF"));
            if (performer.Ready)
                return ($"ready · {performer.AppliedMods?.Acronym ?? "NM"}", FontAwesome.Solid.CheckCircle, Color4Extensions.FromHex("#AADD00"));
            return ("idle", FontAwesome.Solid.Circle, Color4.LightGray);
        }

        private static string teamInitial(string name) => string.IsNullOrWhiteSpace(name)
            ? "?"
            : StringInfo.GetNextTextElement(name).ToUpperInvariant();
    }
}
