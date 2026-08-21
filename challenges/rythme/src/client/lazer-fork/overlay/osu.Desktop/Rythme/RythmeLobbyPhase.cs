namespace osu.Desktop.Rythme
{
    internal static class RythmeLobbyPhase
    {
        public const int STEP_COUNT = 4;

        public static int StepIndex(string phase) => phase switch
        {
            "enrollment" => 0,
            "reveal" => 1,
            "running" => 2,
            "submission" or "results" => 3,
            _ => -1,
        };

        public static string DisplayName(string phase) => phase switch
        {
            "enrollment" => "JOIN",
            "reveal" => "REVEAL",
            "running" => "PLAY",
            "submission" => "SUBMIT",
            "results" => "RESULTS",
            _ => "SYNCING",
        };
    }
}
