#!/usr/bin/env python3
"""Reference solver for the Rythme KOTH challenge."""

import argparse
import base64
import hashlib
import hmac
import io
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pwn import context, log


CHANNEL_PROTOCOL = "rythme-secure-channel-v3"
EMPTY_SETTINGS_HASH = hashlib.sha256(b"").hexdigest()
MODIFIER_ENVELOPE_VERSION = 2
PROFILE_TAG = 0x32
FRAME_MAGIC = bytes.fromhex("299ed946")
FRAME_VERSION = 1
FRAME_ENROLL = 0x5C
FRAME_READY = 0x12
FRAME_CUES = 0x3B
FRAME_INPUT = 0x21
FRAME_RESULT = 0x55
FRAME_FINISH = 0x31
POLICY_VARIANT_KEY = bytes.fromhex(
    "76505bdbc56782425f3d162a2b6b2849"
    "6a1891f921f7974c37f2dee73b26fceb"
)
POLICY_PROOF_KEY = bytes.fromhex(
    "6e2c211ee477e9b498e24cd01b58db43"
    "6072ffee67372d9506d8bc311eb72ec7"
)
MODE_NUMBERS = {"osu": "0", "taiko": "1", "fruits": "2", "mania": "3"}
MAXIMUM_ARCHIVE_BYTES = 96 * 1024 * 1024

# These are deliberately aggressive. The patched client still has to run the
# real 1.5x lazer playfield; the extra visual modifiers raise the applied
# multiplier before final settlement.
APPLIED_MODS = {
    "osu": ["DT", "FL", "HD", "HR"],
    "taiko": ["DT", "FL", "HD", "HR"],
    "fruits": ["DT", "FL", "HD", "HR"],
    "mania": ["DT"],
}


class FrameReader:
    def __init__(self, raw, expected_action):
        if (
            len(raw) < 6
            or raw[:4] != FRAME_MAGIC
            or raw[4] != FRAME_VERSION
            or raw[5] != expected_action
        ):
            raise RuntimeError("coordinator returned an invalid arena frame")
        self.raw = raw
        self.offset = 6

    def take(self, length):
        end = self.offset + length
        if length < 0 or end > len(self.raw):
            raise RuntimeError("coordinator arena frame is truncated")
        value = self.raw[self.offset:end]
        self.offset = end
        return value

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def i64(self):
        return struct.unpack("<q", self.take(8))[0]

    def f64(self):
        return struct.unpack("<d", self.take(8))[0]

    def blob(self, maximum=8192):
        length = self.u16()
        if length > maximum:
            raise RuntimeError("coordinator arena field is oversized")
        return self.take(length)

    def text(self, maximum=8192):
        return self.blob(maximum).decode("utf-8")

    def done(self):
        if self.offset != len(self.raw):
            raise RuntimeError("coordinator arena frame has trailing data")


def frame(action, payload=b""):
    return FRAME_MAGIC + bytes((FRAME_VERSION, action)) + payload


def wire_blob(value):
    if isinstance(value, str):
        value = value.encode()
    if len(value) > 0xFFFF:
        raise RuntimeError("arena field is oversized")
    return struct.pack("<H", len(value)) + value


def read_profile(reader):
    return {
        "acronym": reader.text(64),
        "rate_milli": reader.i32(),
        "settings_hash": reader.text(128),
        "score_multiplier_milli": reader.i32(),
    }


def read_judge(reader):
    return {
        "hit_count": reader.i32(),
        "note_count": reader.i32(),
        "total_score_without_mods": reader.i64(),
        "perfect_raw": reader.i64(),
        "raw_basis": reader.i64(),
        "accuracy": reader.f64(),
        "max_combo": reader.i32(),
        "input_digest": reader.text(512),
    }


