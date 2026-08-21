using System.Text;

namespace Rythme.Desktop;

internal sealed record PlaySummary(int Attempts, bool Aborted);

internal static class TerminalGame
{
    private const int frameDelayMs = 25;

    public static async Task<PlaySummary> PlayAsync(
        Song song,
        string ruleset,
        Note[] notes,
        long serverStartUnixMs,
        long serverOffsetMs,
        double playbackRate,
        InputSender? sender,
        CancellationToken cancellationToken)
    {
        if (Console.IsInputRedirected)
            throw new InvalidOperationException("Interactive play requires a terminal.");

        int attempts = 0;
        int cursorColumn = 2;
        int cursorRow = 1;
        bool aborted = false;
        var reportedNotes = new HashSet<int>();
        Console.Write("\x1b[?25l\x1b[2J");
        try
        {
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                long estimatedServerNow = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + serverOffsetMs;
                long wallElapsed = estimatedServerNow - serverStartUnixMs;
                long chartElapsed = wallElapsed > 0 ? (long)(wallElapsed * playbackRate) : wallElapsed;

                while (Console.KeyAvailable)
                {
                    ConsoleKeyInfo key = Console.ReadKey(intercept: true);
                    if (key.Key == ConsoleKey.Escape)
                    {
                        aborted = true;
                        break;
                    }

                    ushort? action = null;
                    if (ruleset == "osu")
                    {
                        switch (key.Key)
                        {
                            case ConsoleKey.LeftArrow:
                                cursorColumn = Math.Max(0, cursorColumn - 1);
                                break;
                            case ConsoleKey.RightArrow:
                                cursorColumn = Math.Min(4, cursorColumn + 1);
                                break;
                            case ConsoleKey.UpArrow:
                                cursorRow = Math.Max(0, cursorRow - 1);
                                break;
                            case ConsoleKey.DownArrow:
                                cursorRow = Math.Min(2, cursorRow + 1);
                                break;
                            case ConsoleKey.Z:
                                action = 1;
                                break;
                            case ConsoleKey.X:
                                action = 2;
                                break;
                        }
                    }
                    else
                    {
                        action = ruleset switch
                        {
                            "taiko" => key.Key switch
                            {
                                ConsoleKey.D => 1,
                                ConsoleKey.F => 2,
                                ConsoleKey.J => 4,
                                ConsoleKey.K => 8,
                                _ => null
                            },
                            "fruits" => key.Key switch
                            {
                                ConsoleKey.LeftArrow => 1,
                                ConsoleKey.RightArrow => 2,
                                ConsoleKey.Spacebar => 4,
                                _ => null
                            },
                            "mania" => key.Key switch
                            {
                                ConsoleKey.D => 1,
                                ConsoleKey.F => 2,
                                ConsoleKey.J => 4,
                                ConsoleKey.K => 8,
                                _ => null
                            },
                            _ => null
                        };
                    }

                    if (action.HasValue && chartElapsed >= 0)
                    {
                        attempts++;
                        if (sender != null)
                        {
                            int noteIndex = Enumerable.Range(0, notes.Length)
                                                      .Where(index => !reportedNotes.Contains(index))
                                                      .Where(index => (notes[index].Action & action.Value) != 0)
                                                      .OrderBy(index => Math.Abs(notes[index].AtMs - chartElapsed))
                                                      .FirstOrDefault(-1);
                            if (noteIndex >= 0 &&
                                Math.Abs(notes[noteIndex].AtMs - chartElapsed) <= 220 &&
                                reportedNotes.Add(noteIndex))
                            {
                                Note note = notes[noteIndex];
                                ushort? x = ruleset == "osu" ? note.X : null;
                                ushort? y = ruleset == "osu" ? note.Y : null;
                                await sender.PulseAsync(noteIndex, action.Value, x, y);
                            }
                        }
                    }
                }

                if (aborted || chartElapsed > song.DurationMs)
                    break;

                Render(song, ruleset, notes, chartElapsed, playbackRate, cursorColumn, cursorRow, attempts);
                await Task.Delay(frameDelayMs, cancellationToken);
            }
        }
        finally
        {
            Console.Write("\x1b[?25h\x1b[0m\n");
        }

