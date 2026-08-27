namespace Rythme.Desktop;

internal static class ArenaReceiptSummary
{
    public static string Build(
        ManifestResponse? receipt,
        string? failure,
        string? warning,
        string appliedMods,
        string? returnMessage = null)
    {
        if (!string.IsNullOrWhiteSpace(returnMessage))
            return returnMessage;

        if (!string.IsNullOrWhiteSpace(failure))
            return $"RUN LINK ERROR\n{failure}";

        if (receipt == null)
            return "The local score completed, but no arena receipt was accepted.";

        ArgumentException.ThrowIfNullOrWhiteSpace(appliedMods);
        Settlement settlement = receipt.Settlement;
        string result = $"ROTATION {settlement.SeriesIndex:0000}  •  WAVE {settlement.SeriesLeg}/4 RECEIPT\n" +
                        $"Hits: {settlement.Judge.HitCount}/{settlement.Judge.NoteCount}  Raw: {settlement.Judge.RawBasis:N0}\n" +
                        $"Applied: {appliedMods} {settlement.AppliedMultiplier:0.00}x  " +
                        $"Effective: {settlement.Multiplier:0.00}x  Settled: {settlement.SettledScore:N0}\n" +
                        $"Official score: ACCEPTED  •  Exploit boost: " +
                        (settlement.Eligible ? "QUALIFIED" : settlement.Reason);

        if (!string.IsNullOrWhiteSpace(warning))
            result += $"\n\nINPUT DELIVERY WARNING\n{warning}\nThe official settlement above remains authoritative.";

        result += receipt.TookCrown
            ? "\n\nCROWN CAPTURED\nThis wave's Crown is yours."
            : "\n\nThe wave Crown updates after every performer is settled.";
        return result;
    }
}
