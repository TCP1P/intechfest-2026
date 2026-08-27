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

`freezed` renders the real file with syntax highlighting and annotations. Use `--circle` for
one vulnerable region and repeated `--mark` options for several related regions. Select code
by quoted text when practical so the command survives harmless line movement.

```sh
freezed path/to/source.py \
  --lines 20,45 \
  --show-line-numbers \
  --window \
  --theme github-dark \
  --padding 20 \
  --margin 26 \
  --title 'player-visible/path/to/source.py' \
  --circle 'vulnerable_call(user_input)' \
  -o solution/assets/vuln-input-flow.png
```

Record the exact command in the writeup's `Evidence` section. Render the exact source bytes
from the recorded revision. For a player-facing writeup, source figures may use only files the
player receives or earns. An internal organizer writeup may show author source under `Why it
works`, but must label it author-only and must not use it to justify player discovery.

After source changes, regenerate the image and inspect it again. A generated image is not
frozen merely because `freezed` exited successfully.
