#!/usr/bin/env python3
"""Bounded BYOC checks for Yani-netlink."""

import hashlib
import re
import socket
import struct

from lib import AdContext, Mumble, Offline, checker, run_ad_checker


TIMEOUT = 3.0
MAX_RESPONSE = 4096
PORT = 31337
BANNER = b"YANINEKO  /\\_/\\\\  (=^.^=)y-~~\n"
IDENTITY = b"YANINEKO netfilter=ipv4 kernel=7.2.3\n"
MEMORY_IDENTITY = b"YANINEKO memory identity kernel=7.2.3\n"
INIT_SHA256 = "1beaf1503abc4cd2faf6b2af7ce0f016f3cf61c23b50be6419c3ad526f68602d"
KERNEL_LINK_TEXT = 0xFFFFFFFF81000000
KERNEL_LINK_MEMORY_IDENTITY = 0xFFFFFFFF82574E60
KERNEL_LINK_PREPARE = 0xFFFFFFFF812CAB40
KERNEL_LINK_COMMIT = 0xFFFFFFFF812CA890
KERNEL_LINK_INIT_TASK = 0xFFFFFFFF82C0CA00
IMMUTABLE_CODE_CHECKS = (
    (KERNEL_LINK_TEXT, 128, "d97893ed7a4e12bf79dd0ac7ef9bdc76b3526e2c2bcbbd3a2d6d890d3bdd480c"),
    (0xFFFFFFFF8205A730, 128, "95e717fde1b6579767cbd15eaff01150fd258a3914398a8818632c6788bea0e0"),
    (0xFFFFFFFF8205A900, 128, "e0694ee0a917f82f402d4708207644462b9eda4fa7702f045f8ef88fa7d4f7cf"),
)
CONTRACT = (
    b"YANINEKO Yani-netlink kernel relay\n"
    b"transport=tcp,udp\n"
    b"port=31337\n"
)
SMOKING_MESSAGE_BYTES = 361
SMOKING_MESSAGE_SHA256 = "99b4249080b3d7d74717786ae4c044bf97348142eabf482f09a671770f77dc94"
INFO_PATTERN = re.compile(
    rf"INFO kernel=7\.2\.3 port={PORT} netfilter=ipv4 "
    r"smep=0 smap=0 mem_start=0x([0-9a-f]+) mem_length=([0-9]+)"
)
CRITICAL_FILE_WHITELIST = {
    "/proc/sys/vm/mmap_min_addr": hashlib.sha256(b"0\n").hexdigest(),
    "/proc/sys/kernel/randomize_va_space": hashlib.sha256(b"2\n").hexdigest(),
    "/etc/yani/contract": hashlib.sha256(CONTRACT).hexdigest(),
    "/proc/yani": hashlib.sha256(IDENTITY).hexdigest(),
    "/init": INIT_SHA256,
}


def _line(data: bytes) -> str:
    if len(data) > MAX_RESPONSE:
        raise Mumble("response was too large")
    if not data.endswith(b"\n"):
        raise Mumble("response was not newline terminated")
    try:
        return data[:-1].decode("utf-8")
    except UnicodeDecodeError as error:
        raise Mumble("response was not UTF-8") from error


def _connect(context: AdContext):
    try:
        sock = socket.create_connection(
            (context.target_ip, context.target_port), TIMEOUT
        )
        stream = sock.makefile("rwb", buffering=0)
        if stream.readline(len(BANNER) + 1) != BANNER:
            stream.close()
            sock.close()
            raise Mumble("Yani Neko banner was missing")
        return sock, stream
    except Mumble:
        raise
    except (ConnectionError, OSError, TimeoutError) as error:
        raise Offline("TCP request did not complete") from error


def request_tcp(context: AdContext, command: str) -> str:
    if "\r" in command or "\n" in command:
        raise ValueError("checker command must be one line")
    sock, stream = _connect(context)
    try:
        stream.write(command.encode("ascii") + b"\n")
        return _line(stream.readline(MAX_RESPONSE + 1))
    except (ConnectionError, OSError, TimeoutError) as error:
        raise Offline("TCP request did not complete") from error
    finally:
        stream.close()
        sock.close()


def request_tcp_batch(context: AdContext, commands: tuple[str, ...]) -> list[str]:
    sock, stream = _connect(context)
    responses: list[str] = []
    try:
        for command in commands:
            if "\r" in command or "\n" in command:
                raise ValueError("checker command must be one line")
            stream.write(command.encode("ascii") + b"\n")
            responses.append(_line(stream.readline(MAX_RESPONSE + 1)))
        return responses
    except (ConnectionError, OSError, TimeoutError) as error:
        raise Offline("TCP batch did not complete") from error
    finally:
        stream.close()
        sock.close()


def digest_flag_image(flag: str) -> str:
    raw = flag.encode("utf-8") + b"\n"
    return hashlib.sha256(raw.ljust(4096, b"\0")).hexdigest()


