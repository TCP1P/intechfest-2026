using System;
using osu.Framework.Allocation;
using osu.Framework.Audio;
using osu.Framework.Audio.Sample;
using osu.Framework.Extensions.Color4Extensions;
using osu.Framework.Graphics;
using osu.Framework.Graphics.Containers;
using osu.Framework.Graphics.Shapes;
using osu.Framework.Graphics.UserInterface;
using osu.Game.Graphics;
using osu.Game.Graphics.Sprites;
using osuTK;
using osuTK.Graphics;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeMatchCountdown : CompositeDrawable
    {
        private readonly string songTitle;
        private readonly string ruleset;
        private readonly long targetUnixMs;

        private Container pulse = null!;
        private OsuSpriteText countText = null!;
        private OsuSpriteText detailText = null!;
        private CircularProgress progress = null!;
        private Sample? tickSample;
        private Sample? warningSample;
        private Sample? finalSample;
        private int previousSecond = int.MaxValue;
        private bool complete;

        public RythmeMatchCountdown(string songTitle, string ruleset, long targetUnixMs)
        {
            this.songTitle = songTitle;
            this.ruleset = ruleset;
            this.targetUnixMs = targetUnixMs;
            RelativeSizeAxes = Axes.Both;
            Depth = float.MinValue;
        }

        [BackgroundDependencyLoader]
        private void load(OsuColour colours, AudioManager audio)
        {
            tickSample = audio.Samples.Get(@"Multiplayer/countdown-tick");
            warningSample = audio.Samples.Get(@"Multiplayer/countdown-warn");
            finalSample = audio.Samples.Get(@"Multiplayer/countdown-warn-final");

            InternalChildren =
            [
                new Box
                {
                    RelativeSizeAxes = Axes.Both,
                    Colour = Color4.Black.Opacity(0.58f),
                },
                new FillFlowContainer
                {
                    Anchor = Anchor.Centre,
                    Origin = Anchor.Centre,
                    AutoSizeAxes = Axes.Both,
                    Direction = FillDirection.Vertical,
                    Spacing = new Vector2(0, 15),
                    Children =
                    [
                        new OsuSpriteText
                        {
                            Anchor = Anchor.TopCentre,
                            Origin = Anchor.TopCentre,
                            Text = "MULTIPLAYER MATCH START",
                            Font = OsuFont.GetFont(size: 18, weight: FontWeight.Bold),
                            Colour = colours.Pink1,
                        },
                        pulse = new Container
                        {
                            Anchor = Anchor.TopCentre,
                            Origin = Anchor.TopCentre,
                            Size = new Vector2(212),
                            Children =
                            [
                                new Circle
                                {
                                    RelativeSizeAxes = Axes.Both,
                                    Colour = new Color4(32, 36, 54, 245),
                                },
                                progress = new CircularProgress
                                {
                                    Anchor = Anchor.Centre,
                                    Origin = Anchor.Centre,
                                    Size = new Vector2(190),
                                    InnerRadius = 0.045f,
                                    RoundedCaps = true,
                                    Colour = colours.Pink1,
                                },
                                countText = new OsuSpriteText
                                {
                                    Anchor = Anchor.Centre,
                                    Origin = Anchor.Centre,
                                    Text = "—",
                                    Font = OsuFont.GetFont(size: 62, weight: FontWeight.Light),
                                },
                            ],
                        },
                        detailText = new OsuSpriteText
                        {
                            Anchor = Anchor.TopCentre,
                            Origin = Anchor.TopCentre,
                            Text = $"{songTitle}  •  {displayRuleset(ruleset)}",
                            Font = OsuFont.GetFont(size: 17, weight: FontWeight.SemiBold),
                            Colour = Color4.White,
                        },
                        new OsuSpriteText
                        {
                            Anchor = Anchor.TopCentre,
                            Origin = Anchor.TopCentre,
                            Text = "All performers are held on the same server clock",
                            Font = OsuFont.GetFont(size: 13),
                            Colour = Color4.LightGray,
                        },
                    ],
                },
            ];
        }

        protected override void Update()
        {
            base.Update();
            if (complete || countText == null)
                return;

            long remainingMs = targetUnixMs - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            if (remainingMs <= 0)
            {
                showFinal();
                return;
            }

            int seconds = (int)Math.Ceiling(remainingMs / 1000.0);
            progress.Progress = 1 - (remainingMs % 1000) / 1000.0;
            if (seconds == previousSecond)
                return;

            previousSecond = seconds;
            countText.Text = seconds <= 3 ? seconds.ToString() : TimeSpan.FromSeconds(seconds).ToString(@"mm\:ss");
            pulse.ScaleTo(0.92f).Then().ScaleTo(1, 220, Easing.OutElasticHalf);
            progress.FlashColour(Color4.White, 260, Easing.Out);

            if (seconds < 10)
                tickSample?.Play();
            if (seconds <= 3)
                warningSample?.Play();
        }

        public void Complete()
        {
            if (complete)
                return;
            showFinal();
        }

        private void showFinal()
        {
            if (complete)
                return;

            complete = true;
            finalSample?.Play();
            if (countText != null)
            {
                countText.Text = "GO!";
                countText.Font = OsuFont.GetFont(size: 52, weight: FontWeight.Bold);
                pulse.ScaleTo(1.18f, 180, Easing.OutQuint);
                progress.Progress = 1;
            }
            detailText?.FadeOut(120);
            this.Delay(250).FadeOut(300, Easing.OutQuint).Expire();
        }

        private static string displayRuleset(string value) => value switch
        {
            "osu" => "osu!",
            "taiko" => "osu!taiko",
            "fruits" => "osu!catch",
            "mania" => "osu!mania",
            _ => value,
        };
    }
}
