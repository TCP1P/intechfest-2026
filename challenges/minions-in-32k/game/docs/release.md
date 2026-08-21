# Release Checklist

The player release contains the client binary and runtime assets only.

## Build

Linux/macOS host build:

```sh
cargo build --release -p client
```

Windows cross-compile from Linux/WSL:

```sh
scripts/build-win.sh
```

## Package Contents

Include:

- `client` or `client.exe`
- `assets/`
- `docs/public-protocol.md` if the event wants a protocol handout

Exclude:

- `config.toml`
- `deploy/`
- `tools/`
- `replays/`
- `target/`
- server, spectator, and replay verifier binaries
- private runbooks or operator notes

## Validation

Run before publishing:

```sh
cargo fmt --check
cargo check -p client -p server -p spectator
cargo test -p protocol -p sim
cargo run -p sim --example verify_scenes
```

Manual smoke:

```sh
cargo run -p client -- --demo
```

Verify the first screen is the playable demo, the minion variants load, and no local `config.toml` is shipped next to the binary.

## Spectator

Offline replay viewer:

```sh
cargo run -p spectator -- serve replays/<file>.mrpl --bind 127.0.0.1:8080
```

Replay determinism:

```sh
cargo run -p spectator -- verify replays/<file>.mrpl --arena gru-lab
```

## Operator Telemetry

Run the server through the lightweight dashboard during test matches:

```sh
RUST_LOG=info cargo run -p server 2>&1 | scripts/telemetry-dashboard.py
```
