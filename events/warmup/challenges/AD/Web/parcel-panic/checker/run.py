#!/usr/bin/env python3
"""Read-only functional checks for Parcel Panic."""

import hashlib
import http.client

from lib import AdContext, Mumble, Offline, checker, run_ad_checker


MAX_RESPONSE_BYTES = 4096
REQUEST_TIMEOUT_SECONDS = 3
VAULT_PROOF_PREFIX = b"parcel-panic:v1\0"


def vault_proof(flag: str) -> str:
    return hashlib.sha256(VAULT_PROOF_PREFIX + flag.encode("utf-8")).hexdigest()


def http_get(
    context: AdContext,
    path: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    connection = http.client.HTTPConnection(
        context.target_ip,
        context.target_port,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    request_headers = {
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    if headers:
        request_headers.update(headers)

    try:
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (ConnectionError, TimeoutError, OSError) as error:
        raise Offline("the service did not complete the request") from error
    except http.client.HTTPException as error:
        raise Mumble("the service returned invalid HTTP") from error
    finally:
        connection.close()

    if len(body) > MAX_RESPONSE_BYTES:
        raise Mumble("the service response was too large")
    try:
        return response.status, body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Mumble("the service response was not UTF-8") from error


@checker
def check_health(context: AdContext) -> None:
    if http_get(context, "/health") != (200, "ok\n"):
        raise Mumble("the health endpoint did not return ok")


@checker
def check_normal_parcel(context: AdContext) -> None:
    expected = (
        "Parcel Panic dispatch manifest\n"
        "depot_root=/srv/parcels\n"
        "example=manifest.txt\n"
    )
    if http_get(context, "/parcel?name=manifest.txt") != (200, expected):
        raise Mumble("the normal parcel lookup was incorrect")


@checker
def check_vault_rejects_invalid_proof(context: AdContext) -> None:
    result = http_get(context, "/vault", {"X-Vault-Proof": "0" * 64})
    if result != (403, "forbidden\n"):
        raise Mumble("the vault accepted an invalid proof")


@checker
def check_current_flag(context: AdContext) -> None:
    proof = vault_proof(context.flag)
    result = http_get(context, "/vault", {"X-Vault-Proof": proof})
    if result != (200, f"{context.flag}\n"):
        raise Mumble("the vault did not return this round's flag")


def main() -> int:
    return run_ad_checker()


if __name__ == "__main__":
    raise SystemExit(main())
