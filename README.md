# INTECHFEST 2026 challenge repository

This private organizer repository is the rsctf Repository Bindings source for the
INTECHFEST 2026 warmup and main event. Both event roots start hidden. A repository scan
must not be treated as permission to make either event public or enable a challenge.

## Events

| Event | Root | Current challenges |
| --- | --- | ---: |
| Warmup | `events/warmup/` | 0 |
| Main event | `events/main-event/` | 2 |

The source contains no warmup challenge yet. Add one only when its player contract and
challenge package are real; do not duplicate a main-event challenge as a placeholder.

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
│   │   └── .gzevent
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
make validate RSCTF=/path/to/rsctf
make matrix RSCTF=/path/to/rsctf
make checkers
make solutions
```

`make validate` and GitHub Actions invoke rsctf directly. This repository intentionally
contains no local manifest validator or container-discovery script.

## Publishing boundary

- Never commit a live flag, KotH capability, reporter secret, admin token, TLS private key,
  undisclosed internal endpoint, or unredacted secret evidence. Reviewed public player
  endpoints may appear only in their owning release inputs.
- Treat `dist/` as player-visible and `solution/` as organizer-only.
- Keep both events hidden and every imported challenge disabled until the exact revision
  passes the release checklist, a fresh blind playtest, and hidden staging.
- Enable the Leaderboard claim source and configure rsctf's private managed-reporter origin.
  rsctf injects a lifecycle-bound secret into the target; it never belongs in Git, a
  manifest, Compose file, handout, or child gameplay process. Deploy `gateway/` only for a
  required transport adapter, never for scoring.

Production topology and immutable artifact identities are recorded in
[docs/production.md](docs/production.md).
