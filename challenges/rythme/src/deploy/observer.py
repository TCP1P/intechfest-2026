#!/usr/bin/env python3
"""Trusted RSCTF Leaderboard referee for Rythme."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


OBJECTIVE_IDS = ["official-score"]
MAX_TEAMS = 2_000
MAX_WAVES = 64
MAX_TEAM_WAVE_ROWS = 2_000
MAX_SCORE = 1_000_000_000_000
POLL_SECONDS = 1.0
HEARTBEAT_FILE = Path(
    os.environ.get("RYTHME_OBSERVER_HEARTBEAT_FILE", "/tmp/rythme-observer-heartbeat")
)


def env_int(name: str) -> int:
    value = int(os.environ[name])
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


GAME_ID = env_int("RSCTF_GAME_ID")
CHALLENGE_ID = env_int("RSCTF_CHALLENGE_ID")
RSCTF_URL = os.environ.get("RSCTF_URL", "http://rsctf:8080").rstrip("/")
ARENA_URL = os.environ["RYTHME_ARENA_URL"].rstrip("/")
SECRET = Path(
    os.environ.get("RSCTF_OBSERVER_SECRET_FILE", "/run/secrets/observer")
).read_text().strip()
if not SECRET.startswith("koth_api_") or len(SECRET) < 32:
    raise ValueError("invalid observer secret")


def request_json(
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST" if body is not None else "GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "rsctf-rythme-referee/2",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP {response.status}")
        value = json.load(response)
    if isinstance(value, dict) and set(value) >= {"data"} and isinstance(value["data"], dict):
        value = value["data"]
    if not isinstance(value, dict):
        raise RuntimeError("endpoint returned a non-object")
    return value


def canonical_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def mark_healthy() -> None:
    """Record a successful referee cycle for the container health probe."""
    HEARTBEAT_FILE.touch(exist_ok=True)


def is_round_gap(error: BaseException) -> bool:
    return isinstance(error, urllib.error.HTTPError) and error.code == 409


def build_body(context: dict, scoring: dict) -> bytes:
    opaque = context.get("context")
    eligible_values = context.get("eligibleTokenHashes")
    if not canonical_hash(opaque) or not isinstance(eligible_values, list):
        raise RuntimeError("invalid RSCTF context")
    eligible = set(eligible_values)
    if not 1 <= len(eligible) <= MAX_TEAMS or len(eligible) != len(eligible_values):
        raise RuntimeError("invalid eligible roster")
    frozen_ids = context.get("objectiveIds")
    if frozen_ids not in ([], OBJECTIVE_IDS):
        raise RuntimeError("Rythme objective schema changed")

    round_start = context.get("waveWindowStartsAt")
    round_end = context.get("waveWindowEndsAt")
    waves_value = scoring.get("waves")
    if (
        not isinstance(round_start, int)
        or isinstance(round_start, bool)
        or not isinstance(round_end, int)
        or isinstance(round_end, bool)
        or round_start >= round_end
        or not isinstance(waves_value, list)
        or len(waves_value) > MAX_WAVES
    ):
        raise RuntimeError("invalid Rythme scoring window")

    waves = []
    seen_waves: set[str] = set()
    team_wave_rows = 0
    for wave in waves_value:
        if not isinstance(wave, dict):
            raise RuntimeError("invalid Rythme wave")
        wave_id = wave.get("wave_id")
        ended_at = wave.get("ended_at_unix_ms")
        results = wave.get("results")
        crown_owner = wave.get("crown_owner_id", "")
        if (
            not isinstance(wave_id, str)
            or not wave_id
            or len(wave_id.encode()) > 128
            or wave_id in seen_waves
            or not isinstance(ended_at, int)
            or isinstance(ended_at, bool)
            or not round_start <= ended_at < round_end
            or not isinstance(results, list)
            or not isinstance(crown_owner, str)
        ):
            raise RuntimeError("invalid Rythme finalized wave")
        seen_waves.add(wave_id)

        accepted_results = []
        seen_teams: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("invalid Rythme wave result")
            token_hash = result.get("team_id")
            score = result.get("score")
            is_crown = result.get("is_crown")
            if token_hash not in eligible:
                continue
            if (
                token_hash in seen_teams
                or not isinstance(score, int)
                or isinstance(score, bool)
                or not 0 <= score <= MAX_SCORE
                or not isinstance(is_crown, bool)
            ):
                raise RuntimeError("invalid Rythme team wave result")
            seen_teams.add(token_hash)
            accepted_results.append((token_hash, score, is_crown))

        team_wave_rows += len(accepted_results)
        if team_wave_rows > MAX_TEAM_WAVE_ROWS:
            raise RuntimeError("Rythme scoring window contains too many team-wave rows")

        best_score = max((score for _, score, _ in accepted_results), default=0)
        crowns = [token_hash for token_hash, _, is_crown in accepted_results if is_crown]
        leaders = [
            token_hash
            for token_hash, score, _ in accepted_results
            if best_score > 0 and score == best_score
        ]
        if len(leaders) == 1:
            if crowns != leaders or crown_owner != leaders[0]:
                raise RuntimeError("Rythme wave has no unique recognized Crown holder")
        elif best_score > 0:
            if crowns or crown_owner:
                raise RuntimeError("Rythme tied wave cannot have a Crown holder")
        elif crowns or crown_owner:
            raise RuntimeError("Rythme zero-score wave cannot have a Crown holder")

        possible = max(best_score, 1)
        teams = [
            {
                "tokenHash": token_hash,
                "activity": {"earned": 1, "possible": 1},
                "objectives": [{"earned": score, "possible": possible}],
                "isCrown": is_crown,
            }
            for token_hash, score, is_crown in accepted_results
        ]
        teams.sort(key=lambda row: row["tokenHash"])
        waves.append(
            {
                "waveId": wave_id,
                "endedAtUnixMs": ended_at,
                "teams": teams,
            }
        )
    waves.sort(key=lambda wave: (wave["endedAtUnixMs"], wave["waveId"]))
    return json.dumps(
        {"context": opaque, "objectiveIds": OBJECTIVE_IDS, "waves": waves},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def submit(context: dict, body: bytes, timestamp: int) -> dict:
    message = f"{timestamp}.{GAME_ID}.{CHALLENGE_ID}.".encode() + body
    signature = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    return request_json(
        f"{RSCTF_URL}/api/v1/koth/games/{GAME_ID}/challenges/{CHALLENGE_ID}/observations",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-RSCTF-Timestamp": str(timestamp),
            "X-RSCTF-Signature": f"sha256={signature}",
        },
    )


def main() -> None:
    last_context = None
    last_digest = None
    last_timestamp = 0
    while True:
        try:
            context = request_json(
                f"{RSCTF_URL}/api/v1/koth/games/{GAME_ID}/challenges/{CHALLENGE_ID}/context"
            )
            scoring = request_json(
                f"{ARENA_URL}/api/v1/scoring?from={context['waveWindowStartsAt']}&to={context['waveWindowEndsAt']}"
            )
            body = build_body(context, scoring)
            digest = hashlib.sha256(body).hexdigest()
            opaque = context["context"]
            if opaque != last_context or digest != last_digest:
                last_timestamp = max(time.time_ns() // 1_000_000, last_timestamp + 1)
                accepted = submit(context, body, last_timestamp)
                submitted = json.loads(body)
                expected_waves = len(submitted["waves"])
                expected_teams = len(
                    {
                        team["tokenHash"]
                        for wave in submitted["waves"]
                        for team in wave["teams"]
                    }
                )
                if (
                    accepted.get("accepted") is not True
                    or accepted.get("roundNumber") != context.get("roundNumber")
                    or accepted.get("submittedWaves") != expected_waves
                    or accepted.get("submittedTeams") != expected_teams
                    or accepted.get("recognizedTeams") != expected_teams
                ):
                    raise RuntimeError("RSCTF did not acknowledge the complete snapshot")
                print(
                    f"accepted round={accepted['roundNumber']} cycle={accepted['cycleNumber']} "
                    f"waves={expected_waves} teams={expected_teams}",
                    flush=True,
                )
                last_context = opaque
                last_digest = digest
            mark_healthy()
        except urllib.error.HTTPError as error:
            if is_round_gap(error):
                mark_healthy()
            else:
                print(f"referee waiting: HTTP {error.code}", flush=True)
        except (
            OSError,
            ValueError,
            RuntimeError,
            urllib.error.URLError,
        ) as error:
            print(f"referee waiting: {error}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
