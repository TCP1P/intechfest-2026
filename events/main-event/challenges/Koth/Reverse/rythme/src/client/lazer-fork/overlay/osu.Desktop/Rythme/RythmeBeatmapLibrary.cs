using System.Diagnostics;
using osu.Framework.Logging;
using osu.Game.Beatmaps;
using osu.Game.Database;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    /// <summary>
    /// Imports the verified event pool before the lobby can enter a heat. This
    /// keeps package parsing and database work outside the synchronized reveal
    /// window, while still checking the exact difficulty on every launch.
    /// </summary>
    internal sealed class RythmeBeatmapLibrary
    {
        private readonly BeatmapManager beatmaps;

        public RythmeBeatmapLibrary(BeatmapManager beatmaps)
        {
            this.beatmaps = beatmaps;
        }

        public async Task<WorkingBeatmap> PrepareAsync(
            byte[] archive,
            Song song,
            CancellationToken cancellationToken,
            string? selectedBeatmapMd5 = null)
        {
            ArgumentNullException.ThrowIfNull(archive);
            ArgumentNullException.ThrowIfNull(song);

            selectedBeatmapMd5 ??= BeatmapArchiveChart.SelectedBeatmapMd5(
                archive,
                song.BeatmapId,
                song.BeatmapsetId,
                song.Ruleset);
            int beatmapId = checked((int)song.BeatmapId);
            int expectedMode = ModeFor(song.Ruleset);
            BeatmapInfo? existing = beatmaps.QueryOnlineBeatmapId(beatmapId);
            if (existing != null &&
                string.Equals(existing.MD5Hash, selectedBeatmapMd5, StringComparison.OrdinalIgnoreCase))
            {
                Logger.Log($"Rythme reused exact imported beatmap {beatmapId} ({selectedBeatmapMd5}).");
                return workingBeatmap(existing, beatmapId, expectedMode, selectedBeatmapMd5);
            }

            if (existing != null)
            {
                Logger.Log(
                    $"Rythme rejected stale imported beatmap {beatmapId}: local {existing.MD5Hash}, expected {selectedBeatmapMd5}.",
                    LoggingTarget.Database);
            }

            var elapsed = Stopwatch.StartNew();
            using var stream = new MemoryStream(archive, writable: false);
            Live<BeatmapSetInfo>? imported = await beatmaps
                                                   .Import(new ImportTask(stream, $"{song.Id}.osz"), cancellationToken: cancellationToken)
                                                   .ConfigureAwait(false);
            if (imported == null)
                throw new InvalidOperationException("lazer rejected the arena beatmap package.");

            WorkingBeatmap result = imported.PerformRead(set =>
            {
                BeatmapInfo info = set.Beatmaps.Single(beatmap => beatmap.OnlineID == beatmapId);
                return workingBeatmap(info, beatmapId, expectedMode, selectedBeatmapMd5);
            });
            Logger.Log($"Rythme imported beatmap {beatmapId} in {elapsed.ElapsedMilliseconds} ms.");
            return result;
        }

        private WorkingBeatmap workingBeatmap(
            BeatmapInfo info,
            int beatmapId,
            int expectedMode,
            string selectedBeatmapMd5)
        {
            if (info.OnlineID != beatmapId ||
                info.Ruleset.OnlineID != expectedMode ||
                !string.Equals(info.MD5Hash, selectedBeatmapMd5, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "The pinned beatmap difficulty has the wrong chart identity or native ruleset.");
            }

            return beatmaps.GetWorkingBeatmap(info);
        }

        internal static int ModeFor(string ruleset) => ruleset switch
        {
            "osu" => 0,
            "taiko" => 1,
            "fruits" => 2,
            "mania" => 3,
            _ => throw new InvalidOperationException($"Unsupported ruleset {ruleset}."),
        };
    }
}
