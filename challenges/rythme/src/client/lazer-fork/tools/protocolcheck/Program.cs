using Rythme.Desktop;

SessionResponse session = validSession();
require(ReferenceEquals(ProtocolValidation.Validate(session, 201), session), "valid session changed");
expectInvalid(
    () => ProtocolValidation.Validate(session with { AccessToken = null! }, 201),
    "session.access_token");

ArenaView arena = validArena();
require(ReferenceEquals(ProtocolValidation.Validate(arena, 200), arena), "valid arena state changed");
expectInvalid(
    () => ProtocolValidation.Validate(arena with { Heat = null! }, 200),
    "state.heat");
expectInvalid(
    () => ProtocolValidation.Validate(
        arena with { Heat = arena.Heat with { Performers = null! } },
        200),
    "state.heat.performers");
expectInvalid(
    () => ProtocolValidation.Validate(
        arena with { Heat = arena.Heat with { Performers = [null!] } },
        200),
    "state.heat.performers");
expectInvalid(
    () => ProtocolValidation.Validate(
        arena with { Series = arena.Series with { Standings = null! } },
        200),
    "state.series.standings");
expectInvalid(
    () => ProtocolValidation.Validate(
        arena with { Heat = arena.Heat with { Phase = "reveal", Selection = null } },
        200),
    "state.heat.selection");
expectInvalid(
    () => ProtocolValidation.Validate(
        arena with { Heat = arena.Heat with { Phase = "unknown" } },
        200),
    "state.heat.phase");
expectInvalid(
    () => ProtocolValidation.Validate<object>(null!, 200),
    "response body");

ManifestResponse receipt = validReceipt();
string receiptSummary = ArenaReceiptSummary.Build(receipt, null, null, "NM");
require(receiptSummary.Contains("WAVE 1/4 RECEIPT", StringComparison.Ordinal), "accepted receipt was not shown immediately");
require(receiptSummary.Contains("Official score: ACCEPTED", StringComparison.Ordinal), "accepted official score was hidden behind exploit eligibility");
require(receiptSummary.Contains("wave Crown updates", StringComparison.Ordinal), "pending crown result was not explained");
require(!receiptSummary.Contains("RUN LINK ERROR", StringComparison.Ordinal), "pending crown result was treated as a link failure");

string warnedSummary = ArenaReceiptSummary.Build(receipt, null, "one cue expired", "NM");
require(warnedSummary.Contains("INPUT DELIVERY WARNING", StringComparison.Ordinal), "recoverable cue miss hid its warning");
require(warnedSummary.Contains("Settled: 123,456", StringComparison.Ordinal), "recoverable cue miss hid the official receipt");

string failedSummary = ArenaReceiptSummary.Build(receipt, "channel failed", null, "NM");
require(failedSummary == "RUN LINK ERROR\nchannel failed", "fatal run-link failure was not prioritized");

const string missedStartSummary = "WAVE SKIPPED — SYNCHRONIZED START MISSED";
require(
    ArenaReceiptSummary.Build(null, null, null, "NM", missedStartSummary) == missedStartSummary,
    "intentional missed-start return was presented as a score failure");

Pool pool = validPool();
PoolCatalog.Validate(pool);
expectInvalidOperation(() => PoolCatalog.Validate(pool with { Songs = null! }), "no songs");
expectInvalidOperation(() => PoolCatalog.Validate(pool with { Songs = [null!] }), "null song");
expectInvalidOperation(
    () => PoolCatalog.Validate(pool with { Songs = [pool.Songs[0] with { ArchiveSha256 = "bad" }] }),
    "checksum");
expectInvalidOperation(
    () => PoolCatalog.ExtractedChart(pool.Songs[0] with { Notes = null! }),
    "chart data");

long expiry = 10_000;
require(
    CueDeliveryPolicy.HasSendBudget(expiry - CueDeliveryPolicy.SendSafetyMarginMs - 1, expiry),
    "cue with sufficient delivery budget was rejected");
require(
    !CueDeliveryPolicy.HasSendBudget(expiry - CueDeliveryPolicy.SendSafetyMarginMs, expiry),
    "cue at the delivery safety boundary was accepted");
require(
    !CueDeliveryPolicy.HasSendBudget(expiry + 250, expiry),
    "expired cue was accepted with the former client-side grace period");
require(
    CueDeliveryPolicy.IsRecoverableExpiredCue(
        new ArenaProtocolException(401, "unauthorized: frame 0 has an unavailable or expired cue")),
    "coordinator expired-cue rejection was not recoverable");
require(
    !CueDeliveryPolicy.IsRecoverableExpiredCue(new ArenaProtocolException(401, "unauthorized: invalid channel")),
    "unrelated authentication rejection was made recoverable");
require(
    !CueDeliveryPolicy.IsRecoverableExpiredCue(
        new ArenaProtocolException(403, "frame 0 has an unavailable or expired cue")),
    "non-authentication protocol failure was made recoverable");

Console.WriteLine("protocol null-boundary checks passed");

static SessionResponse validSession() => new()
{
    AccessToken = "access",
    TokenType = "Bearer",
    TeamId = "team-1",
    TeamName = "Team One",
    ChannelId = "channel-1",
    ServerPublicKey = "public-key",
    ServerNonce = "nonce",
    ServerProof = "proof",
    ChannelExpiresAtUnixMs = 1,
};

static ArenaView validArena() => new()
{
    ServerNowUnixMs = 1,
    PoolSchema = "rythme-pool-v2",
    Heat = new HeatView
    {
        Id = "heat-1",
        Phase = "enrollment",
        Commitment = "commitment",
        Performers = [],
    },
    Series = new SeriesView
    {
        Index = 1,
        CurrentLeg = 1,
        TotalLegs = 4,
        Standings = [],
    },
};

static ManifestResponse validReceipt() => new()
{
    Settlement = new Settlement
    {
        TeamId = "team-1",
        TeamName = "Team One",
        Eligible = true,
        AppliedMultiplier = 1,
        Multiplier = 1,
        SettledScore = 123_456,
        Judge = new JudgeResult
        {
            HitCount = 95,
            NoteCount = 100,
            RawBasis = 987_654,
        },
        SeriesIndex = 7,
        SeriesLeg = 1,
    },
    TookCrown = false,
};

static Pool validPool() => new()
{
    Schema = "rythme-pool-v2",
    Songs =
    [
        new Song
        {
            Id = "song-1",
            Title = "Song",
            Artist = "Artist",
            Ruleset = "osu",
            Bpm = 180,
            DurationMs = 60_000,
            NoteCount = 100,
            BeatmapsetId = 1,
            BeatmapId = 2,
            Difficulty = "Hard",
            ArchiveSha256 = new string('a', 64),
            Notes = [],
        },
    ],
};

static void expectInvalid(Action action, string field)
{
    try
    {
        action();
    }
    catch (ArenaProtocolException error) when (error.Message.Contains(field, StringComparison.Ordinal))
    {
        return;
    }

    throw new InvalidOperationException($"invalid protocol field {field} was accepted");
}

static void expectInvalidOperation(Action action, string detail)
{
    try
    {
        action();
    }
    catch (InvalidOperationException error) when (error.Message.Contains(detail, StringComparison.OrdinalIgnoreCase))
    {
        return;
    }

    throw new InvalidOperationException($"invalid pool field {detail} was accepted");
}

static void require(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}
