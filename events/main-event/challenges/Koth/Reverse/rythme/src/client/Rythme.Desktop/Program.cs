using System.Globalization;

namespace Rythme.Desktop;

internal static class Program
{
    public static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        using var cancellation = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            cancellation.Cancel();
        };

        try
        {
            Arguments options = Arguments.Parse(args);
            Pool pool = PoolCatalog.Load();
            return options.Command switch
            {
                "arena" => await RunArenaAsync(options, pool, cancellation.Token),
                "practice" => await RunPracticeAsync(options, pool, cancellation.Token),
                "mods" => RunModLab(options),
                "help" => ShowHelp(),
                _ => await RunMenuAsync(options, pool, cancellation.Token)
            };
        }
        catch (OperationCanceledException)
        {
            Console.WriteLine("\nSession cancelled.");
            return 130;
        }
        catch (ArenaProtocolException error)
        {
            Console.Error.WriteLine($"Coordinator rejected the request ({error.StatusCode}): {error.Message}");
            return 2;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"Rythme stopped: {error.Message}");
            return 1;
        }
    }

    private static async Task<int> RunMenuAsync(Arguments options, Pool pool, CancellationToken cancellationToken)
    {
        Header();
        Console.WriteLine("  1  Enter Crown arena");
        Console.WriteLine("  2  Practice a pool chart");
        Console.WriteLine("  3  Inspect pinned modifier catalogues");
        Console.WriteLine();
        Console.Write("Select: ");
        string choice = Console.ReadLine()?.Trim() ?? "";
        return choice switch
        {
            "1" => await RunArenaAsync(options, pool, cancellationToken),
            "2" => await RunPracticeAsync(options, pool, cancellationToken),
            "3" => RunModLab(options),
            _ => 0
        };
    }

    private static async Task<int> RunArenaAsync(Arguments options, Pool pool, CancellationToken cancellationToken)
    {
        Header();
        Console.WriteLine($"Coordinator  {options.Server}");
        using var api = new ArenaApi(options.Server);

        SessionResponse session;
        if (!string.IsNullOrWhiteSpace(options.Token))
            session = await api.CreateRctfSessionAsync(options.Token, cancellationToken);
        else
        {
            string teamId = options.TeamId ?? Prompt("Team ID", "local-crew");
            string teamName = options.TeamName ?? Prompt("Crew name", teamId);
            session = await api.CreateLocalSessionAsync(teamId, teamName, cancellationToken);
        }

        ArenaView state = await api.StateAsync(cancellationToken);
        while (state.Heat.Phase != "enrollment")
        {
            long remaining = Math.Max(0, state.Heat.NextHeatAtUnixMs - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            Console.Write($"\rWaiting for next enrollment: {remaining / 1000.0,6:0.0}s ");
            await Task.Delay(250, cancellationToken);
            state = await api.StateAsync(cancellationToken);
        }
        Console.WriteLine();

        EnrollResponse enrollment = await api.EnrollAsync(cancellationToken);
        Console.WriteLine($"Enrolled in {enrollment.HeatId}; selection commitment {state.Heat.Commitment[..12]}…");

        ArenaView revealed;
        do
        {
            await Task.Delay(100, cancellationToken);
            revealed = await api.StateAsync(cancellationToken);
            if (revealed.Heat.Id != enrollment.HeatId)
                throw new InvalidOperationException("Heat rolled over before reveal.");
        } while (revealed.Heat.Selection == null);

        Selection selection = revealed.Heat.Selection;
        Song song = PoolCatalog.Resolve(pool, selection);
        Note[] notes = PoolCatalog.ExtractedChart(song);
        string digest = PoolCatalog.ChartDigest(song, selection.Ruleset, notes);
        if (!string.Equals(digest, selection.ChartDigest, StringComparison.Ordinal))
            throw new InvalidOperationException("Selected chart failed its embedded-pool digest.");

        AppliedModSet requestedMods = ArenaPolicy.RequiredMods(selection);
        ReadyBinding ready = await api.ReadyAsync(
            enrollment.HeatTicketId,
            selection.Ruleset,
            selection.Nonce,
            requestedMods,
            cancellationToken);
        AppliedModSet appliedMods = ready.AppliedMods;
        Console.WriteLine($"Reveal: {selection.SongTitle} / {selection.Ruleset.ToUpperInvariant()}");
        Console.WriteLine($"Locked: {appliedMods.Acronym} / {appliedMods.ScoreMultiplierMilli / 1000.0:0.00}x score");
        Console.WriteLine($"Controls: {selection.ControlProfile}");
        long serverOffset = await api.MeasureServerOffsetAsync(cancellationToken);
        Console.WriteLine($"Clock lock: server offset {serverOffset:+#;-#;0} ms");

        await using var sender = new InputSender(
            api,
            enrollment.HeatId,
            enrollment.HeatTicketId,
            selection.Ruleset,
            serverOffset,
            cancellationToken);
        PlaySummary play = await TerminalGame.PlayAsync(
            song,
            selection.Ruleset,
            notes,
            revealed.Heat.StartAtUnixMs,
            serverOffset,
            playbackRate: appliedMods.RateMilli / 1000.0,
            sender,
            cancellationToken);
        await sender.DrainAsync();

        ArenaView afterPlay;
        do
        {
            afterPlay = await api.StateAsync(cancellationToken);
            if (afterPlay.Heat.Id != enrollment.HeatId)
                throw new InvalidOperationException("Manifest window closed before submission.");
            if (afterPlay.Heat.Phase is "enrollment" or "reveal" or "running")
                await Task.Delay(50, cancellationToken);
            else if (afterPlay.Heat.Phase != "submission")
                throw new InvalidOperationException("Manifest window closed before submission.");
        } while (afterPlay.Heat.Phase != "submission");

        ResultResponse result = await api.ResultAsync(enrollment.HeatTicketId, cancellationToken);
        ManifestResponse receipt = await api.SubmitCompletionAsync(cancellationToken);

        Console.WriteLine();
        Console.WriteLine("INTEGRITY RECEIPT");
        Console.WriteLine($"  Inputs       {play.Attempts}");
        Console.WriteLine($"  Hits         {receipt.Settlement.Judge.HitCount}/{receipt.Settlement.Judge.NoteCount}");
        Console.WriteLine($"  Raw basis    {receipt.Settlement.Judge.RawBasis:N0}");
        Console.WriteLine($"  Applied      {appliedMods.Acronym} {appliedMods.RateMilli / 1000.0:0.00}x");
        Console.WriteLine($"  Verified     {receipt.Settlement.AttestedMods?.Acronym ?? "-"} " +
                          $"{(receipt.Settlement.AttestedMods?.RateMilli ?? 0) / 1000.0:0.00}x");
        Console.WriteLine($"  Multiplier   {receipt.Settlement.Multiplier:0.00}x");
        Console.WriteLine($"  Settled      {receipt.Settlement.SettledScore:N0}");
        Console.WriteLine("  Official     ACCEPTED");
        Console.WriteLine($"  Exploit boost {(receipt.Settlement.Eligible ? "QUALIFIED" : receipt.Settlement.Reason)}");

        ArenaView final;
        do
        {
            await Task.Delay(100, cancellationToken);
            final = await api.StateAsync(cancellationToken);
        } while (final.Heat.Id == enrollment.HeatId && final.Heat.Phase != "results");

        if (final.CrownOwnerId == session.TeamId)
        {
            Console.WriteLine($"  Crown owner  {session.TeamName}");
            Console.WriteLine("  Wave score submitted to Leaderboard KotH");
            return 0;
        }

        Console.WriteLine($"  Crown owner  {final.CrownOwnerName ?? "unclaimed"}");
        Console.WriteLine("  Wave score submitted to Leaderboard KotH");
        return 0;
    }

    private static async Task<int> RunPracticeAsync(Arguments options, Pool pool, CancellationToken cancellationToken)
    {
        Header();
        string ruleset = options.Ruleset ?? Choose("Ruleset", ["osu", "taiko", "fruits", "mania"]);
        Song[] compatibleSongs = pool.Songs.Where(item => item.Ruleset == ruleset).ToArray();
        Song song = options.SongId == null
            ? compatibleSongs[ChooseIndex(
                "Song",
                compatibleSongs.Select(item => $"{item.Title} · {item.Difficulty} · {item.StarRating:0.00}★").ToArray())]
            : compatibleSongs.Single(item => item.Id == options.SongId);
        IReadOnlyList<ModEntry> catalogue = UpstreamModCatalog.Read(ruleset);
        string acronym = options.Mod ?? Prompt("Practice mod acronym", "NM").ToUpperInvariant();
        ModEntry? selectedMod = acronym == "NM"
            ? null
            : catalogue.FirstOrDefault(entry => entry.Acronym.Equals(acronym, StringComparison.OrdinalIgnoreCase));
        if (acronym != "NM" && selectedMod == null)
            throw new InvalidOperationException($"{acronym} is not in the pinned {ruleset} catalogue.");

        double rate = selectedMod?.PlaybackRate ?? 1;
        double multiplier = selectedMod?.DefaultMultiplier ?? 1;
        Console.WriteLine($"Practice: {song.Title} / {ruleset.ToUpperInvariant()} / {acronym}");
        Console.WriteLine($"Pinned defaults: playback {rate:0.00}x, score multiplier {multiplier:0.000}x");
        if (selectedMod is { Implemented: false })
            Console.WriteLine("This upstream catalogue entry has no gameplay implementation.");
        if (selectedMod is not null && Math.Abs(selectedMod.PlaybackRate - 1) < 0.001)
            Console.WriteLine("The compact practice renderer reports this mod's multiplier but does not reproduce its visual transform.");
        Console.WriteLine("Starting in two seconds…");

        Note[] notes = PoolCatalog.ExtractedChart(song);
        long start = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 2000;
        PlaySummary summary = await TerminalGame.PlayAsync(
            song, ruleset, notes, start, 0, rate, null, cancellationToken);
        Console.WriteLine($"Practice complete: {summary.Attempts} inputs.");
        return 0;
    }

    private static int RunModLab(Arguments options)
    {
        Header();
        string ruleset = options.Ruleset ?? Choose("Ruleset", ["osu", "taiko", "fruits", "mania"]);
        IReadOnlyList<ModEntry> entries = UpstreamModCatalog.Read(ruleset);
        Console.WriteLine($"Pinned upstream modifier catalogue / {ruleset.ToUpperInvariant()}");
        Console.WriteLine("Arena modifier choices lock at ready-up and affect native gameplay.\n");
        Console.WriteLine("ACR  TYPE                   SCORE     RATE   PLAYABLE  NAME");
        Console.WriteLine("───  ─────────────────────  ────────  ─────  ────────  ─────────────────────────");
        foreach (ModEntry entry in entries)
        {
            Console.WriteLine(
                $"{entry.Acronym,-3}  {entry.Type,-21}  {entry.DefaultMultiplier,7:0.000}x  {entry.PlaybackRate,4:0.00}x  " +
                $"{(entry.UserPlayable && entry.Implemented ? "yes" : "no"),-8}  {entry.Name}");
        }
        Console.WriteLine($"\n{entries.Count} built-in entries loaded from the pinned osu! ruleset assembly.");
        return 0;
    }

    private static int ShowHelp()
    {
        Console.WriteLine(
            """
            rythme-client [arena|practice|mods] [options]

              --server URL       Coordinator override for local development
              --team ID          local or rCTF team identifier
              --name NAME        crew display name
              --token TOKEN      RSCTF koth_ token (no team ID or name needed)
              --ruleset ID       osu, taiko, fruits, or mania
              --song ID          pool song for practice
              --mod ACRONYM      practice modifier
            """);
        return 0;
    }

    private static void Header()
    {
        Console.WriteLine("RYTHME  /  SYNCED CROWN CLIENT");
        Console.WriteLine("──────────────────────────────");
    }

    private static string Prompt(string label, string fallback)
    {
        Console.Write($"{label} [{fallback}]: ");
        string? value = Console.ReadLine();
        return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
    }

    private static string Choose(string label, string[] values) => values[ChooseIndex(label, values)];

    private static int ChooseIndex(string label, string[] values)
    {
        for (int i = 0; i < values.Length; i++)
            Console.WriteLine($"  {i + 1}  {values[i]}");
        Console.Write($"{label}: ");
        if (!int.TryParse(Console.ReadLine(), NumberStyles.None, CultureInfo.InvariantCulture, out int selection) ||
            selection < 1 || selection > values.Length)
            throw new InvalidOperationException($"Select a value from 1 to {values.Length}.");
        return selection - 1;
    }
}

