#!/usr/bin/env python3
"""Read-only functional checker for the Tower of Babel KotH protocol."""

from __future__ import annotations

import json
import re
import socket

from lib import KothContext, Mumble, Offline, checker, run_koth_checker


MAX_LINE = 70 * 1024
ARCHES = {"amd64", "aarch64", "riscv64", "mips64be"}
NAMES = {"Doctor", "Kal'tsit", "Theresa", "Civilight Eterna"}
SYSCALL_HEX = {
    "amd64": "0f05",
    "aarch64": "010000d4",
    "riscv64": "73000000",
    "mips64be": "0000000c",
}
PATH_RE = re.compile(r"^/tmp/babel/[A-Za-z0-9]{10}/(runner|secret)-[0-3]$")


def _connection(context: KothContext):
    try:
        connection = socket.create_connection((context.target_ip, context.target_port), timeout=4)
        connection.settimeout(4)
        reader = connection.makefile("rb", buffering=0)
        first = reader.readline(MAX_LINE + 1)
        second = reader.readline(MAX_LINE + 1)
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the target did not complete its greeting") from error
    if first != b"BABEL/2 Tower of Babel \xe2\x80\x94 managed Leaderboard KotH\n" or not second.startswith(b"Send PING"):
        reader.close()
        connection.close()
        raise Mumble("the target greeting was incorrect")
    return connection, reader


def _request(context: KothContext, line: bytes) -> bytes:
    connection, reader = _connection(context)
    try:
        connection.sendall(line + b"\n")
        response = reader.readline(MAX_LINE + 1)
        if not response:
            raise Offline("the target closed before replying")
        if len(response) > MAX_LINE:
            raise Mumble("the target response was too large")
        return response.rstrip(b"\r\n")
    except (TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        raise Offline("the target did not complete the request") from error
    finally:
        reader.close()
        connection.close()


@checker
def check_ping(context: KothContext) -> None:
    if _request(context, b"PING") != b"PONG":
        raise Mumble("PING did not return PONG")


@checker
def check_info(context: KothContext) -> None:
    response = _request(context, b"INFO")
    if not response.startswith(b"INFO "):
        raise Mumble("INFO did not return JSON")
    try:
        value = json.loads(response[5:])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Mumble("INFO returned malformed JSON") from error
    expected = {
        "protocol", "instanceId", "waveId", "waveEndsAtUnixMs", "order", "stages",
        "activeWard", "ward", "limits", "mandatorySyscallHex",
    }
    if not isinstance(value, dict) or set(value) != expected or value["protocol"] != "babel-koth-v2":
        raise Mumble("INFO contract changed")
    if (
        not isinstance(value["instanceId"], str)
        or not re.fullmatch(r"[0-9a-f]{12}", value["instanceId"])
        or not isinstance(value["waveId"], str)
        or not re.fullmatch(r"babel-[0-9]+", value["waveId"])
        or isinstance(value["waveEndsAtUnixMs"], bool)
        or not isinstance(value["waveEndsAtUnixMs"], int)
        or value["waveEndsAtUnixMs"] <= 0
    ):
        raise Mumble("INFO wave clock was invalid")
    order = value["order"]
    stages = value["stages"]
    if not isinstance(order, list) or set(order) != NAMES or len(order) != 4 or not isinstance(stages, list) or len(stages) != 4:
        raise Mumble("INFO stage roster was invalid")
    if [stage.get("name") for stage in stages] != order:
        raise Mumble("INFO order and stage roster differ")
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict) or set(stage) != {"name", "arch", "secretPath", "runnerPath", "nextRunner"}:
            raise Mumble("INFO stage shape was invalid")
        if stage["arch"] not in ARCHES or not PATH_RE.fullmatch(stage["secretPath"]) or not PATH_RE.fullmatch(stage["runnerPath"]):
            raise Mumble("INFO stage path was invalid")
        if index < 3 and not PATH_RE.fullmatch(stage["nextRunner"]):
            raise Mumble("INFO next runner was invalid")
        if index == 3 and stage["nextRunner"] != "":
            raise Mumble("INFO leaf unexpectedly has a next runner")
    ward = value["activeWard"]
    if not isinstance(ward, list) or len(ward) > 8:
        raise Mumble("INFO ward was invalid")
    for pattern in ward:
        if not isinstance(pattern, dict) or set(pattern) != {"arch", "hex"} or pattern["arch"] not in ARCHES or not re.fullmatch(r"[0-9a-f]{4,16}", pattern["hex"]):
            raise Mumble("INFO ward pattern was invalid")
        mandatory = bytes.fromhex(SYSCALL_HEX[pattern["arch"]])
        raw = bytes.fromhex(pattern["hex"])
        if raw in mandatory or mandatory in raw:
            raise Mumble("INFO ward blocks a mandatory syscall")
    ward_state = value["ward"]
    if not isinstance(ward_state, dict) or set(ward_state) != {
        "status", "revision", "effectiveWave", "owner", "fallback"
    }:
        raise Mumble("INFO ward status was invalid")
    if ward_state["status"] not in {"empty", "active", "rejected"}:
        raise Mumble("INFO ward status was invalid")
    if isinstance(ward_state["revision"], bool) or not isinstance(ward_state["revision"], int) or ward_state["revision"] < 0:
        raise Mumble("INFO ward revision was invalid")
    if ward_state["effectiveWave"] is not None and (
        not isinstance(ward_state["effectiveWave"], str)
        or not re.fullmatch(r"babel-[0-9]+", ward_state["effectiveWave"])
    ):
        raise Mumble("INFO ward effective wave was invalid")
    if ward_state["owner"] is not None and (
        not isinstance(ward_state["owner"], str) or not re.fullmatch(r"[0-9a-f]{64}", ward_state["owner"])
    ):
        raise Mumble("INFO ward owner was invalid")
    if ward_state["fallback"] not in {None, "previous-known-good"}:
        raise Mumble("INFO ward fallback was invalid")
    if ward_state["status"] == "rejected" and ward_state["fallback"] != "previous-known-good":
        raise Mumble("INFO rejected ward did not expose fallback")
    limits = value["limits"]
    if limits != {"maxPayloadBytes": 32768, "maxAttemptsPerTeam": 12, "maxTeamsPerWave": 2000, "maxPatchPatterns": 8, "maxPatchBytes": 32, "maxPatchAttempts": 3}:
        raise Mumble("INFO limits changed")
    if value["mandatorySyscallHex"] != SYSCALL_HEX:
        raise Mumble("INFO syscall contract changed")


@checker
def check_read_only_errors(context: KothContext) -> None:
    if _request(context, b"RELAY 00") != b"ERR command: use HELP":
        raise Mumble("unauthenticated relay was not rejected")
    if _request(context, b"PATCH {\"patterns\":[]}") != b"ERR command: use HELP":
        raise Mumble("unauthenticated patch was not rejected")
    if _request(context, b"STATUS") != b"ERR auth-required":
        raise Mumble("unauthenticated status was not rejected")


@checker
def check_invalid_capability(context: KothContext) -> None:
    if _request(context, b"HELLO koth_bad") != b"ERR auth: invalid KotH capability":
        raise Mumble("invalid KotH capability was not rejected at the protocol boundary")


if __name__ == "__main__":
    raise SystemExit(run_koth_checker())
