# solve.py format

The reference solver should look like the shortest clear implementation a teammate would
write after understanding the challenge.

## Required shape

```python
#!/usr/bin/env python3
"""One-line description of the solve."""

import argparse


def solve(target):
    """Perform the player-visible solve and return the result."""
    ...


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    print(solve(args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Adapt argument names to the contract: use `--url`, `--host` and `--port`, `--input`, or
challenge-specific public values. Put the solve in `solve()`; keep `main()` limited to
arguments, one call, concise output, and exit status.

## Simplicity rules

- Prefer the Python standard library. Add a dependency only when it makes the exploit
  materially clearer, then pin the direct version exactly in `requirements.txt`.
- Use one linear exploit path and at most a few small helpers named after real protocol
  stages. Avoid classes, frameworks, config layers, generic retry systems, plugin patterns,
  banners, emoji, and debug modes unless the challenge genuinely needs them.
- Accept player-visible inputs. Never hard-code an author host, production target, live flag,
  container name, organizer API, hidden route, or checker variable.
- Bound network timeouts, reads, loops, subprocesses, and brute-force ranges.
- Validate the expected result and exit nonzero on failure. Catch only errors that can be
  explained more clearly; do not hide bugs behind a broad `except Exception`.
- Close resources with context managers. Print the final flag or KotH success evidence once.
- Keep code and README commands byte-for-byte consistent. A normal solver should remain well
  under 100 lines; exceed that only when the exploit itself requires it.

Run `python3 -m py_compile solution/solve.py` and exercise the exact documented command before
freezing the writeup.
