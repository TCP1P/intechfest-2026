using System.Text.Json.Serialization;

namespace Rythme.Desktop;

internal static class ProtocolConstants
{
    public const string SecureChannel = "rythme-secure-channel-v3";
    public const string ClientBuild = "r6-0f3d3c3-8f21";
    public const string EmptySettingsHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
}

internal sealed record SessionResponse
{
    [JsonPropertyName("access_token")]
    public string AccessToken { get; init; } = "";

    [JsonPropertyName("token_type")]
    public string TokenType { get; init; } = "";

    [JsonPropertyName("team_id")]
    public string TeamId { get; init; } = "";

    [JsonPropertyName("team_name")]
    public string TeamName { get; init; } = "";

    [JsonPropertyName("channel_id")]
    public string ChannelId { get; init; } = "";

    [JsonPropertyName("server_public_key")]
    public string ServerPublicKey { get; init; } = "";

    [JsonPropertyName("server_nonce")]
    public string ServerNonce { get; init; } = "";

    [JsonPropertyName("server_proof")]
    public string ServerProof { get; init; } = "";

    [JsonPropertyName("channel_expires_at_unix_ms")]
    public long ChannelExpiresAtUnixMs { get; init; }

    [JsonIgnore]
    public SecureChannel? Channel { get; set; }
}

internal sealed record EnrollResponse(
    [property: JsonPropertyName("heat_id")] string HeatId,
    [property: JsonPropertyName("heat_ticket_id")] string HeatTicketId);

internal sealed record ArenaView
{
    [JsonPropertyName("server_now_unix_ms")]
    public long ServerNowUnixMs { get; init; }

    [JsonPropertyName("pool_schema")]
    public string PoolSchema { get; init; } = "";

    [JsonPropertyName("crown_owner_id")]
    public string? CrownOwnerId { get; init; }

    [JsonPropertyName("crown_owner_name")]
    public string? CrownOwnerName { get; init; }

    [JsonPropertyName("crown_threshold")]
    public long CrownThreshold { get; init; }

    [JsonPropertyName("raw_score_floor")]
    public long RawScoreFloor { get; init; }

    [JsonPropertyName("heat")]
    public HeatView Heat { get; init; } = new();

    [JsonPropertyName("series")]
    public SeriesView Series { get; init; } = new();

    [JsonPropertyName("ruleset_bag_rule")]
    public string RulesetBagRule { get; init; } = "";

    [JsonPropertyName("series_rule")]
    public string SeriesRule { get; init; } = "";
}

internal sealed record SeriesView
{
    [JsonPropertyName("index")]
    public long Index { get; init; }

    [JsonPropertyName("current_leg")]
    public int CurrentLeg { get; init; }

    [JsonPropertyName("total_legs")]
    public int TotalLegs { get; init; }

    [JsonPropertyName("standings")]
    public SeriesStandingView[] Standings { get; init; } = [];
}

internal sealed record SeriesStandingView
{
    [JsonPropertyName("team_id")]
    public string TeamId { get; init; } = "";

    [JsonPropertyName("team_name")]
    public string TeamName { get; init; } = "";

    [JsonPropertyName("completed_legs")]
    public int CompletedLegs { get; init; }

    [JsonPropertyName("eligible_legs")]
    public int EligibleLegs { get; init; }

    [JsonPropertyName("weakest_settled")]
    public long WeakestSettled { get; init; }

    [JsonPropertyName("average_raw")]
    public long AverageRaw { get; init; }

    [JsonPropertyName("average_settled")]
    public long AverageSettled { get; init; }

    [JsonPropertyName("complete")]
    public bool Complete { get; init; }
}

internal sealed record HeatView
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = "";

    [JsonPropertyName("index")]
    public long Index { get; init; }

    [JsonPropertyName("phase")]
    public string Phase { get; init; } = "";

    [JsonPropertyName("commitment")]
    public string Commitment { get; init; } = "";

    [JsonPropertyName("enrollment_ends_unix_ms")]
    public long EnrollmentEndsUnixMs { get; init; }

    [JsonPropertyName("start_at_unix_ms")]
    public long StartAtUnixMs { get; init; }

    [JsonPropertyName("end_at_unix_ms")]
    public long EndAtUnixMs { get; init; }

    [JsonPropertyName("submission_ends_unix_ms")]
    public long SubmissionEndsUnixMs { get; init; }

    [JsonPropertyName("next_heat_at_unix_ms")]
    public long NextHeatAtUnixMs { get; init; }

    [JsonPropertyName("selection")]
    public Selection? Selection { get; init; }

    [JsonPropertyName("performers")]
    public PerformerView[] Performers { get; init; } = [];
}

