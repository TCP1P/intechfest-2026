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
    "protocol",
    "scoringReady",
    "waveSeconds",
    "turnCount",
}
RULE_KEYS = {
    "name",
    "protocolVersion",
    "boardSize",
    "startPosition",
    "finishPosition",
    "waveSeconds",
    "turnCount",
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
    "Each 60-second wave gives every team the same twelve dice pairs.",
    "Choose one die from each pair; the server applies choices in turn order.",
    "A move past 100 stays on its current square before any transport is applied.",
    "Each team receives one one-use run per wave.",
    "Route efficiency is (finalPosition - startPosition) / (optimalPosition - startPosition).",
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
        {"token": INVALID_PROBE_TOKEN}, separators=(",", ":"), sort_keys=True
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
            "/api/session",
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
    if document["protocol"] != "serpent-circuit/1":
        raise Mumble("the health response reported the wrong protocol")
    if document["waveSeconds"] != 60 or document["turnCount"] != 12:
        raise Mumble("the health response reported the wrong match timing")


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
        "protocolVersion": 1,
        "boardSize": 100,
        "startPosition": 1,
        "finishPosition": 100,
        "waveSeconds": 60,
        "turnCount": 12,
        "objective": "route-efficiency",
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
