# Yani-netlink solution

## Verification record

- Status: `draft` (hidden BYOC staging is still required)
- Type: self-hosted/BYOC `AttackDefense`, Pwn
- Kernel: Linux 7.2.3, x86-64, GCC 12 container, `make -j8 bzImage`
- Source tarball SHA-256: `8ba259e8e7b13ec6ef0941c8a39ad90b24bd4a4d6c0010ba6bafb794550ecd03`
- `bzImage` SHA-256: `0262643f67d2939e43261a7577a15fd2a0d04f191979cc851ec81d46432e9a4a`
- `initramfs.cpio.gz` SHA-256: `ff06f3f8278c424021c3407d1752e24797b16c426cd353ccd63f21c7ec5d9e89`
- `yani_netfilter.c` snippet SHA-256: `cd008d236232aa989537634767e0e4c520cd295e06105aef61232daf8beaee14`
- `vmlinux.unstripped` SHA-256: `48bc49a2b9995dea834630568412483be0af58e85b20aec4984c846abdfdf5db`
- Docker image index: `sha256:bb0700192797c342b5beae86730eb0cf68fa1dd55dcec6232682102e7980d790`
- Docker image config: `sha256:859b7b03a3bbdce492b72a2f8fa5b24c42082fb976a961f00ac02f17e83194d5`
- Player transport: TCP port `31337`. UDP accepts one request per datagram.
- The relay, netfilter hook, and `/proc/yani` are built into `bzImage`. The
  initramfs contains BusyBox and boot files only. It has no challenge daemon or
  interactive serial shell. PID 1 keeps the guest alive with `sleep 5`.
- The image includes `/opt/yani/yani_netfilter.c`, an incomplete context snippet
  that shows only `EAT`, `PUKE`, and `SMOKING`. The full implementation stays in
  `dev/`. It also includes `/opt/yani/vmlinux.unstripped` for reverse engineering.
- `CONFIG_DEFAULT_MMAP_MIN_ADDR=0` is compiled into the kernel.

## Protocol

TCP sends the banner first:

```
YANINEKO  /\_/\\  (=^.^=)y-~~
```

Each TCP command is one newline-terminated line. UDP returns the banner and one
response line in the same datagram.

```
YANINEKO PING
YANINEKO INFO
YANINEKO EAT <length>
YANINEKO PUKE <length> <hex>
YANINEKO SMOKING
YANINEKO SMOKING <address>
YANINEKO PROBE PUKE <byte>
YANINEKO PROBE SMOKING
YANINEKO CHECK <path>
YANINEKO CHECKMEM <address> <length>
YANINEKO Mrrph
```

`SMOKING` without an address calls the built-in dummy and returns its
source-defined response.

With an address, the server parses hexadecimal (`0x...`) or decimal and calls
that function with the argument stored by `PUKE`. Invalid numbers return
`SMOKING_ERR -EINVAL`. A null function address is rejected as well.

`CHECK` hashes any requested file with SHA-256. `CHECKMEM` hashes at most 4096
bytes of kernel memory. The checker also hashes `/init`, the kernel text base,
and two immutable code ranges after cross-checking the KASLR slide from `INFO`
and `EAT`. `Mrrph` requires root
credentials. The launcher refreshes
the read-only 4096-byte `/dev/vda` flag image from `RSCTF_FLAG_FILE`.

## Intended solve

1. Send `YANINEKO EAT 96`. Bytes at offsets 64, 72, and 80 disclose
   `prepare_kernel_cred`, `commit_creds`, and `init_task`.
2. Send 80 bytes with `YANINEKO PUKE`: 64 padding bytes followed by
   `prepare_kernel_cred` and `init_task` in little-endian form.
3. Send `YANINEKO SMOKING 0x<prepare>` to obtain a new kernel credential.
4. Overwrite the fields with `commit_creds` and that credential, then send
   `YANINEKO SMOKING 0x<commit>`.
5. Send `YANINEKO Mrrph` to read the current flag.

The vulnerability is the unchecked copy beyond the logical 64-byte `storage`
field into the adjacent function and argument fields. The trailing guard area
keeps the intended overwrite inside the same object. `EAT` discloses bytes past
its 64-byte stack buffer without modifying it. Calls require an IPv4 packet to
have crossed the `LOCAL_IN` netfilter hook within five seconds.

`solution/solve.py` is the reference TCP solver. The checker only performs
bounded health, protocol, CPU-policy, file-hash, memory-hash, and safe probe
checks. It never sends an overflowing `PUKE`, an attacker-chosen `SMOKING`
address, or `Mrrph`.

The final release record needs fresh image hashes, Docker health, direct TCP and
UDP checks, a solver run, checker verdict tests, and flag rotation. Hidden BYOC
staging remains a release gate.
