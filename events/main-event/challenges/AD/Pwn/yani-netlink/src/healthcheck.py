#!/usr/bin/env python3
import os
import socket


banner = b"YANINEKO  /\\_/\\\\  (=^.^=)y-~~\n"
port = int(os.environ.get("YANI_HOST_PORT", "31337"))
try:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        with sock.makefile("rwb", buffering=0) as stream:
            if stream.readline(len(banner) + 1) != banner:
                raise SystemExit(1)
            stream.write(b"YANINEKO PING\n")
            if stream.readline(128) != b"PONG\n":
                raise SystemExit(1)
except (OSError, TimeoutError):
    raise SystemExit(1)
