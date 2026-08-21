# INTECHFEST challenge event

This event contains two flagless, server-authoritative Leaderboard KotH
challenges for RSCTF: Rythme and Minions in 32K. The checked-in player archives,
runtime source, production Compose definitions, checkers, and integrity tests are
the release source of truth.

“Private arena” describes the event’s listing and admission policy. It does not
create one arena per team and does not indicate a development-only deployment.
Each challenge runs one shared global arena, while RSCTF authenticates each team
with an event-scoped capability and records private scoring evidence. The event
can remain hidden for rehearsal and be made discoverable at launch without
rebuilding either challenge.

See [PRODUCTION.md](PRODUCTION.md) for the release contract, deployment topology,
validation gates, and rollback rules.

## Available Challenges

| Challenge Name | Category | Difficulty | Writeup Hash |
|---|---|---|---|
| Rythme | reversing | Hard | `N/A` |
| Minions in 32K | game | Hard | `N/A` |

## Usage

### Generate Challenge Templates

```bash
# Generate all challenge templates
make generate

# List available challenges
make list
```

### Running a Challenge

```bash
# Navigate to the generated challenge directory
cd challenges/challenge-slug

# Build and run the challenge
./build-docker.sh

# Stop the challenge
./build-docker.sh stop
```

### Solving a Challenge

```bash
# Navigate to the challenge directory
cd challenges/challenge-slug

# Run the solver
python3 htb/solver.py

# Or with custom host/port
python3 htb/solver.py --host target.com --port 8080
```

## Challenge Structure

Each generated challenge follows HTB's delivered-repo structure:

```
challenges/{slug}/
├── challenge.yml          # Metadata: name/author/difficulty/category/description/objective/flag
├── README.md               # The writeup: full solution walkthrough (required section order)
├── Dockerfile               # Container definition (only if the challenge runs in Docker)
├── build-docker.sh          # Single build/run/stop entrypoint
├── challenge/               # Files copied into the Docker image
│   ├── flag.txt              # Challenge flag (generated from challenge.yml)
│   └── serve_flag.sh         # Flag serving script
├── src/                     # Source used to build the challenge, if distinct from challenge/
├── htb/                     # Solver scripts
│   └── solver.py              # Python solver using pwntools
├── brainstorm/              # Gitignored author design; optional static or interactive map; never shipped
├── build-release.sh          # Builds or validates the declared player artifacts
├── release/                 # exact player files; may be empty for service-only challenges
├── playtest/                # declared player inputs only; local and gitignored
└── assets/                  # Additional challenge assets
```

## Configuration

Challenges are configured in `challenges.yml`. Each challenge entry should include:

- `name`: Display name of the challenge
- `author`: The author's HTB name
- `difficulty`: Very Easy / Easy / Medium / Hard / Insane
- `category`: The challenge's category (web, pwn, crypto, reversing, forensics, misc, ...)
- `description`: The description players see before they spawn the challenge (lore, no technical hints)
- `objective`: A technical description of the high-level objective
- `flag`: `'HTB{...}'` (a placeholder is generated if omitted)

## Development

To add a new challenge:

1. Add the challenge configuration to `challenges.yml`
2. Review the repo-local skills in `.agents/skills/` (Codex) and `.claude/skills/` (Claude Code)
3. Generate all challenge templates: `make generate`
4. Inside the generated challenge, use `htb-challenge-brainstorm` to maintain `brainstorm/README.md` and choose a static `brainstorm/architecture.svg` or interactive `brainstorm/visualizer/` when the design benefits from it
5. For a black-box challenge, use `htb-blackbox-writeup` to write the entire solution from the player's evidence timeline; before each pivotal experiment, show the exact player-visible response field and value that motivates every non-obvious input instead of merely claiming an endpoint reveals it; omit undisclosed source, hidden architecture, privileged production captures, stale image assets, and author-only defaults in linked exploit scripts
6. Use `htb-challenge-playtest` from the generated challenge to create an audited player-input room from the exact contract; a service-only black-box playtest receives the target and only neutral runner mappings for ordinary player-owned infrastructure, never release source. The directory is not a sandbox, so release-level blind evidence requires a fresh runner with an enforced workspace/network boundary
7. After a fresh player produces `PLAYTEST-REPORT.md`, use `htb-playtest-review` from the author workspace to classify guessing gaps and unintended shortcuts without leaking hints into the next attempt
8. Update this README.md with the new challenge in the table
9. Customize the generated files as needed

`brainstorm/` is spoiler-heavy local design material. It is gitignored and excluded from the Docker build context and every player artifact. A service-only challenge publishes no source handout. `htb generate`, `htb clean`, and `/regenerate` preserve existing challenge-local brainstorm directories.
The Svelte attack-map starter is bundled with the brainstorming skill and scaffolded only on demand, so challenges that do not need an interactive map stay minimal.

## AI Assistant Support

This template ships with guidance for several editors and agents:

- **Cursor**: rules under `.cursor/rules/`
- **Codex CLI**: skills under `.agents/skills/`
- **Claude Code**: `CLAUDE.md` at the repo root and skills under `.claude/skills/`
