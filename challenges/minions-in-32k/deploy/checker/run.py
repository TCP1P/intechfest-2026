#!/usr/bin/env python3
"""Read-only functional checker for the authoritative Minions in 32K arena."""

from http.client import HTTPConnection, HTTPException
import json
import os
import socket
import time


OFFLINE = 2
MUMBLE = 1
INTERNAL_ERROR = 3
MAX_BODY = 16 * 1024
EXPECTED_BUILD = "2026.08.21.1"


def get_json(host: str, port: int, path: str, timeout: float = 4) -> dict:
    connection = HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise RuntimeError("offline") from error
    except HTTPException as error:
        raise ValueError("invalid HTTP") from error
    finally:
        connection.close()
    if (
        response.status != 200
        or len(body) > MAX_BODY
        or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ValueError("unexpected response")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("response is not an object")
    return value


def valid_health(value: dict, now_unix_ms: int | None = None) -> bool:
    if set(value) - {
        "name", "status", "protocol", "protocolVersion", "renderWidth", "renderHeight",
        "matchId", "expectedEndUnixMs", "players", "npcs", "certHash", "build",
    }:
        return False
    cert_hash = value.get("certHash")
    build = value.get("build")
    if (
        value.get("name") != "Minions in 32K"
        or value.get("status") not in {"idle", "running"}
        or value.get("protocol") != "minions/0"
        or value.get("protocolVersion") != 13
        or value.get("renderWidth") != 256
        or value.get("renderHeight") != 128
        or not isinstance(value.get("players"), int)
        or isinstance(value.get("players"), bool)
        or not 0 <= value["players"] <= 8
        or not isinstance(value.get("npcs"), int)
        or isinstance(value.get("npcs"), bool)
        or not 0 <= value["npcs"] <= 7
        or not isinstance(cert_hash, str)
        or len(cert_hash) != 64
        or any(character not in "0123456789abcdef" for character in cert_hash)
        or build != EXPECTED_BUILD
    ):
        return False
    if value["status"] == "idle":
        return (
            (value["players"], value["npcs"]) in {(0, 0), (8, 7)}
            and "matchId" not in value
            and "expectedEndUnixMs" not in value
        )
    match_id = value.get("matchId")
    expected_end = value.get("expectedEndUnixMs")
    now_unix_ms = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
    return (
        isinstance(match_id, str)
        and value["players"] == 8
        and value["npcs"] == 7
        and match_id.startswith("m32-")
        and 10 <= len(match_id) <= 128
        and all(character.isascii() and (character.isalnum() or character in "-_") for character in match_id)
        and isinstance(expected_end, int)
        and not isinstance(expected_end, bool)
        and now_unix_ms - 10_000 < expected_end <= now_unix_ms + 45 * 60 * 1000
    )


def valid_quality(value: dict, arena_status: str) -> bool:
    return (
        set(value) == {"status", "probe", "mode", "protocolVersion"}
        and value.get("status") == "ok"
        and value.get("probe") == "pinned-quic-invalid-key"
        and value.get("mode") == arena_status
        and value.get("protocolVersion") == 13
    )


def valid_context() -> bool:
    try:
        host = os.environ["RSCTF_TARGET_IP"]
        port = int(os.environ["RSCTF_TARGET_PORT"])
        return (
            os.environ.get("RSCTF_ACTION") == "check"
            and int(os.environ["RSCTF_TEAM_ID"]) == 0
            and int(os.environ["RSCTF_ROUND"]) >= 0
            and int(os.environ["RSCTF_CHALLENGE_ID"]) > 0
            and 0 < len(host) <= 253
            and not any(character.isspace() for character in host)
            and 1 <= port <= 65_535
        )
    except (KeyError, TypeError, ValueError):
        return False


def main() -> int:
    try:
        if not valid_context():
            return INTERNAL_ERROR
        host = os.environ["RSCTF_TARGET_IP"]
        port = int(os.environ["RSCTF_TARGET_PORT"])
        health = get_json(host, port, "/healthz")
        if not valid_health(health):
            return MUMBLE
        quality = get_json(host, port, "/qualityz", timeout=10)
        if valid_quality(quality, health["status"]):
            return 0
        # A match may start or finish between the two independent reads. Retry
        # only the cheap health sample; no second gameplay probe is needed.
        health = get_json(host, port, "/healthz")
        return 0 if valid_health(health) and valid_quality(quality, health["status"]) else MUMBLE
    except KeyError:
        return INTERNAL_ERROR
    except RuntimeError:
        return OFFLINE
    except (TypeError, ValueError, OSError):
        return MUMBLE


if __name__ == "__main__":
    raise SystemExit(main())
