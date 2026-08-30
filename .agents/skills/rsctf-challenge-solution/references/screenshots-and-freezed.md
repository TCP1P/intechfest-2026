# Screenshots and freezed figures

Use an image only when it proves a visual state or makes a source relationship easier to
understand than text. Plain text protocols should normally keep genuine terminal output in
the README instead.

## Evidence rules

- Capture the current revision through the same route, identity, and permissions available at
  that writeup stage.
- Never fabricate terminal output, mock browser state, or hand-edit source to make a cleaner
  image. Redact live flags, tokens, production hosts, and unrelated personal data.
- Record the filename, source command or route, active identity, prerequisites, UTC time, and
  fact the image proves before capturing it.
- Store only referenced images under `solution/assets/`. Use descriptive names such as
  `ui-login.png`, `terminal-solve.png`, or `vuln-auth-check.png`.
- Open every final image at readable scale. Check clipping, stale controls, wrong identities,
  hidden secrets, and mismatched source before marking the writeup frozen.

## Annotated source with freezed

The `--arrow` examples require the TCP1P Freezed fork at commit
[`7acde4f1f02abda8224b740a722b32652d1907a2`](https://github.com/dimasma0305/freezed/commit/7acde4f1f02abda8224b740a722b32652d1907a2).
No tagged Freezed release contains this feature. Build and install that exact checkout under
the `freezed` binary name, then fail fast before rendering:

```sh
freezed --help | grep -F -- '--arrow' >/dev/null
```

Do not substitute the upstream `freeze` binary or an older local `freezed`; both can reject
the documented option.

`freezed` renders the real file with syntax highlighting and annotations. Use `--arrow` to
point from a right-hand gutter to one vulnerable region without covering the source. Repeat
`--arrow` for independent targets, or use repeated `--mark` options when numbered
relationships are clearer. A selector may be an absolute line (`12`), an inclusive range
(`12,14`), unique quoted source text, or a text-delimited block (`start..end`). Prefer quoted
source text when practical so the command survives harmless line movement.

```sh
freezed path/to/source.py \
  --lines 20,45 \
  --show-line-numbers \
  --window \
  --theme github-dark \
  --padding 20 \
  --margin 26 \
  --title 'player-visible/path/to/source.py' \
  --arrow 'vulnerable_call(user_input)' \
  -o solution/assets/vuln-input-flow.png
```

Record the exact command in the writeup's `Evidence` section. Render the exact source bytes
from the recorded revision. For a player-facing writeup, source figures may use only files the
player receives or earns. An internal organizer writeup may show author source under `Why it
works`, but must label it author-only and must not use it to justify player discovery.

After source changes, regenerate the image and inspect it again. A generated image is not
frozen merely because `freezed` exited successfully.
