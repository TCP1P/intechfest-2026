#!/usr/bin/env python3
"""Join four teams to Serpent Circuit and play the assigned match."""

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


def solve(base_url, capabilities):
    """Enter matchmaking, wait for assignment, and play legal turns."""
    rules = request_json(base_url, "/api/rules")
    if rules.get("protocolVersion") != 5 or rules.get("minPlayers") != 4:
        raise ValueError("target does not expose the four-team matchmaking contract")

    tokens_by_player = {}
    for number, capability in enumerate(capabilities, 1):
        seat = request_json(
            base_url,
            "/api/matchmaking",
            {"name": f"Solver {number}", "token": capability},
        )
        tokens_by_player[seat["room"]["you"]["id"]] = seat["playerToken"]

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        states = [
            request_json(base_url, "/api/matchmaking/state", {"playerToken": token})
            for token in tokens_by_player.values()
        ]
        rosters = [{player["id"] for player in room.get("players", [])} for room in states]
        if all(room.get("status") == "playing" for room in states) and all(
            roster == set(tokens_by_player) for roster in rosters
        ):
            room = states[0]
            break
        if time.monotonic() >= deadline:
            raise ValueError("four supplied teams were not assigned to one active match in time")
        time.sleep(2)

    rolls = 0
    while room.get("status") == "playing" and rolls < MAX_ROLLS:
        player_token = tokens_by_player.get(room.get("currentPlayerId"))
        if player_token is None:
            raise ValueError("server selected an unknown current player")
        room = request_json(
            base_url,
            "/api/matchmaking/roll",
            {"actionId": secrets.token_hex(12), "playerToken": player_token},
        )
        rolls += 1
    if room.get("status") != "finished":
        raise ValueError(f"match did not finish within {MAX_ROLLS} rolls")

    winner = next(
        (player for player in room["players"] if player["id"] == room.get("winnerId")),
        None,
    )
    if winner is None:
        raise ValueError("match finished without a unique winner")
    positions = ", ".join(f"{player['name']}={player['position']}" for player in room["players"])
    return f"{winner['name']} won after {rolls} rolls; {positions}; score={room['scoreStatus']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="issued Serpent Circuit URL")
    parser.add_argument("--capability", action="append", required=True)
    args = parser.parse_args()
    if len(args.capability) != 4:
        parser.error("--capability must be supplied exactly four times")
    try:
        result = solve(args.url, args.capability)
    except (KeyError, OSError, TypeError, ValueError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
