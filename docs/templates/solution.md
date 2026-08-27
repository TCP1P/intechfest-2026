# [Challenge name] solution

> Organizer material. Anyone with repository access can read this file. Keep it out of
> player handouts, service images, and blind playtests. Redact live secrets.

## Verification record

- Status: `draft` or `frozen`
- Revision: `<full Git commit SHA>`
- Challenge type: `<manifest type>`
- Delivery: `service`, `handout`, `hybrid`, or `byoc`
- Artifact or image: `<SHA-256, image digest, or not applicable>`
- Tested at: `<UTC timestamp>`
- Tested by: `<name>`
- Command: `<exact solve.py command>`
- Result: `<exit status and redacted result>`

Use `frozen` only after this exact committed revision and every retained image pass.

## Summary

Explain the objective and intended solve in two or three plain sentences.

## Player inputs

- Description: `<exact public copy or a faithful short quote>`
- Target: `<issued URL or host/port, if any>`
- Files: `<filenames and SHA-256 values, if any>`
- Credentials or rules: `<only player-visible values>`
- Not supplied: `<source, admin access, container access, or other boundaries>`

## Walkthrough

### 1. [First meaningful step]

- Observation: `<what the player can see now>`
- Hypothesis: `<what that evidence suggests>`
- Action: `<exact command, request, input, or UI action>`
- Result: `<genuine trimmed output or visible state>`
- Capability gained: `<new access, knowledge, primitive, or artifact>`

Repeat only for real steps. Keep them chronological and do not fill gaps with author knowledge.

## Why it works

Explain the implementation behavior in plain language. Label author-only source evidence and
do not pretend the player had it during discovery.

## Solver

Run the tracked [`solve.py`](solve.py) with explicit player inputs:

```console
$ python3 solution/solve.py <arguments>
<genuine trimmed output with the flag redacted as rsctf{...}>
```

List exact dependency pins only when the standard library is insufficient.

## Evidence

Prefer text for text protocols. Add a screenshot only when it proves a visual state or makes
source relationships materially clearer. Store it under `assets/`, link it exactly once, and
record the capture or `freezed` command. For example:

```sh
freezed path/to/source.py --lines 20,45 --show-line-numbers --window \
  --theme github-dark --title 'path/to/source.py' \
  --circle 'vulnerable_call(user_input)' \
  -o solution/assets/vuln-input-flow.png
```

If no image helps, say why instead of adding a decorative screenshot.

## Notes

- Cleanup or reset behavior:
- Important negative or unintended-path checks:
- Known limitations or environment requirements:
- Evidence that must be regenerated after a change:
