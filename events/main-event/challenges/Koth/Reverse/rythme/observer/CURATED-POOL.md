# Pinned community beatmap pool

The organizer's 29-track list is the only song seed ledger. It is preserved in
`src/shared/song-seeds.json`. `src/shared/pool.sources.json` maps selected seed
indices to exact community BeatmapSetIDs and BeatmapIDs.

No Rythme chart generator exists in the current design. `pool.json` contains
only hit objects extracted from the selected native `.osu` difficulties.

## Current pool

| Mode | Community title | Difficulty | Stars | Beatmap |
| --- | --- | --- | ---: | ---: |
| osu! | Tada Koe Hitotsu | Regret | 6.38 | 3554008 |
| osu! | Shelter | hs | 6.16 | 1112812 |
| osu! | STYX HELIX | meow meow | 6.76 | 5126454 |
| osu! | Daisy Crown | Gentle Breeze | 5.08 | 5033291 |
| osu! | Odoriko | Spinning | 5.03 | 3896717 |
| taiko | Kawaikute Gomen (TV Size) | Sorry not sorry | 4.34 | 4689082 |
| taiko | Shelter | The Last Wishes from a Distant Night | 4.39 | 3335833 |
| taiko | STYX HELIX | Rebirth | 3.76 | 1067687 |
| taiko | Spring Thief | Spring Breeze | 3.58 | 5610786 |
| taiko | Ame o Matsu, | Viewfinder, | 3.79 | 3357775 |
| catch | Kawaikute Gomen | I'm Too Cute For This World :3 | 4.84 | 4195570 |
| catch | Shelter | Alone | 4.41 | 4328219 |
| catch | MONTAGEM HIKARI | TRILL Dopagaki | 5.79 | 5720846 |
| catch | STYX HELIX | Disappeared time | 5.03 | 2898921 |
| catch | Spring Thief | Transient Beauty of Life | 3.77 | 2795454 |
| mania | MONTAGEM XONADA | 1.4x (162bpm) OD8 | 6.78 | 5324317 |
| mania | MONTAGEM RUGADA | MMM 1.2x (156bpm) | 7.29 | 5330970 |
| mania | Kawaikute Gomen | >3< [1.4x Rate] | 6.40 | 3911688 |
| mania | Tiny Light | Memories! | 4.82 | 3452912 |
| mania | Ame o Matsu, | lv.23 1.2x (180bpm) | 5.27 | 3896469 |
| mania | With Glory I Shall Fall | The Reminiscence of Fleurdelys | 5.01 | 5066848 |

The pool has 21 difficulties and 27,135 extracted scoreable objects. Every
mode has at least four choices. Mania choices are native 4K. The hard floor is
3.50 stars; the current range is 3.58 to 7.29 stars.

Several supplied Spotify entries are covers. Where the selected community map
uses an original or a different cover, the source manifest retains the supplied
seed title and artist while `pool.json` keeps the map's real metadata.

## Selection rules

Each four-heat series contains one native osu!, taiko, catch, and 4K mania leg
in shuffled order. Each mode has its own shuffled song bag. A mode-specific map
is not repeated until that mode's bag is exhausted, and the first map of a new
bag cannot repeat the last map of the previous bag.

The full chart is played. Rythme does not crop every map to a common duration.
The coordinator derives the result deadline from the selected map and the
slowest allowed fixed playback rate.

## Rebuilding and validating

The source manifest pins the archive URL and SHA-256 before any download. Run
the importer from the challenge root:

```bash
go run ./src/client/lazer-fork/tools/poolimport/main.go \
  -sources ./src/shared/pool.sources.json \
  -cache ./src/shared/beatmap-cache \
  -out ./src/shared/pool.json
```

For each entry, the importer:

1. verifies the exact 29-track allowlist and its SHA-256;
2. checks every selected seed index, title, and artist against that allowlist;
3. verifies the archive SHA-256;
4. locates exactly one `.osu` file with the pinned BeatmapID;
5. verifies its BeatmapSetID and native mode;
6. rejects maps below 3.50 stars or with fewer than 100 scoreable objects;
7. rejects converted or non-4K mania selections;
8. extracts the actual hit object times, actions, lanes, and osu! positions.

Changing the source manifest requires rerunning the importer, the Go tests, and
the Windows release build. `pool.json` is embedded in `Rythme.exe`, so the
server and issued client must be rebuilt together.

## Archive delivery

The repository does not contain the `.osz` files. Operators have two options:

- leave the cache writable and let the coordinator retrieve, verify, and cache
  each pinned HTTPS source server-side; or
- place a rights-reviewed archive in the configured cache as either
  `<BeatmapSetID>.osz` or `<song-id>.osz`.

The signed Windows client normally contacts each pinned `catboy.best` HTTPS URL
directly. It sends no event credentials or cookies, refuses redirects and other
hosts, and verifies the same archive SHA-256 before caching bytes. The mirror can
observe each player's source IP. If the direct source is unavailable or returns
different bytes, the client automatically uses the coordinator endpoint.

The public coordinator endpoint itself never redirects. A cache miss returns a
verified archive or a local `503`. Pre-populate all 21 archives before the event
so a mirror outage does not consume the ready window. Neither path requires
Spotify credentials or an osu! OAuth token. Operators remain responsible for
beatmap, music, and event-use permissions.
