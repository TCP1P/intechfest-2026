namespace Rythme.Desktop;

internal static class ProtocolValidation
{
    public static T Validate<T>(T payload, int statusCode)
    {
        if (payload == null)
            throw invalid(statusCode, "response body is null");

        switch (payload)
        {
            case SessionResponse session:
                validateSession(session, statusCode);
                break;

            case ArenaView arena:
                validateArena(arena, statusCode);
                break;
        }

        return payload;
    }

    private static void validateSession(SessionResponse session, int statusCode)
    {
        requireText(session.AccessToken, "session.access_token", statusCode);
        requireText(session.TokenType, "session.token_type", statusCode);
        requireText(session.TeamId, "session.team_id", statusCode);
        requireText(session.TeamName, "session.team_name", statusCode);
        requireText(session.ChannelId, "session.channel_id", statusCode);
        requireText(session.ServerPublicKey, "session.server_public_key", statusCode);
        requireText(session.ServerNonce, "session.server_nonce", statusCode);
        requireText(session.ServerProof, "session.server_proof", statusCode);
        if (session.ChannelExpiresAtUnixMs <= 0)
            throw invalid(statusCode, "session.channel_expires_at_unix_ms is invalid");
    }

    private static void validateArena(ArenaView arena, int statusCode)
    {
        requireText(arena.PoolSchema, "state.pool_schema", statusCode);
        HeatView heat = arena.Heat ?? throw invalid(statusCode, "state.heat is null");
        SeriesView series = arena.Series ?? throw invalid(statusCode, "state.series is null");

        requireText(heat.Id, "state.heat.id", statusCode);
        requireText(heat.Phase, "state.heat.phase", statusCode);
        requireNotNull(heat.Commitment, "state.heat.commitment", statusCode);
        if (heat.Phase is not ("enrollment" or "reveal" or "running" or "submission" or "results"))
            throw invalid(statusCode, "state.heat.phase is unknown");
        if (heat.Performers == null)
            throw invalid(statusCode, "state.heat.performers is null");
        foreach (PerformerView? performer in heat.Performers)
        {
            if (performer == null)
                throw invalid(statusCode, "state.heat.performers contains null");
            requireText(performer.TeamId, "state.heat.performers.team_id", statusCode);
            requireText(performer.TeamName, "state.heat.performers.team_name", statusCode);
        }

        if (series.Standings == null)
            throw invalid(statusCode, "state.series.standings is null");
        foreach (SeriesStandingView? standing in series.Standings)
        {
            if (standing == null)
                throw invalid(statusCode, "state.series.standings contains null");
            requireText(standing.TeamId, "state.series.standings.team_id", statusCode);
            requireText(standing.TeamName, "state.series.standings.team_name", statusCode);
        }

        if (heat.Selection is Selection selection)
        {
            validateSelection(selection, statusCode);
        }
        else if (heat.Phase != "enrollment")
        {
            throw invalid(statusCode, $"state.heat.selection is null during {heat.Phase}");
        }
    }

    private static void validateSelection(Selection selection, int statusCode)
    {
        requireText(selection.SongId, "state.heat.selection.song_id", statusCode);
        requireText(selection.SongTitle, "state.heat.selection.song_title", statusCode);
        requireText(selection.SongArtist, "state.heat.selection.song_artist", statusCode);
        requireText(selection.Ruleset, "state.heat.selection.ruleset", statusCode);
        requireText(selection.ChartDigest, "state.heat.selection.chart_digest", statusCode);
        requireText(selection.ControlProfile, "state.heat.selection.control_profile", statusCode);
        requireText(selection.Difficulty, "state.heat.selection.difficulty", statusCode);
        requireText(selection.ArchiveSha256, "state.heat.selection.archive_sha256", statusCode);
        requireText(selection.Nonce, "state.heat.selection.nonce", statusCode);
        if (selection.Ruleset is not ("osu" or "taiko" or "fruits" or "mania"))
            throw invalid(statusCode, "state.heat.selection.ruleset is unknown");
        if (selection.Bpm <= 0 ||
            selection.DurationMs <= 0 ||
            selection.NoteCount <= 0 ||
            selection.BeatmapsetId <= 0 ||
            selection.BeatmapId <= 0)
            throw invalid(statusCode, "state.heat.selection metadata is invalid");
        if (selection.ArchiveSha256.Length != 64 ||
            selection.ArchiveSha256.Any(character => !Uri.IsHexDigit(character)))
            throw invalid(statusCode, "state.heat.selection.archive_sha256 is invalid");
    }

    private static void requireText(string? value, string field, int statusCode)
    {
        if (string.IsNullOrWhiteSpace(value))
            throw invalid(statusCode, $"{field} is empty");
    }

    private static void requireNotNull(string? value, string field, int statusCode)
    {
        if (value == null)
            throw invalid(statusCode, $"{field} is null");
    }

    private static ArenaProtocolException invalid(int statusCode, string detail) =>
        new(statusCode, $"Coordinator returned an invalid payload: {detail}.");
}
