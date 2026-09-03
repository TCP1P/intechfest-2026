import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [app, html, styles] = await Promise.all([
  readFile(new URL("./static/app.js", import.meta.url), "utf8"),
  readFile(new URL("./static/index.html", import.meta.url), "utf8"),
  readFile(new URL("./static/styles.css", import.meta.url), "utf8"),
]);

test("matchmaking entry uses labeled semantic controls and live status", () => {
  assert.match(html, /<form id="room-form" novalidate>/);
  assert.match(html, /<label for="player-name">Display name<\/label>/);
  assert.match(html, /<label for="capability">Current KotH capability<\/label>/);
  assert.match(html, /id="status"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(html, /<button id="join-queue"[^>]*type="submit">/);
  assert.doesNotMatch(html, /room code|create a room|add npc/i);
});

test("browser implements queue, reconnect, state, and roll lifecycle", () => {
  for (const endpoint of [
    "/api/matchmaking",
    "/api/matchmaking/state",
    "/api/matchmaking/roll",
  ]) {
    assert.ok(app.includes(endpoint), `missing ${endpoint}`);
  }
  assert.doesNotMatch(app, /\/api\/rooms|roomCode|addNpc|start_room/);
  assert.match(app, /localStorage\.setItem\(SAVED_ROOM_KEY/);
  assert.match(app, /serpent-circuit-match-v6/);
  assert.match(app, /raw\.protocolVersion !== 6/);
  assert.match(app, /raw\.minPlayers !== 4/);
  assert.match(app, /raw\.matchSeconds !== 300/);
  assert.match(app, /playerToken: state\.playerToken/);
  assert.doesNotMatch(app, /localStorage\.setItem\([^\n]*capability/i);
  assert.match(app, /elements\.capability\.value = ""/);
  assert.match(app, /crypto\.getRandomValues\(bytes\)/);
});

test("classic board artwork and live pieces derive from server state", () => {
  assert.match(app, /function boardCoordinates\(position\)/);
  assert.match(app, /function renderLadder\(layer, transition\)/);
  assert.match(app, /function renderSnake\(layer, transition\)/);
  assert.match(app, /for \(const transition of state\.rules\.transitions\)/);
  assert.match(app, /cell\.dataset\.position = String\(number\)/);
  assert.match(app, /for \(const \[index, player\] of state\.room\.players\.entries\(\)\)/);
  assert.match(styles, /\.transition-layer\s*{/);
  assert.match(styles, /\.piece-coral\s*{/);
  assert.match(styles, /\.piece-blue\s*{/);
});

test("queue and turn state are announced without relying only on color", () => {
  assert.match(html, /id="last-move"[^>]*aria-live="polite"/);
  assert.match(app, /tags\.push\("Current turn"\)/);
  assert.match(app, /tags\.push\("Winner"\)/);
  assert.match(app, /room\.queueAhead > 0/);
  assert.match(app, /Match starts automatically/);
  assert.match(app, /Match starts in \$\{seconds\}s/);
  assert.match(app, /current\.isNpc/);
  assert.match(app, /state\.room\.matchDeadline/);
  assert.match(app, /move\.automatic \? "auto-rolled" : "rolled"/);
  assert.match(app, /piece\.textContent = String\(index \+ 1\)/);
});

test("validation exposes invalid fields and keyboard focus remains visible", () => {
  assert.match(app, /elements\.capability\.setAttribute\("aria-invalid"/);
  assert.match(app, /elements\.playerName\.setAttribute\("aria-invalid"/);
  assert.match(styles, /input\[aria-invalid="true"\]/);
  assert.match(styles, /button:focus-visible,[\s\S]*?outline: 3px solid/);
  assert.doesNotMatch(styles, /outline:\s*none/);
});

test("compact layout keeps DOM order and supports reduced and forced colors", () => {
  assert.ok(html.indexOf('class="panel match-sidebar"') < html.indexOf('class="panel board-panel"'));
  const compactStart = styles.indexOf("@media (max-width: 68rem)");
  const narrowStart = styles.indexOf("@media (max-width: 38rem)");
  assert.ok(compactStart >= 0 && compactStart < narrowStart);
  assert.doesNotMatch(styles.slice(compactStart, narrowStart), /grid-row\s*:/);
  assert.match(styles, /min-width: 20rem/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(styles, /@media \(forced-colors: active\)[\s\S]*?\.transition-layer[\s\S]*?display: none/);
});
