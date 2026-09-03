#!/usr/bin/env python3
"""Join Serpent Circuit and play one automatically assigned match."""

import argparse
import json
import secrets
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 8
MAX_WAIT_SECONDS = 620
MAX_ROLLS = 1000


def request_json(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base_url.rstrip("/") + path, data=body, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(64 * 1024 + 1)
    except HTTPError as error:
        detail = error.read(4096).decode(errors="replace")
        raise ValueError(f"{path} returned HTTP {error.code}: {detail}") from error
    if len(raw) > 64 * 1024:
        raise ValueError("response is too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError(f"{path} did not return a JSON object")
    return result


def solve(base_url, capability):
    """Enter matchmaking, wait for assignment, and play legal turns."""
    rules = request_json(base_url, "/api/rules")
    if rules.get("protocolVersion") != 6 or rules.get("matchmakingSeconds") != 15:
        raise ValueError("target does not expose automatic NPC matchmaking")

    seat = request_json(
        base_url,
        "/api/matchmaking",
        {"name": "Solver", "token": capability},
    )
    player_id = seat["room"]["you"]["id"]
    player_token = seat["playerToken"]

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        room = request_json(
            base_url,
            "/api/matchmaking/state",
            {"playerToken": player_token},
        )
        if room.get("status") == "playing":
            break
        if time.monotonic() >= deadline:
            raise ValueError("the assigned match did not start in time")
        time.sleep(1)

    rolls = 0
    while room.get("status") == "playing" and rolls < MAX_ROLLS:
        if room.get("currentPlayerId") == player_id:
            room = request_json(
                base_url,
                "/api/matchmaking/roll",
                {"actionId": secrets.token_hex(12), "playerToken": player_token},
            )
            rolls += 1
        else:
            time.sleep(1)
            room = request_json(
                base_url,
                "/api/matchmaking/state",
                {"playerToken": player_token},
            )
    if room.get("status") != "finished":
        raise ValueError(f"match did not finish within {MAX_ROLLS} rolls")

    winner = next(
        (player for player in room["players"] if player["id"] == room.get("winnerId")),
        None,
    )
    positions = ", ".join(f"{player['name']}={player['position']}" for player in room["players"])
    outcome = f"{winner['name']} won" if winner else "the match ended in a draw"
    return f"{outcome}; player rolls={rolls}; {positions}; score={room['scoreStatus']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="issued Serpent Circuit URL")
    parser.add_argument("--capability", required=True)
    args = parser.parse_args()
    try:
        result = solve(args.url, args.capability)
    except (KeyError, OSError, TypeError, ValueError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
