#!/usr/bin/env python3
import os
import socket


banner = (
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


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)
port = int(os.environ.get("YANI_HOST_PORT", "31337"))
try:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        with sock.makefile("rwb", buffering=0) as stream:
            if read_exact(stream, len(banner)) != banner:
                raise SystemExit(1)
            stream.write(b"YANINEKO PING\n")
            if stream.readline(128) != b"PONG\n":
                raise SystemExit(1)
except (OSError, TimeoutError):
    raise SystemExit(1)