def parse_info(info: str) -> tuple[int, int]:
    match = INFO_PATTERN.fullmatch(info)
    if not match:
        raise Mumble("INFO or SMEP/SMAP policy failed")
    address = int(match.group(1), 16)
    length = int(match.group(2), 10)
    if not address or length != len(MEMORY_IDENTITY):
        raise Mumble("memory whitelist metadata was invalid")
    return address, length


def parse_leak(leak: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"LEAK [0-9a-f]{192}", leak):
        raise Mumble("EAT leak shape was invalid")
    data = bytes.fromhex(leak[5:])
    return struct.unpack_from("<QQQ", data, 64)


@checker
def check_info_and_cpu_policy(context: AdContext) -> None:
    parse_info(request_tcp(context, "YANINEKO INFO"))


@checker
def check_tcp_ping(context: AdContext) -> None:
    if request_tcp(context, "YANINEKO PING") != "PONG":
        raise Mumble("TCP PING failed")


@checker
def check_mode_capabilities(context: AdContext) -> None:
    requests = (
        ("EAT", "YANINEKO EAT 1", "LEAK 41"),
        ("PUKE", "YANINEKO PROBE PUKE 41", "PROBE_OK PUKE"),
        ("SMOKING", "YANINEKO PROBE SMOKING", "PROBE_OK SMOKING"),
        (
            "CHECK",
            "YANINEKO CHECK /proc/yani",
            f"CHECK_OK {CRITICAL_FILE_WHITELIST['/proc/yani']}",
        ),
        ("Mrrph", "YANINEKO Mrrph", "Mrrph_ERR -13"),
    )
    for mode, command, expected in requests:
        if request_tcp(context, command) != expected:
            raise Mumble(f"{mode} capability failed")


@checker
def check_smoking_output(context: AdContext) -> None:
    response = request_tcp(context, "YANINEKO SMOKING")
    encoded = response.encode("utf-8")
    if (
        len(encoded) != SMOKING_MESSAGE_BYTES
        or hashlib.sha256(encoded).hexdigest() != SMOKING_MESSAGE_SHA256
    ):
        raise Mumble("SMOKING dummy response failed")
    if request_tcp(context, "YANINEKO SMOKING not-a-number") != "SMOKING_ERR -EINVAL":
        raise Mumble("invalid SMOKING argument was accepted")
    if request_tcp(context, "YANINEKO SMOKING 0") != "SMOKING_ERR -22":
        raise Mumble("null SMOKING function was not rejected")


@checker
def check_byte_probe(context: AdContext) -> None:
    commands = tuple(
        f"YANINEKO PROBE PUKE {value:02x}" for value in range(256)
    )
    responses = request_tcp_batch(context, commands)
    if responses != ["PROBE_OK PUKE"] * 256:
        raise Mumble("byte probe rejected a safe value")


@checker
def check_critical_files(context: AdContext) -> None:
    for path, expected in CRITICAL_FILE_WHITELIST.items():
        command = f"YANINEKO CHECK {path}"
        if request_tcp(context, command) != f"CHECK_OK {expected}":
            raise Mumble(f"critical file hash failed: {path}")
    expected_flag = digest_flag_image(context.flag)
    if request_tcp(context, "YANINEKO CHECK /flag") != f"CHECK_OK {expected_flag}":
        raise Mumble("current flag hash failed")


@checker
def check_memory_whitelist(context: AdContext) -> None:
    address, length = parse_info(request_tcp(context, "YANINEKO INFO"))
    expected = hashlib.sha256(MEMORY_IDENTITY).hexdigest()
    command = f"YANINEKO CHECKMEM 0x{address:x} {length}"
    if request_tcp(context, command) != f"MEMCHECK_OK {expected}":
        raise Mumble("critical memory hash failed")


@checker
def check_kernel_base_and_code(context: AdContext) -> None:
    identity_address, _ = parse_info(request_tcp(context, "YANINEKO INFO"))
    prepare, commit, init_task = parse_leak(request_tcp(context, "YANINEKO EAT 96"))
    data_slide = identity_address - KERNEL_LINK_MEMORY_IDENTITY
    text_slide = prepare - KERNEL_LINK_PREPARE
    if (
        data_slide < 0
        or text_slide < 0
        or data_slide != text_slide
        or data_slide & 0x1FFFFF
        or commit - KERNEL_LINK_COMMIT != text_slide
        or init_task - KERNEL_LINK_INIT_TASK != data_slide
    ):
        raise Mumble("kernel relocation was invalid")
    for link_address, length, expected in IMMUTABLE_CODE_CHECKS:
        runtime_address = link_address + text_slide
        command = f"YANINEKO CHECKMEM 0x{runtime_address:x} {length}"
        if request_tcp(context, command) != f"MEMCHECK_OK {expected}":
            raise Mumble("immutable kernel code hash failed")


if __name__ == "__main__":
    raise SystemExit(run_ad_checker())