class ArenaClient:
    def __init__(self, base_url, debug=False):
        self.base_url = base_url.rstrip("/")
        self.token = None
        self.debug = debug
        self.channel = None
        self.sequence = 0
        self.previous_mac = None

    def request(self, method, path, body=None):
        raw = b""
        headers = {"Accept": "application/json"}
        if body is not None:
            raw = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request_signature = None
        if self.channel is not None:
            self.sequence += 1
            sent_at = time.time_ns() // 1_000_000
            previous = (
                self.previous_mac.hex()
                if self.previous_mac is not None
                else "0" * 64
            )
            canonical = canonical_channel_request(
                self.channel["id"],
                method,
                path,
                self.sequence,
                sent_at,
                raw,
                previous,
            )
            request_signature = hmac.new(
                self.channel["request_key"],
                canonical,
                hashlib.sha256,
            ).digest()
            self.previous_mac = request_signature
            headers.update({
                "X-Rythme-Channel": self.channel["id"],
                "X-Rythme-Sequence": str(self.sequence),
                "X-Rythme-Timestamp": str(sent_at),
                "X-Rythme-Previous": previous,
                "X-Rythme-Signature": request_signature.hex(),
            })

        request = urllib.request.Request(
            self.base_url + path,
            data=raw if body is not None else None,
            headers=headers,
            method=method,
        )
        if self.debug:
            log.debug(f"{method} {path} {raw.decode() if raw else ''}")
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = response.read()
                status = response.status
                response_headers = response.headers
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
            response_headers = error.headers
            if self.channel is not None and request_signature is not None:
                self._verify_response(
                    status, payload, response_headers, request_signature
                )
            try:
                detail = json.loads(payload).get("detail", payload.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = payload.decode(errors="replace")
            raise RuntimeError(
                f"{method} {path}: HTTP {error.code}: {detail}"
            ) from error

        if self.channel is not None and request_signature is not None:
            self._verify_response(
                status, payload, response_headers, request_signature
            )
        parsed = json.loads(payload)
        if self.debug:
            log.debug(f"response: {parsed}")
        return parsed

    def request_bytes(self, method, path, raw):
        headers = {
            "Accept": "application/octet-stream",
            "Content-Type": "application/octet-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request_signature = None
        if self.channel is not None:
            self.sequence += 1
            sent_at = time.time_ns() // 1_000_000
            previous = (
                self.previous_mac.hex()
                if self.previous_mac is not None
                else "0" * 64
            )
            canonical = canonical_channel_request(
                self.channel["id"],
                method,
                path,
                self.sequence,
                sent_at,
                raw,
                previous,
            )
            request_signature = hmac.new(
                self.channel["request_key"],
                canonical,
                hashlib.sha256,
            ).digest()
            self.previous_mac = request_signature
            headers.update({
                "X-Rythme-Channel": self.channel["id"],
                "X-Rythme-Sequence": str(self.sequence),
                "X-Rythme-Timestamp": str(sent_at),
                "X-Rythme-Previous": previous,
                "X-Rythme-Signature": request_signature.hex(),
            })

        request = urllib.request.Request(
            self.base_url + path,
            data=raw,
            headers=headers,
            method=method,
        )
        if self.debug:
            log.debug(f"{method} {path} {raw.hex()}")
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = response.read()
                status = response.status
                response_headers = response.headers
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
            response_headers = error.headers
            if self.channel is not None and request_signature is not None:
                self._verify_response(
                    status, payload, response_headers, request_signature
                )
            try:
                detail = json.loads(payload).get("detail", payload.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = payload.decode(errors="replace")
            raise RuntimeError(
                f"{method} {path}: HTTP {error.code}: {detail}"
            ) from error

        if self.channel is not None and request_signature is not None:
            self._verify_response(
                status, payload, response_headers, request_signature
            )
        if self.debug:
            log.debug(f"response: {payload.hex()}")
        return payload

    def arena_frame(self, action, payload=b""):
        return self.request_bytes(
            "POST", "/api/v3/frame", frame(action, payload)
        )

    def public_bytes(self, path):
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Accept": "application/x-osu-beatmap-archive"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAXIMUM_ARCHIVE_BYTES:
                raise RuntimeError("beatmap archive exceeds the client limit")
            payload = response.read(MAXIMUM_ARCHIVE_BYTES + 1)
        if len(payload) > MAXIMUM_ARCHIVE_BYTES:
            raise RuntimeError("beatmap archive exceeds the client limit")
        return payload

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)

    def create_session(self, team_id, team_name, team_token=None):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_key = base64.b64encode(public_der).decode()
        client_nonce_raw = os.urandom(32)
        client_nonce = base64.b64encode(client_nonce_raw).decode()

        if team_token:
            path = "/api/v2/session/rctf"
            payload = {
                "team_token": team_token,
                "client_public_key": public_key,
                "client_nonce": client_nonce,
            }
        else:
            path = "/api/v2/session/local"
            payload = {
                "team_id": team_id,
                "team_name": team_name,
                "client_public_key": public_key,
                "client_nonce": client_nonce,
            }
        session = self.post(path, payload)

        server_public_der = base64.b64decode(session["server_public_key"])
        server_public = serialization.load_der_public_key(server_public_der)
        if not isinstance(server_public, ec.EllipticCurvePublicKey):
            raise RuntimeError("coordinator returned a non-EC channel key")
        shared = private_key.exchange(ec.ECDH(), server_public)
        server_nonce = base64.b64decode(session["server_nonce"])
        if len(server_nonce) != 32:
            raise RuntimeError("coordinator returned an invalid channel nonce")
        salt = hashlib.sha256(client_nonce_raw + server_nonce).digest()
        info = "\n".join([
            "rythme-channel-kdf-v3",
            session["team_id"],
            session["channel_id"],
            public_key,
            session["server_public_key"],
            "",
        ]).encode()
        material = hkdf_sha256(shared, salt, info, 96)
        self.channel = {
            "id": session["channel_id"],
            "request_key": material[0:32],
            "response_key": material[32:64],
            "state_key": material[64:96],
        }
        proof_payload = "\n".join([
            "rythme-channel-handshake-v3",
            session["team_id"],
            session["access_token"],
            session["channel_id"],
            public_key,
            session["server_public_key"],
            client_nonce,
            session["server_nonce"],
            str(session["channel_expires_at_unix_ms"]),
            "",
        ]).encode()
        expected = hmac.new(
            self.channel["response_key"],
            proof_payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, session["server_proof"]):
            self.channel = None
            raise RuntimeError("coordinator channel proof did not verify")

        self.token = session["access_token"]
        self.sequence = 0
        self.previous_mac = None
        return session

    def _verify_response(self, status, payload, headers, request_signature):
        ack = headers.get("X-Rythme-Ack")
        provided = headers.get("X-Rythme-Response-Signature")
        if ack != str(self.sequence) or not provided:
            raise RuntimeError("coordinator response was not bound to the request")
        canonical = "\n".join([
            CHANNEL_PROTOCOL,
            self.channel["id"],
            str(self.sequence),
            str(status),
            hashlib.sha256(payload).hexdigest(),
            request_signature.hex(),
            "",
        ]).encode()
        expected = hmac.new(
            self.channel["response_key"],
            canonical,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise RuntimeError("coordinator response signature did not verify")


def hkdf_sha256(input_key_material, salt, info, length):
    pseudorandom_key = hmac.new(
        salt, input_key_material, hashlib.sha256
    ).digest()
    output = bytearray()
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac.new(
            pseudorandom_key,
            previous + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        output.extend(previous)
        counter += 1
    return bytes(output[:length])


def canonical_channel_request(
    channel_id,
    method,
    path,
    sequence,
    sent_at,
    body,
    previous,
):
    return "\n".join([
        CHANNEL_PROTOCOL,
        channel_id,
        method.upper(),
        path,
        str(sequence),
        str(sent_at),
        hashlib.sha256(body).hexdigest(),
        previous.lower(),
        "",
    ]).encode()


def wait_for(client, predicate, description):
    last_phase = None
    last_log = 0.0
    while True:
        state = client.get("/api/v2/state")
        if predicate(state):
            return state
        phase = state["heat"]["phase"]
        now = time.monotonic()
        if phase != last_phase or now - last_log >= 15:
            log.info(
                f"Waiting for {description}: heat {state['heat']['id']} is "
                f"{phase}, wave {state['series']['current_leg']}/4"
            )
            last_phase = phase
            last_log = now
        time.sleep(0.25)


def measure_server_clock(client):
    best = None
    for _ in range(7):
        sent = time.time_ns() // 1_000_000
        state = client.get("/api/v2/state")
        received = time.time_ns() // 1_000_000
        rtt = received - sent
        offset = state["server_now_unix_ms"] - (sent + received) // 2
        if best is None or rtt < best[0]:
            best = (rtt, offset)
        time.sleep(0.03)
    return best[1], max(0, best[0] // 2)


def sleep_until_server(target_unix_ms, offset_ms):
    while True:
        remaining = target_unix_ms - (
            time.time_ns() // 1_000_000 + offset_ms
        )
        if remaining <= 0:
            return
        if remaining > 20:
            time.sleep((remaining - 10) / 1000)
        else:
            time.sleep(0.001)


def parse_exact_chart(archive, selection):
    expected_hash = selection["archive_sha256"].lower()
    actual_hash = hashlib.sha256(archive).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise RuntimeError(
            f"beatmap archive hash {actual_hash} does not match the reveal"
        )

    matched = None
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        for name in package.namelist():
            if not name.lower().endswith(".osu"):
                continue
            parsed = parse_osu_file(package.read(name))
            if parsed["metadata"].get("BeatmapID") == str(
                selection["beatmap_id"]
            ):
                if matched is not None:
                    raise RuntimeError("archive contains a duplicate BeatmapID")
                matched = parsed
    if matched is None:
        raise RuntimeError("revealed exact difficulty is absent from the archive")
    if matched["metadata"].get("BeatmapSetID") != str(
        selection["beatmapset_id"]
    ):
        raise RuntimeError("archive BeatmapSetID does not match the reveal")
    ruleset = selection["ruleset"]
    if matched["general"].get("Mode") != MODE_NUMBERS[ruleset]:
        raise RuntimeError("archive native mode does not match the reveal")

    key_count = int(float(matched["difficulty"].get("CircleSize", "0")) + 0.5)
    previous_fruit_x = None
    notes = []
    for raw_object in matched["objects"]:
        fields = raw_object.split(",")
        if len(fields) < 5:
            raise RuntimeError(f"malformed hit object: {raw_object}")
        x, y, at_ms = map(int, fields[:3])
        object_type = int(fields[3])
        hit_sound = int(fields[4])
        is_circle = bool(object_type & 1)
        is_slider = bool(object_type & 2)
        is_hold = bool(object_type & 128)
        if ruleset in ("osu", "fruits") and not (is_circle or is_slider):
            continue
        if ruleset == "taiko" and not is_circle:
            continue
        if ruleset == "mania" and not (is_circle or is_hold):
            continue

        note = {"at_ms": at_ms}
        if ruleset == "osu":
            note.update(
                action=0x3,
                x=scale_coordinate(x, 512),
                y=scale_coordinate(y, 384),
            )
        elif ruleset == "taiko":
            note["action"] = 0x3 if hit_sound & (2 | 8) else 0xC
        elif ruleset == "fruits":
            note["action"] = (
                1
                if previous_fruit_x is None or x < previous_fruit_x
                else 2
            )
            previous_fruit_x = x
        else:
            if key_count != 4:
                raise RuntimeError(f"expected native 4K mania, got {key_count}K")
            lane = min(max(x * key_count // 512, 0), key_count - 1)
            note["action"] = 1 << lane
        notes.append(note)
    notes.sort(key=lambda item: item["at_ms"])
    if len(notes) != selection["note_count"]:
        raise RuntimeError(
            f"extracted {len(notes)} objects, reveal says "
            f"{selection['note_count']}"
        )
    return notes


def parse_osu_file(raw):
    result = {
        "general": {},
        "metadata": {},
        "difficulty": {},
        "objects": [],
    }
    section = ""
    text = raw.decode("utf-8-sig", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if not line or line.startswith("//"):
            continue
        if section == "HitObjects":
            result["objects"].append(line)
            continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        destination = {
            "General": "general",
            "Metadata": "metadata",
            "Difficulty": "difficulty",
        }.get(section)
        if destination:
            result[destination][key] = value
    return result


def scale_coordinate(value, maximum):
    value = min(max(value, 0), maximum)
    return math.floor(value * 65535 / maximum + 0.5)


def policy_spec(nonce):
    digest = hmac.new(
        POLICY_VARIANT_KEY,
        FRAME_MAGIC + nonce.encode(),
        hashlib.sha256,
    ).digest()
    magic = bytes(left ^ right for left, right in zip(digest[:4], FRAME_MAGIC))
    tag = digest[4] ^ PROFILE_TAG
    if tag == 0:
        tag = PROFILE_TAG | 1
    return magic, tag, digest[5] | 0x80, digest[6] % 15 + 1


def modifier_envelope(nonce, applied):
    magic, tag, selector, stride = policy_spec(nonce)
    profiles = [[], [], []]
    server_index = (selector + stride) % len(profiles)
    signed_selector = selector - 256 if selector >= 128 else selector
    native_index = (signed_selector + stride) % len(profiles)
    if server_index == native_index:
        raise RuntimeError("release selector did not create two policy views")
    profiles[server_index] = applied
    profiles[native_index] = ["HT"]

    raw = bytearray(magic)
    raw.extend((MODIFIER_ENVELOPE_VERSION, len(profiles)))
    for profile in profiles:
        value = bytearray((len(profile),))
        for acronym in profile:
            encoded = acronym.encode("ascii")
            if not encoded or len(encoded) > 8:
                raise RuntimeError("invalid modifier acronym")
            value.append(len(encoded))
            value.extend(encoded)
        raw.append(tag)
        raw.extend(struct.pack("<H", len(value)))
        raw.extend(value)
    return bytes(raw)


def native_proof(ready_capsule, judge_capsule, claim):
    ready_raw = ready_capsule
    judge_raw = judge_capsule
    acronym = claim["acronym"].encode("ascii")
    canonical = b"".join([
        FRAME_MAGIC,
        bytes((FRAME_FINISH,)),
        hashlib.sha256(ready_raw).digest(),
        hashlib.sha256(judge_raw).digest(),
        bytes((len(acronym),)),
        acronym,
        struct.pack("<i", claim["rate_milli"]),
        claim["settings_hash"].encode("ascii"),
    ])
    return hmac.new(
        POLICY_PROOF_KEY,
        canonical,
        hashlib.sha256,
    ).digest()


def arena_enroll(client):
    reader = FrameReader(client.arena_frame(FRAME_ENROLL), FRAME_ENROLL)
    result = {"heat_id": reader.text(512), "heat_ticket_id": reader.text(512)}
    reader.done()
    return result


def arena_ready(client, ticket, envelope):
    raw = client.arena_frame(
        FRAME_READY,
        wire_blob(ticket) + wire_blob(envelope),
    )
    reader = FrameReader(raw, FRAME_READY)
    if reader.u8() != 1:
        raise RuntimeError("coordinator did not accept ready-up")
    result = {
        "locked_mods": read_profile(reader),
        "ready_capsule": reader.blob(8192),
    }
    reader.done()
    return result


def arena_cues(client, ticket):
    reader = FrameReader(
        client.arena_frame(FRAME_CUES, wire_blob(ticket)),
        FRAME_CUES,
    )
    heat_id = reader.text(512)
    server_now = reader.i64()
    count = reader.u16()
    if count > 256:
        raise RuntimeError("coordinator returned too many cues")
    cues = []
    for _ in range(count):
        cues.append({
            "note_index": reader.i32(),
            "opens_unix_ms": reader.i64(),
            "expires_unix_ms": reader.i64(),
            "token": reader.text(512),
        })
    reader.done()
    return {
        "heat_id": heat_id,
        "server_now_unix_ms": server_now,
        "cues": cues,
    }


def arena_input(client, frames):
    if not frames:
        raise RuntimeError("cannot send an empty input batch")
    first = frames[0]
    ruleset_wire = {"osu": 0, "taiko": 1, "fruits": 2, "mania": 3}
    payload = bytearray()
    payload.extend(wire_blob(first["heat_ticket_id"]))
    payload.append(ruleset_wire[first["ruleset_id"]])
    payload.extend(struct.pack("<H", len(frames)))
    for item in frames:
        if (
            item["heat_ticket_id"] != first["heat_ticket_id"]
            or item["ruleset_id"] != first["ruleset_id"]
        ):
            raise RuntimeError("input batch binding changed")
        payload.extend(struct.pack("<Qi", item["sequence"], item["note_index"]))
        payload.extend(wire_blob(item["cue_token"]))
        payload.extend(struct.pack("<H", item["action_bits"]))
        pointer = "pointer_x_u16" in item and "pointer_y_u16" in item
        payload.append(1 if pointer else 0)
        if pointer:
            payload.extend(struct.pack(
                "<HH", item["pointer_x_u16"], item["pointer_y_u16"]
            ))

    reader = FrameReader(
        client.arena_frame(FRAME_INPUT, bytes(payload)),
        FRAME_INPUT,
    )
    result = {"accepted": reader.i32(), "digest": reader.text(512)}
    reader.done()
    return result


def arena_result(client, ticket):
    reader = FrameReader(
        client.arena_frame(FRAME_RESULT, wire_blob(ticket)),
        FRAME_RESULT,
    )
    result = {
        "judge": read_judge(reader),
        "judge_capsule": reader.blob(8192),
    }
    reader.done()
    return result


def arena_finish(client, ready_capsule, judge_capsule, claim):
    proof = native_proof(ready_capsule, judge_capsule, claim)
    payload = b"".join([
        wire_blob(ready_capsule),
        wire_blob(judge_capsule),
        wire_blob(claim["acronym"]),
        struct.pack("<i", claim["rate_milli"]),
        wire_blob(claim["settings_hash"]),
        proof,
    ])
    reader = FrameReader(
        client.arena_frame(FRAME_FINISH, payload),
        FRAME_FINISH,
    )
    settlement = {
        "team_id": reader.text(512),
        "team_name": reader.text(512),
        "eligible": bool(reader.u8()),
        "reason": reader.text(1024) or None,
    }
    settlement["applied_mods"] = read_profile(reader) if reader.u8() else None
    settlement["attested_mods"] = (
        {
            "acronym": reader.text(64),
            "rate_milli": reader.i32(),
            "settings_hash": reader.text(128),
        }
        if reader.u8()
        else None
    )
    settlement["applied_multiplier"] = reader.f64()
    settlement["attested_multiplier"] = reader.f64()
    settlement["multiplier"] = reader.f64()
    settlement["settled_score"] = reader.i64()
    settlement["judge"] = read_judge(reader)
    settlement["series_index"] = reader.i64()
    settlement["series_leg"] = reader.i32()
    took_crown = bool(reader.u8())
    reader.done()
    return {"settlement": settlement, "took_crown": took_crown}


def note_groups(notes, start_at):
    timed = [
        (index, start_at + note["at_ms"] * 1000 // 1500, note)
        for index, note in enumerate(notes)
    ]
    groups = []
    current = []
    for item in timed:
        if current and (
            item[1] - current[0][1] > 100 or len(current) >= 32
        ):
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


def wait_for_cues(client, ticket, note_indexes, offset_ms):
    tokens = {}
    wanted = set(note_indexes)
    while not wanted.issubset(tokens):
        response = arena_cues(client, ticket)
        for cue in response["cues"]:
            if cue["note_index"] in wanted:
                tokens[cue["note_index"]] = cue["token"]
        if wanted.issubset(tokens):
            break
        future_opens = [
            cue["opens_unix_ms"]
            for cue in response["cues"]
            if cue["note_index"] in wanted
        ]
        if future_opens:
            sleep_until_server(min(future_opens), offset_ms)
        else:
            time.sleep(0.025)
    return tokens


def play_live_transcript(client, heat, ticket, notes, offset_ms, one_way_ms):
    ruleset = heat["selection"]["ruleset"]
    sequence = 0
    groups = note_groups(notes, heat["start_at_unix_ms"])
    progress_step = max(1, len(groups) // 10)

    for group_index, group in enumerate(groups, start=1):
        tokens = wait_for_cues(
            client,
            ticket,
            [note_index for note_index, _, _ in group],
            offset_ms,
        )
        receipt_target = (group[0][1] + group[-1][1]) // 2
        sleep_until_server(receipt_target - one_way_ms, offset_ms)

        frames = []
        for note_index, _, note in group:
            sequence += 1
            down = {
                "heat_ticket_id": ticket,
                "sequence": sequence,
                "ruleset_id": ruleset,
                "note_index": note_index,
                "cue_token": tokens[note_index],
                "action_bits": note["action"] & -note["action"],
            }
            if ruleset == "osu":
                down["pointer_x_u16"] = note["x"]
                down["pointer_y_u16"] = note["y"]
            frames.append(down)
            sequence += 1
            up = dict(down)
            up["sequence"] = sequence
            up["action_bits"] = 0
            frames.append(up)
        arena_input(client, frames)
        if group_index % progress_step == 0 or group_index == len(groups):
            sent = sum(len(item) for item in groups[:group_index])
            log.info(f"Sent {sent}/{len(notes)} live objects")


def solve_heat(client, session, state):
    enrollment = arena_enroll(client)
    ticket = enrollment["heat_ticket_id"]
    log.success(
        f"Enrolled in {enrollment['heat_id']} with commitment "
        f"{state['heat']['commitment'][:16]}..."
    )
    state = wait_for(
        client,
        lambda view: (
            view["heat"]["id"] == enrollment["heat_id"]
            and view["heat"].get("selection") is not None
        ),
        "selection reveal",
    )
    heat = state["heat"]
    selection = heat["selection"]
    ruleset = selection["ruleset"]
    log.success(
        f"Rotation wave {selection['series_leg']}/4: "
        f"{selection['song_title']} [{selection['difficulty']}] "
        f"{selection['star_rating']:.2f}★ / {ruleset}"
    )

    archive_path = (
        "/api/v1/beatmaps/"
        + urllib.parse.quote(selection["song_id"], safe="")
        + ".osz"
    )
    archive = client.public_bytes(archive_path)
    notes = parse_exact_chart(archive, selection)
    ready = arena_ready(
        client,
        ticket,
        modifier_envelope(selection["nonce"], APPLIED_MODS[ruleset]),
    )
    locked = ready["locked_mods"]
    log.success(
        f"Locked {locked['acronym']} at {locked['rate_milli'] / 1000:.2f}x "
        f"with native {locked['score_multiplier_milli'] / 1000:.3f}x score"
    )

    offset, one_way = measure_server_clock(client)
    log.info(
        f"Clock offset {offset:+d} ms; estimated one-way latency {one_way} ms"
    )
    # Some pinned maps have a scoreable object at chart time zero. Do not
    # race the coordinator's reveal -> running transition by sleeping only to
    # the nominal start timestamp; wait until the authenticated state machine
    # has actually entered the running phase first. The first cue remains
    # valid through its normal post-target window.
    wait_for(
        client,
        lambda view: (
            view["heat"]["id"] == enrollment["heat_id"]
            and view["heat"]["phase"] == "running"
        ),
        "the shared start",
    )
    play_live_transcript(client, heat, ticket, notes, offset, one_way)

    wait_for(
        client,
        lambda view: (
            view["heat"]["id"] == enrollment["heat_id"]
            and view["heat"]["phase"] == "submission"
        ),
        "submission window",
    )
    result = arena_result(client, ticket)
    judge = result["judge"]
    log.success(
        f"Judge: {judge['hit_count']}/{judge['note_count']} hits, "
        f"{judge['accuracy'] * 100:.3f}% accuracy, "
        f"raw basis {judge['raw_basis']}"
    )

    claim = {
        "acronym": "HT",
        "rate_milli": 750,
        "settings_hash": EMPTY_SETTINGS_HASH,
    }
    settlement = arena_finish(
        client,
        ready["ready_capsule"],
        result["judge_capsule"],
        claim,
    )["settlement"]
    if not settlement["eligible"]:
        raise RuntimeError(
            f"leg was not eligible: {settlement.get('reason')}"
        )
    log.success(
        f"Policy claim {claim['acronym']} settled at "
        f"{settlement['settled_score']:,} "
        f"({settlement['multiplier']:.3f}x)"
    )

    final = wait_for(
        client,
        lambda view: (
            view["heat"]["id"] == enrollment["heat_id"]
            and view["heat"]["phase"] == "results"
        ),
        "leg settlement",
    )
    return final, settlement


def solve(
    host="localhost",
    port=1337,
    scheme="http",
    team_id="solver-crew",
    team_name="Patch Crew",
    token=None,
    debug=False,
):
    base_url = f"{scheme}://{host}:{port}"
    client = ArenaClient(base_url, debug=debug)
    try:
        log.info(f"Connecting to {base_url}")
        session = client.create_session(team_id, team_name, team_token=token)
        state = wait_for(
            client,
            lambda view: view["heat"]["phase"] == "enrollment",
            "the next scoring-wave enrollment",
        )
        state, settlement = solve_heat(client, session, state)
        if state.get("crown_owner_id") != session["team_id"]:
            raise RuntimeError(
                "the official wave completed, but another team posted a "
                "higher score or matched an exact top-score tie"
            )
        score = settlement["settled_score"]
        log.success(f"Wave Crown captured with official score {score:,}")
        return score
    finally:
        client.token = None


def main():
    parser = argparse.ArgumentParser(
        description="Solve the Rythme KOTH challenge"
    )
    parser.add_argument("--host", default="localhost", help="target host")
    parser.add_argument("--port", type=int, default=1337, help="target port")
    parser.add_argument(
        "--scheme", choices=("http", "https"), default="http"
    )
    parser.add_argument("--team", default="solver-crew", help="team identifier")
    parser.add_argument("--name", default="Patch Crew", help="crew display name")
    parser.add_argument("--token", help="rCTF team token for event onboarding")
    parser.add_argument("--debug", action="store_true", help="log protocol bodies")
    args = parser.parse_args()

    context.log_level = "debug" if args.debug else "info"
    try:
        solve(
            host=args.host,
            port=args.port,
            scheme=args.scheme,
            team_id=args.team,
            team_name=args.name,
            token=args.token,
            debug=args.debug,
        )
    except (
        RuntimeError,
        OSError,
        KeyError,
        ValueError,
        zipfile.BadZipFile,
    ) as error:
        log.failure(str(error))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
