# Feature Flag Runbook

The server polls `feature_flags.toml` every second. Flips are applied inside the authoritative sim and written into the replay stream as `ReplayEvent::FlagFlip`, so `spectator verify` replays the same rule set.

## File

```toml
[features]
powerups = true
kitchen_lava = true
```

Use `deploy/prod.toml` as the production baseline. Edit the active file atomically where possible:

```sh
tmp="$(mktemp)"
cp /etc/minions-in-16k/feature_flags.toml "$tmp"
sed -i 's/kitchen_lava = true/kitchen_lava = false/' "$tmp"
sudo install -m 0644 "$tmp" /etc/minions-in-16k/feature_flags.toml
rm "$tmp"
```

## Flags

### `powerups`

Default: `true`

When enabled, the arena spawns quad damage, haste, and banana pickups. Turning this off prevents future pickups from being applied. Players keep effects already granted until their existing effect timers expire.

Use during the event:

- Disable if a powerup interaction creates a pacing problem or exploit path that should not dominate the rest of the match.
- Enable for escalation rounds where route optimization and item timing should matter.

Validation:

```sh
cargo test -p sim feature_flags_disable_powerups_and_lava
```

### `kitchen_lava`

Default: `true`

When enabled, the kitchen lava scenario starts 30 seconds into each 120-second cycle, remains active for 30 seconds, and deals periodic damage to players inside the kitchen.

Use during the event:

- Disable if the scenario creates a spawn-trap or makes the kitchen unusable for the current challenge.
- Enable when the round needs more movement pressure and visible mid-game volatility.

Validation:

```sh
cargo test -p sim kitchen_lava_scenario_damages_players_in_kitchen
```

## During A Match

1. Announce the flip internally with wall-clock time, flag, new value, and reason.
2. Edit the configured feature flag file.
3. Watch server logs for `feature flag flip`.
4. After the match, run replay verification:

```sh
cargo run -p spectator -- verify replays/<file>.mrpl --arena gru-lab
```

## Rollback

Set the flag back to its previous value in the same file. Do not restart the server unless the flag watcher is failing to read the file; restarts create a new replay file and disrupt active players.
