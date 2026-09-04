"""Small, dependency-free A&D checker harness."""

from dataclasses import dataclass
import os
import secrets
from ipaddress import ip_address
from typing import Callable


class Mumble(Exception):
    """The target answered with an invalid response."""


class Offline(Exception):
    """The target did not complete a bounded request."""


@dataclass(frozen=True)
class AdContext:
    target_ip: str
    target_port: int
    round_number: int
    challenge_id: int
    participation_id: int
    flag: str


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"missing {name}")
    return value


def load_context() -> AdContext:
    if _required("RSCTF_ACTION") != "check":
        raise ValueError("unsupported action")
    port = int(_required("RSCTF_TARGET_PORT"))
    if not 1 <= port <= 65535:
        raise ValueError("invalid target port")
    return AdContext(
        target_ip=str(ip_address(_required("RSCTF_TARGET_IP"))),
        target_port=port,
        round_number=int(_required("RSCTF_ROUND")),
        challenge_id=int(_required("RSCTF_CHALLENGE_ID")),
        participation_id=int(_required("RSCTF_TEAM_ID")),
        flag=_required("RSCTF_FLAG"),
    )


_checks: list[Callable[[AdContext], None]] = []


def checker(function: Callable[[AdContext], None]) -> Callable[[AdContext], None]:
    _checks.append(function)
    return function


def run_ad_checker() -> int:
    try:
        context = load_context()
        checks = list(_checks)
        for index in range(len(checks) - 1, 0, -1):
            selected = secrets.randbelow(index + 1)
            checks[index], checks[selected] = checks[selected], checks[index]
        failures: list[BaseException] = []
        for function in checks:
            try:
                function(context)
            except BaseException as error:
                failures.append(error)
        if failures:
            if any(not isinstance(error, (Offline, Mumble)) for error in failures):
                return 3
            if any(isinstance(error, Offline) for error in failures):
                return 2
            return 1
        return 0
    except BaseException:
        return 3
