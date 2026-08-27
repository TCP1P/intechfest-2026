#!/usr/bin/env python3
"""Run the Banana Confirmed reference client against one issued arena."""

import argparse
import subprocess
from pathlib import Path
from threading import Timer


BUILD_TIMEOUT_SECONDS = 900
GOAL = "kill confirmed"


def resolve_binary(binary: Path | None) -> Path:
    """Return a supplied headless client or build the repository copy."""
    if binary is not None:
        result = binary.resolve()
        if not result.is_file():
            raise FileNotFoundError(f"headless client not found: {result}")
        return result

    game = Path(__file__).resolve().parents[1] / "src" / "game"
    print("[*] Building the protocol-v13 confirmation client", flush=True)
    subprocess.run(
        ["cargo", "build", "--locked", "--release", "-p", "headless-client"],
        cwd=game,
        check=True,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    result = game / "target" / "release" / "headless-client"
    if not result.is_file():
        raise FileNotFoundError(f"build completed without {result}")
    return result


def solve(host, port, key, cert_hash, timeout, binary):
    """Run until the authoritative client reports a confirmed kill."""
    command = [
        str(binary),
        "--remote", f"{host}:{port}",
        "--count", "1",
        "--npc-count", "1",
        "--npc-name", "banana-confirm",
        "--keys", key,
    ]
    command.extend(["--cert-hash", cert_hash] if cert_hash else ["--insecure"])

    print(f"[*] Connecting to {host}:{port}; waiting for '{GOAL}'", flush=True)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    timer = Timer(timeout, process.kill)
    timer.start()
    found = False
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            if GOAL in line.lower():
                found = True
                break
    finally:
        timer.cancel()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    if not found:
        raise RuntimeError("client exited or timed out before producing proof")
    return f"Observed {GOAL}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="issued arena host")
    parser.add_argument("--port", required=True, type=int, help="issued arena UDP port")
    parser.add_argument("--key", required=True, help="issued per-match arena key")
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument("--cert-hash", help="issued server certificate SHA-256")
    trust.add_argument("--insecure", action="store_true", help="local testing only")
    parser.add_argument("--binary", type=Path, help="prebuilt headless-client")
    parser.add_argument("--timeout", type=float, default=90, help="maximum solve seconds")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        result = solve(
            args.host,
            args.port,
            args.key,
            args.cert_hash,
            args.timeout,
            resolve_binary(args.binary),
        )
    except (FileNotFoundError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"solve failed: {error}\n")
    print(f"[+] {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
