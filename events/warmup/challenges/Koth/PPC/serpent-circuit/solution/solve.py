#!/usr/bin/env python3
"""Create and complete one server-authoritative Serpent Circuit match."""

import argparse
import json
import secrets
from urllib.error import HTTPError
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 8


def request_json(base_url, path, payload=None):
    """Send one bounded player-visible request and return its JSON object."""
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base_url.rstrip("/") + path, data=body, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response is too large")
        result = json.loads(raw)
    except HTTPError as error:
        detail = error.read(4096).decode(errors="replace")
        raise ValueError(f"{path} returned HTTP {error.code}: {detail}") from error
    if not isinstance(result, dict):
        raise ValueError(f"{path} did not return a JSON object")
    return result


def action_id():
    return secrets.token_hex(12)


def seat_values(response):
    try:
        player_token = response["playerToken"]
        room = response["room"]
        player_id = room["you"]["id"]
        room_code = room["code"]
    except (KeyError, TypeError) as error:
        raise ValueError("server returned an invalid room seat") from error
    if not all(isinstance(value, str) and value for value in (player_token, player_id, room_code)):
        raise ValueError("server returned an invalid room seat")
    return player_token, player_id, room_code, room


def solve(base_url, capabilities, max_rolls):
    """Create a room, play every supplied seat, and return the match result."""
    rules = request_json(base_url, "/api/rules")
    if rules.get("protocolVersion") != 3 or rules.get("minPlayers") != 2:
        raise ValueError("target does not expose the multiplayer contract")

    host = request_json(
        base_url,
        "/api/rooms",
        {"name": "Solver 1", "token": capabilities[0]},
    )
    host_token, host_id, room_code, room = seat_values(host)
    tokens_by_player = {host_id: host_token}
    for number, capability in enumerate(capabilities[1:], 2):
        seat = request_json(
            base_url,
            "/api/rooms/join",
            {"name": f"Solver {number}", "roomCode": room_code, "token": capability},
        )
        player_token, player_id, joined_code, room = seat_values(seat)
        if joined_code != room_code:
            raise ValueError("joined a different room")
        tokens_by_player[player_id] = player_token

    room = request_json(
        base_url,
        "/api/rooms/start",
        {"actionId": action_id(), "playerToken": host_token, "roomCode": room_code},
    )
    rolls = 0
    while room.get("status") == "playing" and rolls < max_rolls:
        current_id = room.get("currentPlayerId")
        if current_id not in tokens_by_player:
            raise ValueError("server selected an unknown current player")
        room = request_json(
            base_url,
            "/api/rooms/roll",
            {
                "actionId": action_id(),
                "playerToken": tokens_by_player[current_id],
                "roomCode": room_code,
            },
        )
        rolls += 1
    if room.get("status") != "finished":
        raise ValueError(f"match did not finish within {max_rolls} rolls")

    players = room.get("players")
    if not isinstance(players, list) or len(players) != len(capabilities):
        raise ValueError("server returned an invalid final roster")
    winner = next(
        (player for player in players if player.get("id") == room.get("winnerId")),
        None,
    )
    if winner is None or winner.get("position") != 100:
        raise ValueError("server did not confirm an exact-finish winner")
    positions = ", ".join(f"{player['name']}={player['position']}" for player in players)
    return (
        f"room {room_code}: {winner['name']} won after {rolls} rolls; "
        f"{positions}; score={room.get('scoreStatus')}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="issued Serpent Circuit URL")
    parser.add_argument(
        "--capability",
        action="append",
        required=True,
        help="distinct KotH capability; supply two to four times",
    )
    parser.add_argument("--max-rolls", type=int, default=1000)
    args = parser.parse_args()
    if not 2 <= len(args.capability) <= 4:
        parser.error("--capability must be supplied two to four times")
    if not 1 <= args.max_rolls <= 1000:
        parser.error("--max-rolls must be in 1..1000")
    try:
        result = solve(args.url, args.capability, args.max_rolls)
    except (OSError, ValueError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
