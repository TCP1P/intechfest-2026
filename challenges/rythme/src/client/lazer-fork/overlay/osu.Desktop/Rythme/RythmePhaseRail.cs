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
    internal partial class RythmePhaseRail : CompositeDrawable
    {
        private static readonly string[] labels = ["JOIN", "REVEAL", "PLAY", "SCORE"];
        private readonly PhaseStep[] steps = new PhaseStep[RythmeLobbyPhase.STEP_COUNT];
        private string currentPhase = string.Empty;

        public RythmePhaseRail()
        {
            Width = 718;
            Height = 38;
        }

        [BackgroundDependencyLoader]
        private void load()
        {
            var flow = new FillFlowContainer
            {
                RelativeSizeAxes = Axes.Both,
                Direction = FillDirection.Horizontal,
                Spacing = new Vector2(7, 0),
            };

            for (int index = 0; index < steps.Length; index++)
            {
                steps[index] = new PhaseStep(index + 1, labels[index])
                {
                    Width = 174.25f,
                };
                flow.Add(steps[index]);
            }

            InternalChild = flow;
            SetPhase("enrollment");
        }

        public void SetPhase(string phase)
        {
            if (phase == currentPhase || steps[0] == null)
                return;

            currentPhase = phase;
            int current = RythmeLobbyPhase.StepIndex(phase);
            for (int index = 0; index < steps.Length; index++)
            {
                StepState state = current < 0 || index > current
                    ? StepState.Pending
                    : index == current
                        ? StepState.Current
                        : StepState.Complete;
                steps[index].setState(state);
            }
        }

        private enum StepState
        {
            Pending,
            Current,
            Complete,
        }

        private partial class PhaseStep : CompositeDrawable
        {
            private readonly int number;
            private readonly string label;
            private Box background = null!;
            private OsuSpriteText numberText = null!;
            private OsuSpriteText labelText = null!;
            private StepState state = (StepState)(-1);

            public PhaseStep(int number, string label)
            {
                this.number = number;
                this.label = label;
                RelativeSizeAxes = Axes.Y;
            }

            [BackgroundDependencyLoader]
            private void load(OsuColour colours)
            {
                InternalChild = new Container
                {
                    RelativeSizeAxes = Axes.Both,
                    Masking = true,
                    CornerRadius = 7,
                    Children =
                    [
                        background = new Box
                        {
                            RelativeSizeAxes = Axes.Both,
                        },
                        numberText = new OsuSpriteText
                        {
                            Anchor = Anchor.CentreLeft,
                            Origin = Anchor.CentreLeft,
                            X = 13,
                            Text = number.ToString(),
                            Font = OsuFont.GetFont(size: 13, weight: FontWeight.Bold, fixedWidth: true),
                        },
                        labelText = new OsuSpriteText
                        {
                            Anchor = Anchor.CentreLeft,
                            Origin = Anchor.CentreLeft,
                            X = 37,
                            Text = label,
                            Font = OsuFont.GetFont(size: 13, weight: FontWeight.Bold),
                        },
                    ],
                };
                setState(StepState.Pending, colours);
            }

            public void setState(StepState next)
            {
                if (next == state || background == null)
                    return;

                setState(next, null);
            }

            private void setState(StepState next, OsuColour? colours)
            {
                state = next;
                Color4 backgroundColour;
                Color4 foreground;
                switch (next)
                {
                    case StepState.Complete:
                        backgroundColour = Color4Extensions.FromHex("#29483D").Opacity(0.94f);
                        foreground = Color4Extensions.FromHex("#B8F06A");
                        break;

                    case StepState.Current:
                        backgroundColour = (colours?.Pink2 ?? Color4Extensions.FromHex("#E83D8E")).Opacity(0.96f);
                        foreground = Color4.White;
                        break;

                    default:
                        backgroundColour = Color4Extensions.FromHex("#262B3D").Opacity(0.92f);
                        foreground = Color4Extensions.FromHex("#A4A9B8");
                        break;
                }

                background.Colour = backgroundColour;
                numberText.Colour = foreground;
                labelText.Colour = foreground;
            }
        }
    }
}
