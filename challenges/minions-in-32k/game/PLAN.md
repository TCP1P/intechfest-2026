# PLAN.md

Living roadmap. Update as decisions settle; don't let it rot.

## Decisions

- **Language:** Rust end-to-end. Client uses Bevy.
- **CTF framing:** player-driven, semi-automated combat. Humans discover kill confirmation in the HUD, then reverse-engineer protocol v13 to build tactical assistance for collision-aware confirm/deny decisions. Tools may advise and assist, but sustained unattended combat is ineligible for official KotH evidence. The server assumes every client is hostile.
- **Assets:** the Gru's-lab arena and minion character live as `.glb` under `assets/`. Source files (Blender scenes, scripts, whatever each piece needs) live under `tools/`. The client only ever loads the `.glb`.
- **Transport (tentative):** QUIC via `quinn` for gameplay. WebSocket for spectator fan-out. Confirm once we have a first movement prototype.
- **Determinism:** fixed-point game math; replays record inputs + seeds + flag flips and reproduce bit-exactly.
- **Internal render resolution:** 256 × 128 = 32 768 pixels by default, upscaled to fill the window. The client still accepts a sibling `config.toml`, but render-resolution changes are cosmetic and cannot authorize damage.
- **Tick rate:** 64 Hz. Server flag is kept for experimentation but 64 is the shipped value.
- **Team structure:** 8-player FFA. Confirmed kills rank the lobby; frags and fewer deaths break ties.
- **Anti-cheat posture:** server is authoritative. Per-recipient snapshots filter occluded players and neutralize opponent combat fields; active probe logic remains defense-in-depth. The scoring boundary is the server-owned confirmation event plus private participation-integrity evidence, not client rendering or asset integrity. Integrity classifications never become a player-visible scoreboard.
- **Banana Confirmed:** an opponent frag drops a 12-second objective at the victim's position. The killer can confirm, the victim can deny, and third parties cannot resolve it. Active objectives are bounded at 64.
- **Spectator delay:** 30 s. The web spectator is a constant-delay view of the replay stream, not a live tap; this kills the "watch the stream to scout" side channel during a match.
- **Client distribution:** macOS, Linux, Windows binaries via the rCTF platform. Assets bundled; `config.toml` is not.

## Open questions

_None blocking. Add back here if one comes up._

## Phases

### Phase 0 — scaffold (this session)
- [x] Repo layout, `CLAUDE.md`, `PLAN.md`, `.gitignore`, `README.md`.
- [x] Cargo workspace with empty crates: `protocol`, `server`, `client`, `replay`, `spectator`, `common`.
- [x] Initial git commit.

### Phase 1 — walking skeleton
- [x] Minimal protocol (`Join`, `Input`, `Snapshot`, `Leave`, `Welcome`, `Reject`) with length-prefixed bincode framing and a `MAX_MSG_BYTES` guard.
- [x] 16.16 fixed-point scalars + `Vec3` in `protocol`; `FpExt`/`Vec3Ext` math helpers in `common`; seeded ChaCha12 RNG wrapper.
- [x] Server: QUIC endpoint (quinn + rustls, self-signed dev cert), per-connection task handles Join → Welcome → input/snapshot stream, game loop runs at configurable tick rate and applies WASD-style movement.
- [x] Replay writer records header + every event (joins, leaves, inputs, snapshots) with periodic flush; reader streams events back.
- [x] `spectator verify <file>` CLI — round-trip sanity check for the replay format.
- [x] Bevy client: render-to-texture at exactly 128×128 = 16 384 px, nearest-neighbor upscale to the window, keyboard → `Buttons` → server, per-player cubes spawned/despawned from snapshots.
- [ ] Deterministic playback that reproduces server state bit-exactly. Needs the game-advance function factored out of the binary into a `server` library crate; deferred into phase 2 alongside real Quake-feel movement.

