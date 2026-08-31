#!/usr/bin/env python3
"""Read-only functional checker for the Draft Control marker hill."""

from http.client import HTTPConnection, HTTPException
import json
import socket

from lib import KothContext, Mumble, Offline, checker, run_koth_checker


REQUEST_TIMEOUT_SECONDS = 3
MAX_RESPONSE_BYTES = 4096
EXPECTED_INFO = {
    "draftRoot": "/srv/pages",
    "name": "Draft Control",
    "pages": ["brief.txt", "status.txt"],
    "preview": "GET /api/preview?path=brief.txt",
    "status": "ready",
    "update": "POST /api/drafts",
    "writePolicy": "relative path; existing regular files only",
}


def http_get(context: KothContext, path: str) -> tuple[str, bytes]:
    """Fetch one bounded player-visible route without redirects or proxy state."""
    connection = HTTPConnection(
        context.target_ip,
        context.target_port,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json, text/plain",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the draft service did not complete the request") from error
    except HTTPException as error:
        raise Mumble("the draft service returned invalid HTTP") from error
    finally:
        connection.close()

    if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
        raise Mumble("the draft service returned an unexpected response")
    content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
    return content_type, body


def get_json(context: KothContext, path: str) -> dict:
    content_type, body = http_get(context, path)
    if content_type != "application/json":
        raise Mumble("the draft API did not return JSON")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Mumble("the draft API returned invalid JSON") from error
    if not isinstance(value, dict):
        raise Mumble("the draft API response was not an object")
    return value


@checker
def check_health(context: KothContext) -> None:
    content_type, body = http_get(context, "/health")
    if content_type != "text/plain" or body != b"ok\n":
        raise Mumble("the health endpoint contract changed")


@checker
def check_info(context: KothContext) -> None:
    if get_json(context, "/api/info") != EXPECTED_INFO:
        raise Mumble("the public draft contract changed")


@checker
def check_preview(context: KothContext) -> None:
    value = get_json(context, "/api/preview?path=brief.txt")
    content = value.get("content")
    try:
        content_length = len(content.encode("utf-8")) if isinstance(content, str) else 0
    except UnicodeEncodeError as error:
        raise Mumble("the preview content was not valid UTF-8") from error
    if (
        set(value) != {"content", "path"}
        or value.get("path") != "brief.txt"
        or not isinstance(content, str)
        or not content
        or content_length > 256
    ):
        raise Mumble("the read-only preview contract changed")


def main() -> int:
    return run_koth_checker()


if __name__ == "__main__":
    raise SystemExit(main())
