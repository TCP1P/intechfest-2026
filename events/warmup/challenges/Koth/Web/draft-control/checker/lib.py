"""Protocol-neutral, dependency-free helpers for rsctf process checkers.

Network and protocol code belongs in run.py. Copy this file together with
run.py; Repository Bindings prepares the whole checker directory, so sibling
imports work inside the checker sandbox.
"""

from dataclasses import dataclass
from enum import IntEnum
from functools import wraps
from ipaddress import ip_address
import os
import secrets
from typing import Callable, TypedDict, TypeVar


__all__ = [
    "AdContext",
    "KothContext",
    "Mumble",
    "Offline",
    "ad_checker",
    "checker",
    "koth_checker",
    "run_ad_checker",
    "run_koth_checker",
]


class Verdict(IntEnum):
    OK = 0
    MUMBLE = 1
    OFFLINE = 2
    INTERNAL_ERROR = 3


class Mumble(Exception):
    """The target answered, but its behavior was incorrect."""


class Offline(Exception):
    """The target could not provide a complete response."""


@dataclass(frozen=True)
class TargetContext:
    target_ip: str
    target_port: int
    round_number: int
    challenge_id: int


@dataclass(frozen=True)
class AdContext(TargetContext):
    # RSCTF_TEAM_ID currently contains the participation ID for A&D.
    participation_id: int
    flag: str


@dataclass(frozen=True)
class KothContext(TargetContext):
    pass


class _TargetValues(TypedDict):
    target_ip: str
    target_port: int
    round_number: int
    challenge_id: int


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ValueError(f"missing {name}")
    return value


def _positive_integer(name: str, maximum: int | None = None) -> int:
    value = int(_required(name))
    if value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"invalid {name}")
    return value


def _target_values() -> _TargetValues:
    if _required("RSCTF_ACTION").strip() != "check":
        raise ValueError("unsupported RSCTF_ACTION")
    return {
        "target_ip": str(ip_address(_required("RSCTF_TARGET_IP").strip())),
        "target_port": _positive_integer("RSCTF_TARGET_PORT", 65535),
        "round_number": _positive_integer("RSCTF_ROUND"),
        "challenge_id": _positive_integer("RSCTF_CHALLENGE_ID"),
    }


def _load_ad_context() -> AdContext:
    return AdContext(
        **_target_values(),
        participation_id=_positive_integer("RSCTF_TEAM_ID"),
        # Preserve the expected flag exactly; do not strip it.
        flag=_required("RSCTF_FLAG"),
    )


def _load_koth_context() -> KothContext:
    if int(_required("RSCTF_TEAM_ID")) != 0:
        raise ValueError("KotH checker expects RSCTF_TEAM_ID=0")
    if os.environ.get("RSCTF_FLAG") is not None:
        raise ValueError("KotH checker must not receive RSCTF_FLAG")
    return KothContext(**_target_values())


ContextT = TypeVar("ContextT", AdContext, KothContext)
CheckerFunctionT = TypeVar("CheckerFunctionT", bound=Callable[..., None])
_registered_checkers: list[Callable[..., object]] = []


def _execute(
    function: Callable[[ContextT], None],
    load_context: Callable[[], ContextT],
) -> int:
    try:
        context = load_context()
        result = function(context)
        if result is not None:
            raise TypeError("checker functions must return None")
    except Offline:
        return int(Verdict.OFFLINE)
    except Mumble:
        return int(Verdict.MUMBLE)
    except BaseException:
        # Configuration and checker bugs are infrastructure failures.
        return int(Verdict.INTERNAL_ERROR)
    return int(Verdict.OK)


def checker(function: CheckerFunctionT) -> CheckerFunctionT:
    """Register one focused check for the shuffled checker suite."""
    _registered_checkers.append(function)
    return function


def _shuffled_checkers() -> list[Callable[..., object]]:
    functions = list(_registered_checkers)
    for index in range(len(functions) - 1, 0, -1):
        selected = secrets.randbelow(index + 1)
        functions[index], functions[selected] = functions[selected], functions[index]
    return functions


def _failure_priority(error: BaseException) -> Verdict:
    if isinstance(error, Offline):
        return Verdict.OFFLINE
    if isinstance(error, Mumble):
        return Verdict.MUMBLE
    return Verdict.INTERNAL_ERROR


def _execute_registered(context: ContextT) -> None:
    functions = _shuffled_checkers()
    if not functions:
        raise RuntimeError("no checker functions registered")

    failures: list[BaseException] = []
    for function in functions:
        try:
            result = function(context)
            if result is not None:
                raise TypeError("checker functions must return None")
        except BaseException as error:
            failures.append(error)
    if failures:
        raise max(failures, key=_failure_priority)


def run_ad_checker() -> int:
    """Run every registered A&D check once in shuffled order."""
    return _execute(_execute_registered, _load_ad_context)


def run_koth_checker() -> int:
    """Run every registered KotH check once in shuffled order."""
    return _execute(_execute_registered, _load_koth_context)


def ad_checker(function: Callable[[AdContext], None]) -> Callable[[], int]:
    """Decorate one A&D check with context loading and verdict mapping."""

    @wraps(function)
    def wrapped() -> int:
        return _execute(function, _load_ad_context)

    return wrapped


def koth_checker(function: Callable[[KothContext], None]) -> Callable[[], int]:
    """Decorate one KotH check with context loading and verdict mapping."""

    @wraps(function)
    def wrapped() -> int:
        return _execute(function, _load_koth_context)

    return wrapped
