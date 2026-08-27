namespace Rythme.Desktop;

internal sealed record InputResponse
{
    public int Accepted { get; init; } = 2;
}

internal sealed record InputFrame
{
    public string HeatTicketId { get; init; } = "";
    public ulong Sequence { get; init; }
    public string Ruleset { get; init; } = "";
    public int NoteIndex { get; init; }
    public string CueToken { get; init; } = "";
    public ushort ActionBits { get; init; }
    public ushort? PointerX { get; init; }
    public ushort? PointerY { get; init; }
}

internal sealed record Cue
{
    public int NoteIndex { get; init; }
    public long ExpiresUnixMs { get; init; }
    public string Token { get; init; } = "";
}

internal sealed record CueResponse
{
    public string HeatId { get; init; } = "";
    public long ServerNowUnixMs { get; init; }
    public Cue[] Cues { get; init; } = [];
}
