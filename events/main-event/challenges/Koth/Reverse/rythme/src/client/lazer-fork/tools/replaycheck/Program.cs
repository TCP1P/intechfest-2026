using osu.Game.Rulesets.Taiko.UI;
using osu.Game.Scoring;

var score = new Score();
var recorder = new TaikoReplayRecorder(score);

recorder.RecordFrame(true);

if (score.Replay.Frames.Count != 0)
    throw new InvalidOperationException("An unloaded replay recorder emitted a frame.");

Console.WriteLine("late-start replay-recorder regression check passed");
