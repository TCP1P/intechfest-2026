# Yani-netlink solution

## Verification record

- Status: `draft` (hidden BYOC staging is still required)
- Type: self-hosted/BYOC `AttackDefense`, Pwn
- Kernel: Linux 7.2.3, x86-64, GCC 12 container, `make -j8 bzImage`
- Source tarball SHA-256: `8ba259e8e7b13ec6ef0941c8a39ad90b24bd4a4d6c0010ba6bafb794550ecd03`
- `bzImage` SHA-256: `670a9497f3047a40fea9806b537fd420723b91bf622ab29de227e6491fe1084e`
- `initramfs.cpio.gz` SHA-256: `a71ddd1a156c25c755a7d3d99cdff59f0575e11bff3e96bb31626268b288ff76`
- `yani_netfilter.c` snippet SHA-256: `cd008d236232aa989537634767e0e4c520cd295e06105aef61232daf8beaee14`
- `vmlinux.unstripped` SHA-256: `807c7eb7efcc7d3d420091e6ae1af469411ae257429e8d1aee7abad860823492`
- The Docker context stores this ELF as `vmlinux.unstripped.part-00`,
  `vmlinux.unstripped.part-01`, `vmlinux.unstripped.part-02`, and
  `vmlinux.unstripped.part-03`. Docker joins
  them into `/opt/yani/vmlinux.unstripped`.
- Docker image index: `sha256:b0e33011573518f54c8bf3a68e97709516c7329e5c1153f5ab439c845c909ee6`
- Docker image config: `sha256:d634bd610eadc3f3e982226ed19d5afd758e29c2a9c11188520d2206a8d9a73d`
- Player transport: TCP port `31337`. UDP accepts one request per datagram.
- The relay, netfilter hook, and `/proc/yani` are built into `bzImage`. The
  initramfs contains BusyBox and boot files. It has no challenge daemon or
  interactive serial shell. PID 1 keeps the guest alive with `sleep 5`.
- The image includes `/opt/yani/yani_netfilter.c`, an incomplete context snippet
  that shows only `EAT`, `PUKE`, and `SMOKING`. The full implementation stays in
  `dev/`. It also includes `/opt/yani/vmlinux.unstripped` for reverse engineering.
- The launcher uses `/tmp/yani` for writable runtime state and accepts `RSCTF_FLAG`
  when the platform does not mount `RSCTF_FLAG_FILE`.
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
