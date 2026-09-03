Tower of Babel — player handout
================================

This is a shared King-of-the-Hill arena. Connect to the issued TCP endpoint
and send one line at a time. The current rsctf KotH capability is required for
ranked play; never paste it into a public log.

  HELLO koth_<current-capability>
  INFO
  RELAY <lowercase-even-hex-of-the-outermost-stage>
  AUTH <the four printed stage secrets concatenated in build-order>
  PATCH {"patterns":[{"arch":"amd64","hex":"4889"}]}
  QUIT

`INFO` reveals the current wave, target `instanceId`, build order, architecture,
secret path, executor path, active ward, and ward status for each stage. The
stage paths are the only strings that the shellcode should embed. The four
Linux syscall ABIs are:

  amd64       openat=257 read=0 write=1 close=3 execve=59
  aarch64     openat=56  read=63 write=64 close=57 execve=221
  riscv64     openat=56  read=63 write=64 close=57 execve=221
  mips64be    open=5002  read=5000 write=5001 close=5003 execve=5057

The outer payload is limited to 32768 bytes. A relay stage should open its
own secret, read it, write it to stdout, and then `execve` the next executor
with the next stage encoded as a NUL-terminated hexadecimal argument. The
last stage exits. Use position-independent addresses; the service runs each
guest in a bounded Unicorn VM and does not execute a player's bytes natively.

The active ward is listed by `INFO`. Only the first team to complete and
authenticate a four-stage relay in a wave may submit a patch, at most three
times. A patch may contain at most eight patterns, each 2–8 bytes, with no
mandatory syscall opcode, and no more than 32 pattern bytes total. The target
replays the first solver's known-good relay with the candidate before accepting
it. `PATCH_REJECTED` means the previous known-good ward stayed active; the
fallback and its revision are visible in `INFO`. `PATCH_ACCEPTED` takes effect
immediately for later attempts in the current wave and carries forward. If the
target restarts, `instanceId` changes: reconnect, fetch `INFO` again, and do not
assume the old ward or session survived.

Tooling is player-owned and is not present on the target. A Debian/Ubuntu
workstation can use Python 3 with `pwntools==4.15.0` plus
`binutils-aarch64-linux-gnu`, `binutils-riscv64-linux-gnu`, and
`binutils-mips64-linux-gnuabi64`. The RISC-V stage must be fully linked so its
PC-relative relocations are resolved before it is embedded in the outer stage.

Unauthenticated `PING` returns `PONG`; `INFO` is read-only. The checker never
authenticates a team, submits a relay, reads secrets, or touches scoring state.