### Phase 2 — game feel
- [x] `sim` crate factored out of the server — pure simulation usable by both the server and the replay verifier.
- [x] Yaw-relative Quake-ish movement: ground friction + ground accel, air accel clamped to a low wish-speed so strafe-jumping is the way to carry speed. Gravity, jump, AABB collision against arena walls with sliding on the XZ axes and landing detection on wall tops.
- [x] Hitscan rail: edge-triggered FIRE, 1 s cooldown, 60-damage, 80 m range, line-of-sight blocked by walls. Emits `GameEvent::{Fire, Damage, Kill}`.
- [x] Health, respawn (2 s), `respawn_in` on the wire so clients can draw death UI.
- [x] Per-tick events bundled into `Snapshot` so the replay writer is still the one source of truth.
- [x] `spectator verify --sim` re-simulates a replay and diffs each tick against the recorded snapshot — 0 divergences across a 97-tick idle recording.
- [x] Top-down / FPS camera toggle (Tab). FPS hides the local avatar; top-down shows everyone.

### Phase 3 — arena & character
- [x] Five Gru's-lab zones as `.glb` under `assets/zones/`, authored world-space so they align with `sim::Arena::gru_lab` AABBs. Source pipeline under `tools/pyasset/` + `tools/zones/`.
- [x] Rigged minion variants with gun under `assets/minion/minion.glb`, 9 joints + 8 animation clips per variant (idle, run, strafe, jump, fire, hit, die, crouch). Source under `tools/minion/`.
- [x] Client loads `.glb` via `bevy_gltf` and `SceneBundle`; zones spawn at identity, minions spawn per snapshot.
- [x] Hook the minion `AnimationPlayer` up to game state: per-snapshot movement → Run/Idle, `GameEvent::Damage` → Hit (0.3 s), `GameEvent::Fire` → Fire (0.35 s), `respawn_in > 0` → Die. Each variant loads the same 8 clips into its own `AnimationGraph`; `AnimationTransitions` cross-fades clips over 120 ms.

### Phase 4 — client modes: `--demo` and `--remote`
- [x] `--remote <host:port>`: QUIC into a real lobby server (renamed from `--server`).
- [x] `--demo`: in-process `sim::World` + AI bots; no network. Default when neither flag is passed. `DemoPlugin` feeds `ServerMessage::Snapshot` into the same crossbeam channel the QUIC reader uses, so the render/input path is mode-agnostic.
- [x] Bot AI: seek-the-player behaviour — faces the local player, walks forward at range, fires rail inside 22 m, flips a 0.75 s strafe direction. Three bots on gru-lab by default.
- [x] Showcase sub-arena exposing every minion animation on a podium row + zone-prop gallery + firing-range dummy.

### Phase 5 — gameplay extensibility
- [x] Powerup trait + registry. Ship three: quad damage, haste, banana (short-range slip hazard).
- [x] Mid-game scenario system (timed events: "the floor is lava in the kitchen for 30 s").
- [x] Feature-flag store with live reload + replay logging.

### Phase 6 — security hardening
- [x] seccomp-bpf profile for the server process; fs writes only under `./replays/`.
- [x] User namespace / rootless container in `deploy/`.
- [x] Fuzzing of protocol decode (`cargo-fuzz`) as part of CI.
- [x] Resource limits: max packet size, max state size per player, RTT ceiling.

### Phase 7 — spectator polish
- [x] Web frontend: top-down + FPS camera toggle, team/player panel with health bars, kill feed, round timer.
- [x] Offline replay scrubbing.

### Phase 8 — CTF prep
- [x] Feature-flag runbook: which flips we'll ship mid-event and what they break.
- [x] Telemetry dashboard (grafana-ish or just structured logs) to watch the event live.
- [x] Published client binary + protocol doc intentionally omits the sandbox details.

## Protocol log

Append a one-line entry here whenever a protocol message is added or changed, so client/server/replay stay in sync.

