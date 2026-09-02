"""Non-mutating functional checker for Serpent Circuit's public contract."""

from http.client import HTTPConnection, HTTPException
import json
import socket
from typing import Any

from lib import KothContext, Mumble, Offline, checker, run_koth_checker


REQUEST_TIMEOUT_SECONDS = 3
MAX_RESPONSE_BYTES = 32 * 1024
# rsctf currently issues a far shorter random suffix. The maximum accepted
# syntax is therefore a deterministic non-issued probe, not a guessed token.
INVALID_PROBE_TOKEN = "koth_" + "z" * 128
HEALTH_KEYS = {
    "name",
    "status",
    "mode",
    "players",
    "protocol",
    "reporter",
    "rooms",
    "scoringReady",
    "matchSeconds",
    "turnSeconds",
}
REPORTER_KEYS = {"alive", "consecutiveFailures", "state"}
REPORTER_STATES = {"starting", "waiting", "ready", "retrying"}
RULE_KEYS = {
    "name",
    "protocolVersion",
    "boardSize",
    "startPosition",
    "finishPosition",
    "minPlayers",
    "maxPlayers",
    "matchSeconds",
    "turnSeconds",
    "scoringWaveSeconds",
    "objective",
    "transitions",
    "rules",
}
TRANSITION_KEYS = {"from", "to", "kind"}
EXPECTED_TRANSITIONS = [
    {"from": 3, "to": 24, "kind": "ladder"},
    {"from": 8, "to": 30, "kind": "ladder"},
    {"from": 17, "to": 6, "kind": "snake"},
    {"from": 19, "to": 46, "kind": "ladder"},
    {"from": 28, "to": 62, "kind": "ladder"},
    {"from": 35, "to": 15, "kind": "snake"},
    {"from": 42, "to": 71, "kind": "ladder"},
    {"from": 49, "to": 27, "kind": "snake"},
    {"from": 58, "to": 84, "kind": "ladder"},
    {"from": 64, "to": 40, "kind": "snake"},
    {"from": 73, "to": 92, "kind": "ladder"},
    {"from": 79, "to": 51, "kind": "snake"},
    {"from": 88, "to": 68, "kind": "snake"},
    {"from": 97, "to": 76, "kind": "snake"},
]
EXPECTED_RULES = [
    "Submit one current KotH capability to enter the server-controlled FIFO queue.",
    "Every match has exactly four distinct authenticated teams and starts automatically when it reaches the front of the queue.",
    "Players take turns rolling one server-generated six-sided die.",
    "Ladders climb and snakes slide immediately after a legal move.",
    "A roll must land exactly on 100; an overshoot leaves the piece in place.",
    "Rolling a six grants another turn, and an expired turn is rolled automatically.",
    "During a human-only match, each completed 60-second scoring tick records every team's furthest server-confirmed square in that tick.",
    "The first player to reach 100 wins; otherwise the sole player on the highest square wins when the five-minute match deadline expires.",
]


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _get_json(context: KothContext, path: str) -> dict[str, Any]:
    connection: HTTPConnection | None = None
    try:
        connection = HTTPConnection(
            context.target_ip,
            context.target_port,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the service did not complete the request") from error
    except HTTPException as error:
        raise Mumble("the service returned invalid HTTP") from error
    finally:
        if connection is not None:
            connection.close()

    content_type = response.getheader("Content-Type")
    if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
        raise Mumble("the service returned an unexpected HTTP response")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise Mumble("the service did not return application/json")

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Mumble("the service returned malformed JSON") from error
    if not isinstance(document, dict):
        raise Mumble("the JSON response was not an object")
    return document


def _check_capability_rejection(context: KothContext) -> None:
    body = json.dumps(
        {"name": "checker", "token": INVALID_PROBE_TOKEN},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    connection: HTTPConnection | None = None
    try:
        connection = HTTPConnection(
            context.target_ip,
            context.target_port,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        connection.request(
            "POST",
            "/api/matchmaking",
            body=body,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the managed capability probe did not complete") from error
    except HTTPException as error:
        raise Mumble("the service returned invalid HTTP") from error
    finally:
        if connection is not None:
            connection.close()

    content_type = response.getheader("Content-Type")
    if len(response_body) > MAX_RESPONSE_BYTES:
        raise Mumble("the managed capability response was too large")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise Mumble("the managed capability probe did not return application/json")
    try:
        document = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Mumble("the managed capability response was malformed") from error
    expected_error = {
        403: {"error": "invalid current KotH capability"},
        503: {"error": "the scoring roster is not ready"},
    }.get(response.status)
    if expected_error is None or document != expected_error:
        raise Mumble("the target is not ready for managed Leaderboard authentication")


def _validate_health(document: dict[str, Any]) -> None:
    if set(document) != HEALTH_KEYS:
        raise Mumble("the health response shape was incorrect")
    if document["name"] != "Serpent Circuit":
        raise Mumble("the health response named the wrong challenge")
    if document["status"] != "ok":
        raise Mumble("the service did not report a healthy status")
    if document["mode"] != "managed" or not isinstance(
        document["scoringReady"], bool
    ):
        raise Mumble("the service did not report a managed lifecycle state")
    if document["protocol"] != "serpent-circuit/5":
        raise Mumble("the health response reported the wrong protocol")
    if (
        document["turnSeconds"] != 30
        or document["matchSeconds"] != 300
        or not _is_integer(document["rooms"])
        or not 0 <= document["rooms"] <= 500
        or not _is_integer(document["players"])
        or not 0 <= document["players"] <= 2_000
    ):
        raise Mumble("the health response reported the wrong match timing")
    reporter = document["reporter"]
    if not isinstance(reporter, dict) or set(reporter) != REPORTER_KEYS:
        raise Mumble("the health response reported invalid reporter diagnostics")
    failures = reporter["consecutiveFailures"]
    if (
        reporter["alive"] is not True
        or reporter["state"] not in REPORTER_STATES
        or not _is_integer(failures)
        or not 0 <= failures <= 2
        or (reporter["state"] == "retrying") != (failures > 0)
    ):
        raise Mumble("the managed reporter liveness was inconsistent")


def _validate_transition(transition: object, origins: set[int]) -> None:
    if not isinstance(transition, dict) or set(transition) != TRANSITION_KEYS:
        raise Mumble("a board transition had the wrong shape")

    source = transition["from"]
    destination = transition["to"]
    kind = transition["kind"]
    if not _is_integer(source) or not _is_integer(destination):
        raise Mumble("a board transition used a non-integer position")
    if not 2 <= source <= 99 or not 2 <= destination <= 99:
        raise Mumble("a board transition used an invalid position")
    if source in origins:
        raise Mumble("the board defined two transitions from one position")
    origins.add(source)

    if kind == "ladder":
        if destination <= source:
            raise Mumble("a ladder did not move forward")
    elif kind == "snake":
        if destination >= source:
            raise Mumble("a snake did not move backward")
    else:
        raise Mumble("a board transition had an unknown kind")


def _validate_rules(document: dict[str, Any]) -> None:
    if set(document) != RULE_KEYS:
        raise Mumble("the rules response shape was incorrect")
    expected_scalars = {
        "name": "Serpent Circuit",
        "protocolVersion": 5,
        "boardSize": 100,
        "startPosition": 0,
        "finishPosition": 100,
        "minPlayers": 4,
        "maxPlayers": 4,
        "matchSeconds": 300,
        "turnSeconds": 30,
        "scoringWaveSeconds": 60,
        "objective": "board-progress",
    }
    for key, expected in expected_scalars.items():
        if document[key] != expected or (
            isinstance(expected, int) and not _is_integer(document[key])
        ):
            raise Mumble(f"the rules response reported an invalid {key}")

    transitions = document["transitions"]
    if not isinstance(transitions, list) or len(transitions) != 14:
        raise Mumble("the board did not define exactly 14 transitions")
    origins: set[int] = set()
    for transition in transitions:
        _validate_transition(transition, origins)
    if transitions != EXPECTED_TRANSITIONS:
        raise Mumble("the published Serpent Circuit board changed")

    rules = document["rules"]
    if rules != EXPECTED_RULES:
        raise Mumble("the published Serpent Circuit rules changed")


@checker
def check_health(context: KothContext) -> None:
    _validate_health(_get_json(context, "/healthz"))


@checker
def check_rules(context: KothContext) -> None:
    _validate_rules(_get_json(context, "/api/rules"))


@checker
def check_leaderboard_readiness(context: KothContext) -> None:
    # A syntactically valid but nonexistent token must be absent from the
    # latest rsctf context-fenced hash roster and allocate no game state.
    _check_capability_rejection(context)


if __name__ == "__main__":
    raise SystemExit(run_koth_checker())
