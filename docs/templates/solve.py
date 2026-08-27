#!/usr/bin/env python3
"""Reference solver for [challenge name]."""

import argparse


def solve(target):
    """Perform the player-visible solve and return the flag or success evidence."""
    raise NotImplementedError("replace this with the small challenge-specific solve")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="issued target or input")
    args = parser.parse_args()

    try:
        result = solve(args.target)
    except (OSError, ValueError) as error:
        parser.exit(1, f"solve failed: {error}\n")

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
