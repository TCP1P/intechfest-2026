# Draft Control solution

> Organizer material. Never retain a live KotH token in this writeup or its evidence.

## Verification record

- Status: `draft`
- Candidate source revision: `50a78fa14ad8386f29398696a32801f6588af039`;
  freeze only after hidden staging
- Challenge type: `KingOfTheHill`
- Delivery: shared marker service; no player handout
- Artifact or image: local image
  `sha256:8bb297dca66147c3f32e54e0f8b836ccccd716b5674946a1f63fc5431fbee527`
- Backend and target: hardened local Docker, `http://127.0.0.1:32916`
  (ephemeral, stopped after verification); hidden rsctf staging remains pending
- Tested at: `2026-08-30T09:23:43Z`
- Reviewer: Codex author check
- Command: `python3 solution/solve.py --url http://127.0.0.1:32916 --token 'koth_REDACTED'`
- Result: health, read-only checker, marker write, reset, and reclaim passed
  under the 64 MiB runtime limit; solver returned
  `saved ../../koth/king; wait for rsctf attribution`
- Playtest report: pending

## Summary

The update API checks that a relative draft path already names a file, but it never checks
that the resolved path stays under `/srv/pages`. Two parent-directory components reach the
existing `/koth/king` marker. Replacing that file with the team's current token claims the
hill on the next rsctf scoring tick.

## Player inputs

- The issued shared hill URL.
- The team's current KotH token from rsctf.
- The public `GET /api/info`, `GET /api/preview`, and `POST /api/drafts` routes.
- No source, filesystem access, reporter credential, or direct claim endpoint.

## Walkthrough

1. Request `/api/info`. It identifies `/srv/pages` as the draft root and says updates accept
   a relative path for an existing regular file.
2. Preview `brief.txt` to confirm the documented path is interpreted below that root.
3. The KotH objective names `/koth/king`. From `/srv/pages`, the relative path
   `../../koth/king` reaches that existing file.
4. Submit the current team token as the replacement content:

   ```console
   $ curl --fail-with-body --silent \
       -H 'Content-Type: application/json' \
       --data '{"path":"../../koth/king","content":"koth_REDACTED"}' \
       http://127.0.0.1:8080/api/drafts
   {"bytes":13,"path":"../../koth/king","status":"saved"}
   ```

5. Wait for the platform's next marker sample. The HTTP response proves only that the file
   changed; rsctf's attribution proves control.

## Why it works

Author-side [`src/app.py`](../src/app.py) appends the supplied relative path to
`/srv/pages` and checks only that the resulting filesystem path is an existing regular file.
It deliberately omits a containment check after normalization. The marker already exists
and is writable by the non-root service user, so `../../koth/king` passes that policy.

The service writes a temporary file beside the target and uses `os.replace`. This keeps each
marker value atomic while preserving the intended path traversal. The read-only checker never
calls the update route or inspects the marker.

## Solver

Run the tracked [solve.py](solve.py) with the issued URL and current token:

```console
$ python3 solution/solve.py --url http://127.0.0.1:8080 --token 'koth_REDACTED'
saved ../../koth/king; wait for rsctf attribution
```

## Evidence

The save acknowledgement and rsctf marker attribution are sufficient text/state evidence.
A screenshot would expose no additional visual state and could leak a reusable token.

## Notes

- The direct `/claim` route does not exist; the draft update is the intended control path.
- `/api/preview` accepts only the two public page names and cannot disclose the marker.
- Freeze only after the exact image is tested with token rotation, competing team writes,
  stable before/after marker sampling, reset behavior, and a clean blind playtest.
