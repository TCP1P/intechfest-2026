#!/usr/bin/env python3
"""Compute and submit one optimal Serpent Circuit route."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES, TIMEOUT_SECONDS = 1_000_000, 8


def request_json(base_url, path, payload=None):
    """Send one bounded JSON request and return an object response."""
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
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


def optimal_selections(rules, turns):
    """Return the best legal choice path under the public board rules."""
    if not isinstance(turns, list) or not turns:
        raise ValueError("session contains no turns")
    try:
        start = int(rules["startPosition"])
        finish = int(rules["finishPosition"])
        board_size = int(rules["boardSize"])
        transitions = {int(item["from"]): int(item["to"]) for item in rules["transitions"]}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid rules response") from error
    if not 0 <= start < finish <= board_size:
        raise ValueError("invalid board bounds")

    paths = {start: []}
    for turn, item in enumerate(turns, 1):
        if not isinstance(item, dict) or item.get("turn") != turn:
            raise ValueError("turns are missing or out of order")
        options = item.get("options")
        if not isinstance(options, list) or len(options) != 2:
            raise ValueError(f"turn {turn} does not have two options")
        next_paths = {}
        for position, selections in paths.items():
            for choice, roll in enumerate(options):
                if not isinstance(roll, int) or isinstance(roll, bool) or roll <= 0:
                    raise ValueError(f"turn {turn} has an invalid choice")
                landing = position if position + roll > finish else position + roll
                destination = transitions.get(landing, landing)
                if not 0 <= destination <= finish:
                    raise ValueError("transition leaves the board")
                selection = {"turn": turn, "choice": choice}
                next_paths.setdefault(destination, selections + [selection])
        paths = next_paths
    final_position = max(paths)
    return final_position, paths[final_position]


def solve(base_url, token):
    """Solve and submit one player-visible scoring session."""
    rules = request_json(base_url, "/api/rules")
    session = request_json(base_url, "/api/session", {"token": token})
    session_id = session.get("sessionId")
    turns = session.get("turns")
    wave_id = session.get("waveId")
    if not isinstance(session_id, str) or not session_id or not isinstance(wave_id, str):
        raise ValueError("invalid session response")
    expected, selections = optimal_selections(rules, turns)
    play = {"sessionId": session_id, "selections": selections}
    result = request_json(base_url, "/api/play", play)
    final = result.get("finalPosition")
    optimal = result.get("optimalPosition")
    efficiency = result.get("efficiency")
    valid_efficiency = (
        isinstance(efficiency, dict)
        and type(efficiency.get("earned")) is int
        and efficiency.get("earned") == efficiency.get("possible")
    )
    correct_result = result.get("accepted") is True and final == optimal == expected
    correct_wave = result.get("waveId") == session.get("waveId")
    if not correct_result or not correct_wave or not valid_efficiency:
        raise ValueError("server result does not confirm the computed optimum")
    return (
        f"wave {wave_id}: final {final}/{optimal}, "
        f"efficiency {efficiency['earned']}/{efficiency['possible']}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="issued Serpent Circuit URL")
    parser.add_argument("--token", required=True, help="team KotH capability")
    args = parser.parse_args()
    try:
        result = solve(args.url, args.token)
    except (OSError, ValueError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