internal sealed record Arguments(
    string Command,
    Uri Server,
    string? TeamId,
    string? TeamName,
    string? Token,
    string? Ruleset,
    string? SongId,
    string? Mod)
{
    public static Arguments Parse(string[] args)
    {
        string command = args.FirstOrDefault(arg => !arg.StartsWith("--", StringComparison.Ordinal)) ?? "menu";
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (int i = 0; i < args.Length; i++)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal))
                continue;
            if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                throw new ArgumentException($"{args[i]} requires a value.");
            values[args[i]] = args[++i];
        }

        string serverValue = Value("--server") ?? Environment.GetEnvironmentVariable("RYTHME_SERVER")
                             ?? "https://rythme-private.tcp.1pc.tf/";
        if (!Uri.TryCreate(serverValue, UriKind.Absolute, out Uri? server) ||
            server.Scheme is not ("http" or "https"))
            throw new ArgumentException("--server must be an absolute HTTP(S) URL.");
        if (server.Scheme == "http" && !server.IsLoopback)
            throw new ArgumentException("--server requires HTTPS for non-loopback coordinators.");

        return new Arguments(
            command.ToLowerInvariant(),
            server,
            Value("--team"),
            Value("--name"),
            Value("--token") ?? Environment.GetEnvironmentVariable("RYTHME_TOKEN"),
            Value("--ruleset"),
            Value("--song"),
            Value("--mod"));

        string? Value(string key) => values.GetValueOrDefault(key);
    }
}
