#!/usr/bin/env python3
"""Exercise exact secure-response replay without changing arena gameplay state."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

from solver import ArenaClient, CHANNEL_PROTOCOL, canonical_channel_request


def send(url: str, headers: dict[str, str]) -> tuple[int, bytes, object]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def verify_response(client: ArenaClient, status: int, body: bytes, headers: object, request_mac: bytes) -> None:
    ack = headers.get("X-Rythme-Ack")
    proof = headers.get("X-Rythme-Response-Signature")
    if ack != "1" or not proof:
        raise RuntimeError("secure response did not acknowledge sequence 1")
    canonical = "\n".join([
        CHANNEL_PROTOCOL,
        client.channel["id"],
        "1",
        str(status),
        hashlib.sha256(body).hexdigest(),
        request_mac.hex(),
        "",
    ]).encode()
    expected = hmac.new(client.channel["response_key"], canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, proof):
        raise RuntimeError("secure replay response proof did not verify")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--team-token-stdin", action="store_true")
    parser.add_argument("--team-id", default="replay-probe")
    parser.add_argument("--duplicates", type=int, default=2)
    args = parser.parse_args()
    if not 2 <= args.duplicates <= 64:
        raise RuntimeError("--duplicates must be between 2 and 64")

    team_token = None
    if args.team_token_stdin:
        team_token = sys.stdin.readline().strip()
        if not team_token.startswith("koth_"):
            raise RuntimeError("stdin did not contain an RSCTF KotH capability")

    client = ArenaClient(args.url)
    client.create_session(args.team_id, "Replay Probe", team_token=team_token)
    path = "/api/v2/state"
    previous = "0" * 64
    sent_at = time.time_ns() // 1_000_000
    canonical = canonical_channel_request(client.channel["id"], "GET", path, 1, sent_at, b"", previous)
    request_mac = hmac.new(client.channel["request_key"], canonical, hashlib.sha256).digest()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {client.token}",
        "X-Rythme-Channel": client.channel["id"],
        "X-Rythme-Sequence": "1",
        "X-Rythme-Timestamp": str(sent_at),
        "X-Rythme-Previous": previous,
        "X-Rythme-Signature": request_mac.hex(),
    }

    with ThreadPoolExecutor(max_workers=args.duplicates) as executor:
        responses = list(executor.map(
            lambda _: send(client.base_url + path, headers),
            range(args.duplicates),
        ))
    first_status, first_body, first_headers = responses[0]
    first_proof = (
        first_headers.get("X-Rythme-Ack"),
        first_headers.get("X-Rythme-Response-Signature"),
    )
    for status, body, response_headers in responses:
        verify_response(client, status, body, response_headers, request_mac)
        proof = (
            response_headers.get("X-Rythme-Ack"),
            response_headers.get("X-Rythme-Response-Signature"),
        )
        if status != 200 or body != first_body or proof != first_proof:
            raise RuntimeError("exact replay did not return the original signed response")

    client.sequence = 1
    client.previous_mac = request_mac
    next_state = client.get(path)
    if not isinstance(next_state, dict) or "heat" not in next_state:
        raise RuntimeError("the request after an exact replay was not accepted")

    print(json.dumps({
        "firstStatus": first_status,
        "replayStatus": responses[-1][0],
        "replayCount": len(responses),
        "sameBody": all(body == first_body for _, body, _ in responses),
        "sameResponseProof": all(
            (response_headers.get("X-Rythme-Ack"), response_headers.get("X-Rythme-Response-Signature"))
            == first_proof
            for _, _, response_headers in responses
        ),
        "nextSequenceAccepted": True,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
