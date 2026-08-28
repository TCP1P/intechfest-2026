# Public Protocol Notes

This is the intentionally publishable client-facing protocol note. It describes only what competitors can learn from the distributed client and network traffic. It does not document server deployment, sandboxing, replay storage paths, seccomp policy, or host hardening.

## Transport

- QUIC over UDP.
- One bidirectional stream per client connection.
- The first client message must be `ClientMessage::Join`.
- All messages are length-prefixed with a little-endian `u32`, followed by a bincode payload.
- Maximum payload size is `64 KiB`.

## Version

Current protocol version: `13`

The client sends its protocol version in `Join`. The server rejects mismatched clients.
The version bump is intentional: protocol-v12 Moon Relay clients cannot join this arena.

## Client To Server

```rust
enum ClientMessage {
    Join {
        protocol_version: u16,
        name: String,
        key: Option<String>,
        variant: u8,
    },
    Input(Input),
    Leave,
}
```

`name` must be non-empty and at most 24 bytes.

- `key` is a per-match access key. It is required when the server runs in instance mode (matchmade lobbies) and ignored otherwise.
- `variant` selects the minion variant from the selection screen (`0..=3`).

```rust
struct Input {
    tick: u64,
    buttons: Buttons,
    yaw: Fp,
    pitch: Fp,
}
```

`yaw` is wrapped into `[-π, π)` by the server.

`Buttons` is a `u16` bitfield:

- `1 << 0`: forward
- `1 << 1`: back
- `1 << 2`: left
- `1 << 3`: right
- `1 << 4`: jump
- `1 << 5`: fire
- `1 << 6`: crouch

`Fp` is a signed 16.16 fixed-point integer.

## Server To Client

```rust
enum ServerMessage {
    Roster(Vec<RosterEntry>),
    GameOver { standings: Vec<RosterEntry> },
    Welcome { your_id: u8, tick_rate_hz: u16 },
    Reject { reason: String },
    PlayerJoined { id: u8, name: String },
    PlayerLeft { id: u8 },
    Snapshot(Snapshot),
    Events { tick: u64, events: Vec<GameEvent> },
}
```

- `Roster` is the live scoreboard, broadcast every second and on roster changes. Names, picked minions, latency, and score are server-authoritative.
- `GameOver` is the only trigger for the client's game-over flow; connection loss is not a result.
- `Snapshot` is the world-state sync. Over the network it rides UNRELIABLE datagrams (newest wins; clients drop stale ticks).
- `Events` carries a tick's events on the RELIABLE stream so kills, damage, and explosions are never lost to a dropped datagram.

```rust
struct RosterEntry {
    id: u8,
    name: String,
    variant: u8,
    rtt_ms: u16,
    kills: u16,
    confirmed_kills: u16,
    deaths: u16,
    connected: bool,
}
```

Most client state arrives through `Snapshot`:

```rust
struct Snapshot {
    tick: u64,
    players: Vec<PlayerState>,
    projectiles: Vec<ProjectileState>,
    confirmations: Vec<ConfirmationState>,
    powerups_up: Vec<bool>,
    events: Vec<GameEvent>,
}
```

- `projectiles` are live rockets with authoritative positions; clients render and interpolate, never simulate.
- `confirmations` are unresolved bananas dropped by PvP kills. Their positions, roles, and deadlines are authoritative.
- `powerups_up` is one availability flag per arena powerup (same order as the arena's powerup list).

```rust
struct ProjectileState {
    id: u16,
    pos: Vec3,
    vel: Vec3,
}

struct ConfirmationState {
    id: u16,
    killer: u8,
    victim: u8,
    pos: Vec3,
    expires_in: u16,
}

struct PlayerState {
    id: u8,
    pos: Vec3,
    yaw: Fp,
    pitch: Fp,
    health: u8,
    respawn_in: u16,
    crouching: bool,
    quad_ticks: u16,
    haste_ticks: u16,
    weapon: WeaponKind,
    ammo: u16,
    armor: u8,
    serum_ticks: u16,
}
```

- `respawn_in` is `0` while alive and interactive; non-zero while dead and awaiting respawn.
- `quad_ticks`, `haste_ticks`, and `serum_ticks` are remaining powerup ticks (`0` = inactive).
- `ammo` is remaining ammo for limited weapons; the rail is infinite and reads `0`.
- Exact combat fields are only disclosed for the local player. The server zeroes those fields on enemy views.
- Confirmation records are public arena objectives, but only the named killer or victim can resolve one.

```rust
enum WeaponKind { Rail, Flamethrower, Shotgun, RocketLauncher }
```

`Vec3` is three `Fp` components (`x`, `y`, `z`).

`GameEvent` carries the per-tick timeline:

```rust
enum GameEvent {
    Fire { shooter: u8, from: Vec3, to: Vec3, hit: Option<u8> },
    Damage { attacker: u8, victim: u8, amount: u8 },
    Kill { attacker: u8, victim: u8 },
    Spawn { id: u8, pos: Vec3 },
    PowerupPickup { id: u8, kind: PowerupKind },
    Flame { shooter: u8, from: Vec3, dir: Vec3 },
    Explosion { at: Vec3 },
    ScenarioState { kind: ScenarioKind, active: bool },
    ScenarioDamage { id: u8, kind: ScenarioKind, amount: u8 },
    ScenarioKill { id: u8, kind: ScenarioKind },
    ConfirmationDropped { confirmation_id: u16, killer: u8, victim: u8 },
    KillConfirmed { confirmation_id: u16, killer: u8, victim: u8 },
    KillDenied { confirmation_id: u16, killer: u8, victim: u8 },
    ConfirmationExpired { confirmation_id: u16, killer: u8, victim: u8 },
}
```

```rust
enum PowerupKind {
    QuadDamage,
    Haste,
    Banana,
    Health,
    Flamethrower,
    Shotgun,
    RocketLauncher,
    Armor,
    MegaHealth,
    Serum,
}

enum ScenarioKind { KitchenLava }
```

## Banana Confirmed

Opponent damage always applies through the authoritative simulation. When an
opponent frag occurs, the server drops one confirmation banana at the victim's
death position. It remains live for 12 seconds:

- the named killer may collect it to emit `KillConfirmed`;
- the named victim may recover it after respawning to emit `KillDenied`;
- every other player is ignored by that banana;
- if neither eligible player reaches it, the server emits `ConfirmationExpired`.

The horizontal pickup radius is 1.25 m. The arena holds at most 64 unresolved
confirmations; the bound is part of the denial-of-service and replay-integrity
contract. The client renders yellow objectives for its confirmations, red
objectives for its denials, and neutral objectives for everyone else.

Confirmed kills are the primary live and final ranking field. Total frags and
then fewer deaths break confirmed-score ties. Clients never submit any of these
counters: the server derives them from simulation events and signs the match
report consumed by the managed arena supervisor.

## Automation boundary

Protocol analysis and player-driven assistance are intended. A tool may read
public state, improve presentation, recommend targets or routes, and assist an
action that a player is actively taking. A player must remain in the loop and
drive the match; a client that sustains combat without player participation is
not eligible for official KotH activity, objective, or Crown evidence.

This eligibility decision is private child-to-supervisor evidence. It does not
add a public protocol message, change protocol revision 13, or expose a second
player scoreboard. The bundled headless client is regression and exhibition
infrastructure and is intentionally not scoring eligible.

## Client Config

The shipped client does not include `config.toml`. If a sibling `config.toml` exists, the client reads local render settings from it. A completed match leaves the results window open; returning to the secure lobby reuses the KotH capability kept in zeroized process memory and performs a fresh HTTPS exchange.
