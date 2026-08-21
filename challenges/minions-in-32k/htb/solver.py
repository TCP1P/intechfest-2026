#!/usr/bin/env python3
"""Run the author-side Banana Confirmed reference client against an arena."""

from __future__ import annotations

import argparse
import selectors
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    challenge_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build and run the protocol-v13 confirmation solver"
    )
    parser.add_argument("--host", default="127.0.0.1", help="arena host")
    parser.add_argument("--port", type=int, default=4433, help="arena UDP port")
    parser.add_argument("--key", help="per-match arena key")
    trust = parser.add_mutually_exclusive_group()
    trust.add_argument("--cert-hash", help="server certificate SHA-256 (hex)")
    trust.add_argument(
        "--insecure",
        action="store_true",
        help="accept any certificate (local testing only)",
    )
    parser.add_argument(
        "--game-root",
        type=Path,
        default=challenge_root / "game",
        help="game workspace containing Cargo.toml",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        help="prebuilt headless-client; otherwise build the release binary",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="maximum seconds to wait for proof",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def resolve_binary(args: argparse.Namespace) -> Path:
    if args.binary:
        binary = args.binary.resolve()
        if not binary.is_file():
            raise FileNotFoundError(f"headless client not found: {binary}")
        return binary

    game_root = args.game_root.resolve()
    manifest = game_root / "Cargo.toml"
    if not manifest.is_file():
        raise FileNotFoundError(f"Cargo workspace not found: {manifest}")
    print("[*] Building the protocol-v13 confirmation client", flush=True)
    subprocess.run(
        ["cargo", "build", "--locked", "--release", "-p", "headless-client"],
        cwd=game_root,
        check=True,
    )
    binary = game_root / "target" / "release" / "headless-client"
    if not binary.is_file():
        raise FileNotFoundError(f"build completed without {binary}")
    return binary


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_solver(args: argparse.Namespace, binary: Path) -> bool:
    command = [
        str(binary),
        "--remote",
        f"{args.host}:{args.port}",
        "--count",
        "1",
        "--npc-count",
        "1",
        "--npc-name",
        "banana-confirm",
    ]
    if args.key:
        command.extend(["--keys", args.key])
    if args.cert_hash:
        command.extend(["--cert-hash", args.cert_hash])
    elif args.insecure:
        command.append("--insecure")

    goal = "kill confirmed"
    print(f"[*] Connecting to {args.host}:{args.port}; waiting for '{goal}'", flush=True)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + args.timeout
    success = False
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            for key, _ in selector.select(timeout=min(1.0, remaining)):
                line = key.fileobj.readline()
                if not line:
                    continue
                print(line, end="", flush=True)
                if goal in line.lower():
                    success = True
                    return True
            if process.poll() is not None:
                break
        return False
    finally:
        selector.close()
        stop_process(process)
        if success:
            print(f"[+] Observed {goal}", flush=True)


def main() -> int:
    args = parse_args()
    try:
        binary = resolve_binary(args)
        if run_solver(args, binary):
            return 0
        print("[-] Solver exited or timed out before producing proof", file=sys.stderr)
        return 1
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
