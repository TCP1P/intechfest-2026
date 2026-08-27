#!/usr/bin/env python3
"""Run the Rythme reference exploit against one issued arena."""

import argparse
import zipfile

from pwn import context, log

from exploit import solve as run_exploit


def solve(host, port, scheme, team, name, token, debug=False):
    """Complete one scoring heat and return the settled score."""
    return run_exploit(
        host=host,
        port=port,
        scheme=scheme,
        team_id=team,
        team_name=name,
        token=token,
        debug=debug,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="issued arena host")
    parser.add_argument("--port", required=True, type=int, help="issued arena port")
    parser.add_argument("--scheme", choices=("http", "https"), default="https")
    parser.add_argument("--team", default="solver-crew", help="local-mode team identifier")
    parser.add_argument("--name", default="Patch Crew", help="local-mode display name")
    parser.add_argument("--token", help="current rsctf KotH capability")
    parser.add_argument("--debug", action="store_true", help="show protocol diagnostics")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.scheme == "https" and not args.token:
        parser.error("--token is required for event onboarding")

    context.log_level = "debug" if args.debug else "info"
    try:
        score = solve(
            args.host,
            args.port,
            args.scheme,
            args.team,
            args.name,
            args.token,
            args.debug,
        )
    except (RuntimeError, OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        log.failure(str(error))
        return 1
    print(f"settled score: {score}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