- v0 (phase 1): initial wire format. `Fp` (16.16), `Vec3`, `Buttons` bitfield, `Input { tick, buttons, yaw, pitch }`, `PlayerState { id, pos, yaw, health }`, `Snapshot { tick, players }`, `ClientMessage::{Join, Input, Leave}`, `ServerMessage::{Welcome, Reject, PlayerJoined, PlayerLeft, Snapshot}`. Length-prefixed (u32 LE) bincode. `PlayerJoined`/`PlayerLeft` defined but not yet emitted by the server — clients derive roster from `Snapshot`.
- v1 (phase 2): `PlayerState` gains `pitch` and `respawn_in`. `Snapshot` gains `events: Vec<GameEvent>`. New `GameEvent::{Fire, Damage, Kill, Spawn}`. Breaking change; server and client must match version.
- v2 (phase 3): `Buttons::CROUCH` and `PlayerState::crouching`. Breaking change; server and client must match version.
- v3 (phase 5): `PowerupKind::{QuadDamage, Haste, Banana}` and `GameEvent::PowerupPickup { id, kind }`. Breaking change; server and client must match version.
- v4 (phase 5): `ScenarioKind::KitchenLava` and `GameEvent::{ScenarioState, ScenarioDamage, ScenarioKill}`. Breaking change; server and client must match version.
- v5 (matchmaking): `ClientMessage::Join` gains `key: Option<String>` — per-match access token for instance mode. The current server uses first-connect-wins for a live duplicate key and retains a disconnected slot for reconnect. Breaking change; server and client must match version.
- v6 (ui pass): `ClientMessage::Join` gains `variant: u8` (minion selection); new `ServerMessage::Roster(Vec<RosterEntry { id, name, variant, rtt_ms, kills, deaths, connected }>)` broadcast every second and on roster changes — feeds the Tab scoreboard. Breaking change; server and client must match version.
- v7 (game over + hud + weapons): new `ServerMessage::GameOver { standings }` (the only trigger for the client end-of-match flow); `PlayerState` gains `quad_ticks`/`haste_ticks`/`weapon`/`ammo`; `PowerupKind::{Health, Flamethrower}`; `WeaponKind`; `GameEvent::Flame` (VFX). Breaking change; server and client must match version.
- v8 (arsenal): `WeaponKind::{Shotgun, RocketLauncher}` + matching `PowerupKind`s; `Snapshot` gains `projectiles: Vec<ProjectileState { id, pos, vel }>` (authoritative rockets, client renders only); `GameEvent::Explosion { at }` (VFX). Breaking change; server and client must match version.
- v9 (items): `PowerupKind::{Armor, MegaHealth, Serum}`; `PlayerState` gains `armor`/`serum_ticks`. Q3-referenced balance: shotgun 10x10, rocket 100, armor absorbs 2/3, mega rots to 100. Breaking change; server and client must match version.
- v10 (presentation): `Snapshot` gains `powerups_up: Vec<bool>` (pickup availability, arena order) so clients can hide taken items until respawn. Serum berserk now only replaces the RAIL's fire — pickup weapons keep firing. Breaking change; server and client must match version.
- v11 (transport, international latency): new `ServerMessage::Events { tick, events }` on the reliable stream; `Snapshot` (its `events` emptied) now rides UNRELIABLE QUIC/WebTransport datagrams — newest wins, clients drop stale ticks, so packet loss can't head-of-line-block world state. Falls back to the stream when datagrams are unsupported or the snapshot outgrows the MTU. Replays still record full snapshots (events inline). Breaking change; server and client must match version.
- v12 (Minions in 32K): `PlayerState` gains `relay_stage` and `relay_armed_ticks`; new `MoonRelayPad` and `GameEvent::{RelayProgress, RelayArmed, RelayDenied}`. PvP damage is rejected unless the attacker completed the ordered server-side circuit. Protocol v11 joins are rejected. Breaking change; server and client must match version.
- v13 (Banana Confirmed): removes the v12 relay fields and damage gate; `Snapshot` gains bounded `ConfirmationState` records; `RosterEntry` gains `confirmed_kills`; new `GameEvent::{ConfirmationDropped, KillConfirmed, KillDenied, ConfirmationExpired}`. The desktop results screen remains open and can return to the secure lobby. Protocol v12 joins are rejected. Breaking change; server and client must match version.
- v13 report-only integrity update: ordered instance reports add private `integrity_status` evidence. This does not alter the public protocol revision, ALPN, bincode layout, or player-visible messages.

## Asset spec

- glTF 2.0, Y-up, 1 unit = 1 meter.
- Zone scenes: `assets/zones/{name}.glb` (visual) + `assets/zones/{name}.collision.glb` (simplified).
- Minion: `assets/minion/minion.glb`, rig + baked animations listed in `assets/minion/animations.json`.
- Source files live under `tools/`. Rebuild steps are documented next to each piece.
