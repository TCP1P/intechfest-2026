#!/usr/bin/env python3
"""Read-only functional checker for the private Rythme Leaderboard hill."""

from http.client import HTTPConnection, HTTPException
import json
import re
import socket
import time

from lib import KothContext, Mumble, Offline, checker, run_koth_checker

MAX_BODY = 256 * 1024
RULESETS = {"osu", "taiko", "fruits", "mania"}
PHASES = ("enrollment", "reveal", "running", "submission", "results")
HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
EXPECTED_BUILD = "2026.08.21.1"


def get_json(host: str, port: int, path: str) -> dict:
    connection = HTTPConnection(host, port, timeout=4)
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the coordinator did not complete the request") from error
    except HTTPException as error:
        raise Mumble("the coordinator returned invalid HTTP") from error
    finally:
        connection.close()
    if (
        response.status != 200
        or len(body) > MAX_BODY
        or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise Mumble("the coordinator returned an unexpected response")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Mumble("the coordinator returned invalid JSON") from error
    if not isinstance(value, dict):
        raise Mumble("the coordinator response was not an object")
    return value


def probe_rejected_capability(host: str, port: int) -> bool:
    """Exercise the configured onboarding route without creating a session."""
    body = json.dumps({
        "team_token": "invalid-local-quality-probe",
        "client_public_key": "",
        "client_nonce": "",
    }).encode()
    connection = HTTPConnection(host, port, timeout=6)
    try:
        connection.request(
            "POST",
            "/api/v2/session/rctf",
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        response_body = response.read(MAX_BODY + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the coordinator did not complete the onboarding probe") from error
    except HTTPException as error:
        raise Mumble("the coordinator returned invalid onboarding HTTP") from error
    finally:
        connection.close()
    if (
        response.status != 401
        or len(response_body) > MAX_BODY
        or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return False
    try:
        problem = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return problem == {
        "error": "Unauthorized",
        "detail": "RSCTF KotH token was rejected",
        "status": 401,
    }


def integer(value, minimum=0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def text(value, maximum=256) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum


def valid_config(value: dict) -> bool:
    expected = {
        "arena_mods", "client_build", "local_auth", "pool_schema", "pool_song_count",
        "pool_source", "pool_verified_at", "protocol", "rctf_auth", "rulesets",
        "server_build", "transport",
    }
    rulesets = value.get("rulesets")
    return (
        set(value) == expected
        and value.get("server_build") == EXPECTED_BUILD
        and value.get("protocol") == "rythme-secure-channel-v3"
        and value.get("pool_schema") == "rythme-pool-v2"
        and value.get("pool_song_count") == 21
        and value.get("local_auth") is False
        and value.get("rctf_auth") is True
        and isinstance(rulesets, list)
        and len(rulesets) == len(RULESETS)
        and set(rulesets) == RULESETS
        and all(text(value.get(field)) for field in (
            "arena_mods", "client_build", "pool_source", "pool_verified_at", "transport"
        ))
    )


def valid_mod_profile(value) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"acronym", "rate_milli", "settings_hash", "score_multiplier_milli"}
        and text(value.get("acronym"), 32)
        and integer(value.get("rate_milli"), 1)
        and integer(value.get("score_multiplier_milli"), 1)
        and bool(HEX_64.fullmatch(value.get("settings_hash", "")))
    )


def valid_selection(value, series_index: int, series_leg: int) -> bool:
    required = {
        "song_id", "song_title", "song_artist", "ruleset", "chart_digest", "control_profile",
        "bpm", "duration_ms", "lead_in_ms", "note_count", "beatmapset_id", "beatmap_id",
        "difficulty", "star_rating", "status", "mapper", "source_url", "official_url",
        "archive_sha256", "nonce", "series_index", "series_leg",
    }
    allowed = required | {"origin", "key_count", "license"}
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        return False
    return (
        SAFE_ID.fullmatch(value.get("song_id", "")) is not None
        and all(text(value.get(field), 512) for field in (
            "song_title", "song_artist", "control_profile", "difficulty", "status", "mapper",
            "source_url", "official_url", "nonce",
        ))
        and value.get("ruleset") in RULESETS
        and bool(HEX_64.fullmatch(value.get("chart_digest", "")))
        and bool(HEX_64.fullmatch(value.get("archive_sha256", "")))
        and integer(value.get("bpm"), 1)
        and integer(value.get("duration_ms"), 1)
        and integer(value.get("lead_in_ms"), 0)
        and integer(value.get("note_count"), 1)
        and integer(value.get("beatmapset_id"), 1)
        and integer(value.get("beatmap_id"), 1)
        and isinstance(value.get("star_rating"), (int, float))
        and not isinstance(value.get("star_rating"), bool)
        and value["star_rating"] > 0
        and value.get("series_index") == series_index
        and value.get("series_leg") == series_leg
    )


def valid_performers(values) -> bool:
    if not isinstance(values, list) or len(values) > 2_000:
        return False
    seen = set()
    required = {"team_id", "team_name", "ready", "frames", "completion_sent"}
    allowed = required | {"applied_mods", "raw_basis", "settled_score", "eligible"}
    for value in values:
        if not isinstance(value, dict) or not required <= set(value) <= allowed:
            return False
        team_id = value.get("team_id")
        if not text(team_id, 128) or team_id in seen or not text(value.get("team_name"), 128):
            return False
        seen.add(team_id)
        if (
            not isinstance(value.get("ready"), bool)
            or not isinstance(value.get("completion_sent"), bool)
            or not integer(value.get("frames"), 0)
            or ("applied_mods" in value and not valid_mod_profile(value["applied_mods"]))
            or any(key in value and not integer(value[key], 0) for key in ("raw_basis", "settled_score"))
            or ("eligible" in value and not isinstance(value["eligible"], bool))
        ):
            return False
    return True


def valid_standings(values) -> bool:
    if not isinstance(values, list) or len(values) > 2_000:
        return False
    seen = set()
    required = {"team_id", "team_name", "completed_legs", "eligible_legs", "complete"}
    metrics = {"weakest_settled", "average_raw", "average_settled"}
    for value in values:
        if not isinstance(value, dict) or not required <= set(value) <= required | metrics:
            return False
        team_id = value.get("team_id")
        completed = value.get("completed_legs")
        eligible = value.get("eligible_legs")
        if (
            not text(team_id, 128)
            or team_id in seen
            or not text(value.get("team_name"), 128)
            or not integer(completed, 0)
            or completed > 4
            or not integer(eligible, 0)
            or eligible > completed
            or value.get("complete") is not (completed == 4)
            or any(key in value and not integer(value[key], 0) for key in metrics)
        ):
            return False
        seen.add(team_id)
    return True


def phase_matches(phase: str, now_ms: int, boundaries: list[int]) -> bool:
    enrollment_end, start_at, end_at, submission_end, next_heat = boundaries
    expected = (
        "enrollment" if now_ms < enrollment_end else
        "reveal" if now_ms < start_at else
        "running" if now_ms < end_at else
        "submission" if now_ms < submission_end else
        "results" if now_ms < next_heat else None
    )
    return phase == expected or min(abs(now_ms - boundary) for boundary in boundaries) <= 2_000


def valid_state(value: dict, local_now_ms: int | None = None) -> bool:
    required = {
        "server_now_unix_ms", "pool_schema", "crown_threshold", "raw_score_floor",
        "heat", "series", "ruleset_bag_rule", "series_rule",
    }
    allowed = required | {"crown_owner_id", "crown_owner_name", "crown_since_unix_ms"}
    if not required <= set(value) <= allowed:
        return False
    now_ms = value.get("server_now_unix_ms")
    local_now_ms = int(time.time() * 1000) if local_now_ms is None else local_now_ms
    if (
        not integer(now_ms, 1)
        or abs(now_ms - local_now_ms) > 15_000
        or value.get("pool_schema") != "rythme-pool-v2"
        or value.get("crown_threshold") != 2_100_000
        or value.get("raw_score_floor") != 700_000
        or not text(value.get("ruleset_bag_rule"), 512)
        or not text(value.get("series_rule"), 512)
    ):
        return False
    crown_fields = {key for key in ("crown_owner_id", "crown_owner_name", "crown_since_unix_ms") if key in value}
    if crown_fields and (
        crown_fields != {"crown_owner_id", "crown_owner_name", "crown_since_unix_ms"}
        or not text(value["crown_owner_id"], 128)
        or not text(value["crown_owner_name"], 128)
        or not integer(value["crown_since_unix_ms"], 1)
        or value["crown_since_unix_ms"] > now_ms
    ):
        return False
    heat = value.get("heat")
    series = value.get("series")
    heat_required = {
        "id", "index", "phase", "commitment", "enrollment_ends_unix_ms", "start_at_unix_ms",
        "end_at_unix_ms", "submission_ends_unix_ms", "next_heat_at_unix_ms", "performers",
    }
    if not isinstance(heat, dict) or not heat_required <= set(heat) <= heat_required | {"selection"}:
        return False
    index = heat.get("index")
    phase = heat.get("phase")
    boundaries = [heat.get(key) for key in (
        "enrollment_ends_unix_ms", "start_at_unix_ms", "end_at_unix_ms",
        "submission_ends_unix_ms", "next_heat_at_unix_ms",
    )]
    if (
        not text(heat.get("id"), 128)
        or not integer(index, 1)
        or phase not in PHASES
        or not HEX_64.fullmatch(heat.get("commitment", ""))
        or not all(integer(boundary, 1) for boundary in boundaries)
        or boundaries != sorted(boundaries)
        or len(set(boundaries)) != len(boundaries)
        or not phase_matches(phase, now_ms, boundaries)
        or not valid_performers(heat.get("performers"))
    ):
        return False
    series_index = (index - 1) // 4 + 1
    series_leg = (index - 1) % 4 + 1
    if (
        not isinstance(series, dict)
        or set(series) != {"index", "current_leg", "total_legs", "standings"}
        or series.get("index") != series_index
        or series.get("current_leg") != series_leg
        or series.get("total_legs") != 4
        or not valid_standings(series.get("standings"))
    ):
        return False
    selection = heat.get("selection")
    return (
        selection is None if phase == "enrollment"
        else valid_selection(selection, series_index, series_leg)
    )


@checker
def check_health_and_config(context: KothContext) -> None:
    health = get_json(context.target_ip, context.target_port, "/healthz")
    config = get_json(context.target_ip, context.target_port, "/api/v1/config")
    if health != {"status": "ok"} or not valid_config(config):
        raise Mumble("the coordinator health or release configuration changed")


@checker
def check_state_clock(context: KothContext) -> None:
    state = get_json(context.target_ip, context.target_port, "/api/v1/state")
    if not valid_state(state):
        raise Mumble("the coordinator state contract changed")
    # A second independent sample catches a frozen clock without mutating a
    # session, wave, Crown, or signed observer input.
    time.sleep(0.02)
    follow_up = get_json(context.target_ip, context.target_port, "/api/v1/state")
    if (
        not valid_state(follow_up)
        or follow_up["server_now_unix_ms"] <= state["server_now_unix_ms"]
    ):
        raise Mumble("the coordinator clock did not advance")


@checker
def check_event_onboarding(context: KothContext) -> None:
    if not probe_rejected_capability(context.target_ip, context.target_port):
        raise Mumble("the coordinator did not reject an invalid KotH capability")


def main() -> int:
    return run_koth_checker()


if __name__ == "__main__":
    raise SystemExit(main())
