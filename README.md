# INTECHFEST 2026 challenge repository

This private organizer repository is the rsctf Repository Bindings source for the
INTECHFEST 2026 warmup and main event. Both event roots start hidden. A repository scan
must not be treated as permission to make either event public or enable a challenge.

## Events

| Event | Root | Current challenges |
| --- | --- | ---: |
| Warmup | `events/warmup/` | 3 |
| Main event | `events/main-event/` | 2 |

The warmup currently contains:

- `Parcel Panic` — self-hosted/BYOC Attack & Defense in `Web`.
- `Draft Control` — shared marker/Boot2Root King of the Hill in `Web`.
- `Serpent Circuit` — shared managed-reporting Leaderboard King of the Hill in `PPC`.

The main event currently contains:

- `Rythme` — shared managed-reporting Leaderboard KotH in `Reverse`.
- `Minions in 32K` — shared managed-reporting Leaderboard KotH in `PPC`.

## Layout

```text
.
├── .agents/                         # integrated Codex challenge workflows
├── .github/workflows/validate.yml   # official rsctf validation and dynamic builds
├── AGENTS.md                        # AI-agent entry point
├── docs/                            # human documentation and release contracts
├── events/
│   ├── warmup/
│   │   ├── .gzevent
│   │   └── challenges/<mode>/<category>/<slug>/
│   └── main-event/
│       ├── .gzevent
│       └── challenges/<mode>/<category>/<slug>/
├── Makefile
└── README.md
```

Each challenge owns one `challenge.yaml` and only the components it uses:

```text
challenge.yaml       # rsctf manifest and player-facing copy
dist/                # exact player handout
src/                 # narrow Docker build context
checker/             # read-only rsctf functional checker
gateway/             # optional stateless transport adapter; never a scoring owner
solution/            # organizer writeup and reference solver
```

Read [the documentation index](docs/README.md) and
[the multi-event authoring contract](docs/authoring-contract.md) before adding or moving
files. Anyone with Git read access can see `solution/`; keep repository membership narrow
until the event is over.

## Local checks

Use the official rsctf binary that matches the target deployment:

```sh
make check RSCTF=/path/to/rsctf
```

`make check` runs the official manifest check and dynamic matrix discovery before the
repository's checker and solution tests. GitHub Actions imports the maintained rsctf action
and invokes the same CLI directly. This repository intentionally contains no local manifest
validator or container-discovery script.

## Publishing boundary

- Never commit a live flag, KotH capability, reporter secret, admin token, TLS private key,
  undisclosed internal endpoint, or unredacted secret evidence. Reviewed public player
  endpoints may appear only in their owning release inputs.
- Treat `dist/` as player-visible and `solution/` as organizer-only.
- Keep both events hidden and every imported challenge disabled until the exact revision
  passes the release checklist, a fresh blind playtest, and hidden staging.
- Keep `Draft Control` on the Boot2Root/Marker claim source. Enable Leaderboard for
  `Serpent Circuit`, `Rythme`, and `Minions in 32K`, and configure rsctf's private
  managed-reporter origin. rsctf injects a lifecycle-bound secret into each managed target;
  it never belongs in Git, a manifest, Compose file, handout, or child gameplay process.
  Deploy `gateway/` only for a required transport adapter, never for scoring.
- Before enabling the warmup hills, set the event's KotH epoch/cycle/cooldown/confirmation
  values to `12/6/1/2`. The current Repository Bindings importer does not consume these
  settings from `.gzevent`; leaving rsctf's `12/3/1/2` defaults makes Draft Control's
  two-tick marker confirmation race the first cycle reset.

The frozen main-event topology and immutable artifact identities are recorded in
[docs/production.md](docs/production.md). The hidden warmup remains an unfrozen staging
candidate until its BYOC, Marker, and managed Leaderboard topology and exact image identities
are recorded there.