internal sealed record PerformerView
{
    [JsonPropertyName("team_id")]
    public string TeamId { get; init; } = "";

    [JsonPropertyName("team_name")]
    public string TeamName { get; init; } = "";

    [JsonPropertyName("ready")]
    public bool Ready { get; init; }

    [JsonPropertyName("applied_mods")]
    public AppliedModProfile? AppliedMods { get; init; }

    [JsonPropertyName("frames")]
    public int Frames { get; init; }

    [JsonPropertyName("completion_sent")]
    public bool ManifestSent { get; init; }

    [JsonPropertyName("raw_basis")]
    public long RawBasis { get; init; }

    [JsonPropertyName("settled_score")]
    public long SettledScore { get; init; }

    [JsonPropertyName("eligible")]
    public bool Eligible { get; init; }
}

internal sealed record Selection
{
    [JsonPropertyName("song_id")]
    public string SongId { get; init; } = "";

    [JsonPropertyName("song_title")]
    public string SongTitle { get; init; } = "";

    [JsonPropertyName("song_artist")]
    public string SongArtist { get; init; } = "";

    [JsonPropertyName("ruleset")]
    public string Ruleset { get; init; } = "";

    [JsonPropertyName("chart_digest")]
    public string ChartDigest { get; init; } = "";

    [JsonPropertyName("control_profile")]
    public string ControlProfile { get; init; } = "";

    [JsonPropertyName("bpm")]
    public int Bpm { get; init; }

    [JsonPropertyName("duration_ms")]
    public long DurationMs { get; init; }

    [JsonPropertyName("lead_in_ms")]
    public long LeadInMs { get; init; }

    [JsonPropertyName("note_count")]
    public int NoteCount { get; init; }

    [JsonPropertyName("origin")]
    public string Origin { get; init; } = "";

    [JsonPropertyName("beatmapset_id")]
    public long BeatmapsetId { get; init; }

    [JsonPropertyName("beatmap_id")]
    public long BeatmapId { get; init; }

    [JsonPropertyName("difficulty")]
    public string Difficulty { get; init; } = "";

    [JsonPropertyName("star_rating")]
    public double StarRating { get; init; }

    [JsonPropertyName("status")]
    public string Status { get; init; } = "";

    [JsonPropertyName("key_count")]
    public int KeyCount { get; init; }

    [JsonPropertyName("mapper")]
    public string Mapper { get; init; } = "";

    [JsonPropertyName("source_url")]
    public string SourceUrl { get; init; } = "";

    [JsonPropertyName("official_url")]
    public string OfficialUrl { get; init; } = "";

    [JsonPropertyName("license")]
    public string License { get; init; } = "";

    [JsonPropertyName("archive_sha256")]
    public string ArchiveSha256 { get; init; } = "";

    [JsonPropertyName("nonce")]
    public string Nonce { get; init; } = "";

    [JsonPropertyName("series_index")]
    public long SeriesIndex { get; init; }

    [JsonPropertyName("series_leg")]
    public int SeriesLeg { get; init; }

}

internal sealed record InputFrame
{
    [JsonPropertyName("heat_ticket_id")]
    public string HeatTicketId { get; init; } = "";

    [JsonPropertyName("sequence")]
    public ulong Sequence { get; init; }

    [JsonPropertyName("ruleset_id")]
    public string Ruleset { get; init; } = "";

    [JsonPropertyName("note_index")]
    public int NoteIndex { get; init; }

    [JsonPropertyName("cue_token")]
    public string CueToken { get; init; } = "";

    [JsonPropertyName("action_bits")]
    public ushort ActionBits { get; init; }

