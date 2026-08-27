# Minions in 32K corresponding source

This archive contains the exact source needed to rebuild the distributed
native client:

- `game/crates/client`
- `game/crates/{common,protocol,sim}`
- embedded assets, scene data, licenses, and client build scripts
- the public protocol notes

It intentionally does not contain the arena service, deployment integration,
author reference bot, or private result pipeline. Those programs are not
linked into the distributed client and are outside this corresponding-source
contract.

Build the Linux client from `game/` with:

```sh
cargo build --locked --release -p client
```

Build the Windows client with `game/scripts/build-win.sh`. Protocol v13 and the
Banana Confirmed wire fields are documented in `game/docs/public-protocol.md`.

Program code is licensed under AGPL-3.0-only. Content and assets are licensed
under CC BY-NC-SA 4.0. See the top-level notice and license files.
