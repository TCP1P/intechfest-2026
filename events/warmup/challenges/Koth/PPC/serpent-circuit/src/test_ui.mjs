import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [app, html, styles] = await Promise.all([
  readFile(new URL("./static/app.js", import.meta.url), "utf8"),
  readFile(new URL("./static/index.html", import.meta.url), "utf8"),
  readFile(new URL("./static/styles.css", import.meta.url), "utf8"),
]);

test("compact layout preserves DOM and keyboard order without startup focus stealing", () => {
  assert.ok(html.indexOf('class="play-column"') < html.indexOf('class="board-column"'));

  const compactStart = styles.indexOf("@media (max-width: 56rem)");
  const compactEnd = styles.indexOf("@media (max-width: 39rem)");
  assert.ok(compactStart >= 0 && compactStart < compactEnd);
  const compactLayout = styles.slice(
    compactStart,
    compactEnd,
  );
  assert.doesNotMatch(compactLayout, /grid-row\s*:/);

  const loadRulesStart = app.indexOf("async function loadRules()");
  const loadRulesEnd = app.indexOf("async function createSession");
  assert.ok(loadRulesStart >= 0 && loadRulesStart < loadRulesEnd);
  const loadRules = app.slice(
    loadRulesStart,
    loadRulesEnd,
  );
  assert.doesNotMatch(loadRules, /elements\.token\.focus\(\)/);
});

test("dice radios expose a forced-colors-safe non-color selection indicator", () => {
  assert.match(app, /choiceIndicator\.setAttribute\("aria-hidden", "true"\)/);
  assert.match(styles, /\.choice-indicator::before\s*{\s*content: "○";/);
  assert.match(
    styles,
    /\.roll-choice:has\(input:checked\) \.choice-indicator::before\s*{\s*content: "✓";/,
  );
  assert.match(styles, /@media \(forced-colors: active\)[\s\S]*?\.choice-indicator/);
});