        return new PlaySummary(attempts, aborted);
    }

    private static void Render(
        Song song,
        string ruleset,
        Note[] notes,
        long elapsed,
        double playbackRate,
        int cursorColumn,
        int cursorRow,
        int attempts)
    {
        var output = new StringBuilder();
        output.Append("\x1b[H\x1b[J");
        output.AppendLine(" RYTHME / LIVE PERFORMANCE");
        output.AppendLine(" ─────────────────────────────────────────────────────────────");
        output.Append(" ").Append(song.Title).Append("  ·  ").Append(ruleset.ToUpperInvariant())
              .Append("  ·  ").Append(playbackRate.ToString("0.00")).AppendLine("x");
        output.Append(" ").Append(Controls(ruleset)).AppendLine("  ·  ESC forfeits");
        output.AppendLine();

        if (elapsed < 0)
        {
            output.Append(" SHARED START IN  ").Append((-elapsed / 1000.0).ToString("0.000")).AppendLine("s");
            output.AppendLine();
            output.AppendLine(" The epoch is fixed. Readiness cannot move it.");
        }
        else
        {
            output.Append(" CHART  ").Append((elapsed / 1000.0).ToString("00.000"))
                  .Append(" / ").Append((song.DurationMs / 1000.0).ToString("00.000"))
                  .Append("    INPUTS ").Append(attempts).AppendLine();
            output.AppendLine();
            if (ruleset == "osu")
                RenderOsuGrid(output, notes, elapsed, cursorColumn, cursorRow);
            else
                RenderLaneQueue(output, notes, elapsed, ruleset);
        }

        while (CountLines(output) < 22)
            output.AppendLine();
        output.Append("\x1b[J");
        Console.Write(output);
    }

    private static void RenderOsuGrid(StringBuilder output, Note[] notes, long elapsed, int cursorColumn, int cursorRow)
    {
        Note? target = notes.FirstOrDefault(note => note.AtMs >= elapsed - 220);
        int targetColumn = target == null ? -1 : Math.Clamp((target.X - 6553 + 6553) / 13107, 0, 4);
        int targetRow = target == null ? -1 : Math.Clamp((target.Y - 16384 + 8192) / 16384, 0, 2);

        output.AppendLine(" Move the cursor onto the target, then tap Z or X at zero.");
        output.AppendLine();
        for (int row = 0; row < 3; row++)
        {
            output.Append("          ");
            for (int column = 0; column < 5; column++)
            {
                char cell = column == cursorColumn && row == cursorRow
                    ? column == targetColumn && row == targetRow ? '◆' : '+'
                    : column == targetColumn && row == targetRow ? '●' : '·';
                output.Append('[').Append(cell).Append("] ");
            }
            output.AppendLine();
        }
        output.AppendLine();
        AppendUpcoming(output, notes, elapsed, "osu");
    }

    private static void RenderLaneQueue(StringBuilder output, Note[] notes, long elapsed, string ruleset)
    {
        output.Append(" HIT LINE  ");
        output.AppendLine(ruleset switch
        {
            "taiko" => "[ D/F DON ]       [ J/K KAT ]",
            "fruits" => "[ ← LEFT  ]       [ RIGHT → ]",
            "mania" => "[ D ]  [ F ]  [ J ]  [ K ]",
            _ => ""
        });
        output.AppendLine("           ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        AppendUpcoming(output, notes, elapsed, ruleset);
    }

    private static void AppendUpcoming(StringBuilder output, Note[] notes, long elapsed, string ruleset)
    {
        Note[] upcoming = notes.Where(note => note.AtMs >= elapsed - 220)
                               .Take(7)
                               .ToArray();
        foreach (Note note in upcoming)
        {
            long delta = note.AtMs - elapsed;
            string marker = Math.Abs(delta) <= 70 ? "NOW " : delta < 0 ? "LATE" : $"{delta / 1000.0,4:0.00}s";
            int barLength = Math.Clamp((int)Math.Round(Math.Max(0, delta) / 100.0), 0, 24);
            output.Append(' ').Append(marker.PadLeft(5)).Append("  ")
                  .Append(new string('·', barLength).PadRight(24, ' '))
                  .Append("  ").Append(NoteLabel(note, ruleset)).AppendLine();
        }
        if (upcoming.Length == 0)
            output.AppendLine(" Final notes cleared.");
    }

    private static string NoteLabel(Note note, string ruleset) => ruleset switch
    {
        "osu" => $"target {((note.X - 6553 + 6553) / 13107) + 1}:{((note.Y - 16384 + 8192) / 16384) + 1}",
        "taiko" => (note.Action & 0x3) != 0 ? "DON · D/F" : "KAT · J/K",
        "fruits" => note.Action == 1 ? "LEFT · ←" : "RIGHT · →",
        "mania" => note.Action switch { 1 => "D", 2 => "F", 4 => "J", 8 => "K", _ => "?" },
        _ => "?"
    };

    private static string Controls(string ruleset) => ruleset switch
    {
        "osu" => "ARROWS move · Z/X hit",
        "taiko" => "D/F don · J/K kat",
        "fruits" => "LEFT/RIGHT catch · SPACE dash",
        "mania" => "4K D/F/J/K",
        _ => ""
    };

    private static ushort PointerX(int column) => (ushort)(6553 + column * 13107);

    private static ushort PointerY(int row) => (ushort)(16384 + row * 16384);

    private static int CountLines(StringBuilder value)
    {
        int count = 0;
        foreach (char character in value.ToString())
        {
            if (character == '\n')
                count++;
        }
        return count;
    }
}