    [JsonPropertyName("pointer_x_u16")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public ushort? PointerX { get; init; }

    [JsonPropertyName("pointer_y_u16")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public ushort? PointerY { get; init; }
}

internal sealed record Cue
{
    [JsonPropertyName("note_index")]
    public int NoteIndex { get; init; }

    [JsonPropertyName("opens_unix_ms")]
    public long OpensUnixMs { get; init; }

    [JsonPropertyName("expires_unix_ms")]
    public long ExpiresUnixMs { get; init; }

    [JsonPropertyName("token")]
    public string Token { get; init; } = "";
}

internal sealed record CueResponse
{
    [JsonPropertyName("heat_id")]
    public string HeatId { get; init; } = "";

    [JsonPropertyName("server_now_unix_ms")]
    public long ServerNowUnixMs { get; init; }

    [JsonPropertyName("cues")]
    public Cue[] Cues { get; init; } = [];
}

internal sealed record ModClaim
{
    [JsonPropertyName("acronym")]
    public string Acronym { get; init; } = "";

    [JsonPropertyName("rate_milli")]
    public int RateMilli { get; init; }

    [JsonPropertyName("settings_hash")]
    public string SettingsHash { get; init; } = "";
}

internal sealed record AppliedModProfile
{
    [JsonPropertyName("acronym")]
    public string Acronym { get; init; } = "";

    [JsonPropertyName("rate_milli")]
    public int RateMilli { get; init; }

    [JsonPropertyName("settings_hash")]
    public string SettingsHash { get; init; } = "";

    [JsonPropertyName("score_multiplier_milli")]
    public int ScoreMultiplierMilli { get; init; }
}

internal sealed record JudgeResult
{
    [JsonPropertyName("hit_count")]
    public int HitCount { get; init; }

    [JsonPropertyName("note_count")]
    public int NoteCount { get; init; }

    [JsonPropertyName("raw_basis")]
    public long RawBasis { get; init; }

    [JsonPropertyName("accuracy")]
    public double Accuracy { get; init; }

    [JsonPropertyName("max_combo")]
    public int MaxCombo { get; init; }

    [JsonPropertyName("input_digest")]
    public string InputDigest { get; init; } = "";

}

internal sealed record ResultResponse
{
    [JsonPropertyName("judge")]
    public JudgeResult Judge { get; init; } = new();
}

internal sealed record Settlement
{
    [JsonPropertyName("team_id")]
    public string TeamId { get; init; } = "";

    [JsonPropertyName("team_name")]
    public string TeamName { get; init; } = "";

    [JsonPropertyName("eligible")]
    public bool Eligible { get; init; }

    [JsonPropertyName("reason")]
    public string? Reason { get; init; }

    [JsonPropertyName("applied_mods")]
    public AppliedModProfile? AppliedMods { get; init; }

    [JsonPropertyName("final_mods")]
    public ModClaim? AttestedMods { get; init; }

    [JsonPropertyName("applied_multiplier")]
    public double AppliedMultiplier { get; init; }

    [JsonPropertyName("final_multiplier")]
    public double AttestedMultiplier { get; init; }

    [JsonPropertyName("multiplier")]
    public double Multiplier { get; init; }

    [JsonPropertyName("settled_score")]
    public long SettledScore { get; init; }

    [JsonPropertyName("judge")]
    public JudgeResult Judge { get; init; } = new();

    [JsonPropertyName("series_index")]
    public long SeriesIndex { get; init; }

    [JsonPropertyName("series_leg")]
    public int SeriesLeg { get; init; }
}

internal sealed record ManifestResponse
{
    [JsonPropertyName("settlement")]
    public Settlement Settlement { get; init; } = new();

    [JsonPropertyName("took_crown")]
    public bool TookCrown { get; init; }
}

internal sealed record InputResponse(
    [property: JsonPropertyName("accepted")] int Accepted,
    [property: JsonPropertyName("input_digest")] string InputDigest);

internal sealed record LocalSessionRequest
{
    [JsonPropertyName("team_id")]
    public string TeamId { get; init; } = "";

    [JsonPropertyName("team_name")]
    public string TeamName { get; init; } = "";

    [JsonPropertyName("client_public_key")]
    public string ClientPublicKey { get; init; } = "";

    [JsonPropertyName("client_nonce")]
    public string ClientNonce { get; init; } = "";
}

internal sealed record RctfSessionRequest
{
    [JsonPropertyName("team_token")]
    public string TeamToken { get; init; } = "";

    [JsonPropertyName("client_public_key")]
    public string ClientPublicKey { get; init; } = "";

    [JsonPropertyName("client_nonce")]
    public string ClientNonce { get; init; } = "";
}
