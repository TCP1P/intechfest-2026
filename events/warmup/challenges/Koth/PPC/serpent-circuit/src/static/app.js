import { makeServerClock, serverNow } from "/assets/clock.mjs";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const SAVED_ROOM_KEY = "serpent-circuit-match-v5";
const POLL_INTERVAL_MS = 1_000;
const DIE_FACES = ["?", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"];
const VALID_COLORS = new Set(["coral", "blue", "green", "purple"]);

const elements = {
  board: document.querySelector("#board"),
  boardSummary: document.querySelector("#board-summary"),
  capability: document.querySelector("#capability"),
  die: document.querySelector("#die"),
  entryPanel: document.querySelector("#entry-panel"),
  gameAction: document.querySelector("#game-action"),
  gamePanel: document.querySelector("#game-panel"),
  joinQueue: document.querySelector("#join-queue"),
  lastMove: document.querySelector("#last-move"),
  newRoom: document.querySelector("#new-room"),
  playerCount: document.querySelector("#player-count"),
  playerList: document.querySelector("#player-list"),
  playerName: document.querySelector("#player-name"),
  reconnectRoom: document.querySelector("#reconnect-room"),
  roomForm: document.querySelector("#room-form"),
  roomHelp: document.querySelector("#room-help"),
  roomTitle: document.querySelector("#room-title"),
  rulesList: document.querySelector("#rules-list"),
  runtimeBadge: document.querySelector("#runtime-badge"),
  status: document.querySelector("#status"),
  turnCountdown: document.querySelector("#turn-countdown"),
  turnLabel: document.querySelector("#turn-label"),
};

const state = {
  busy: false,
  clock: null,
  lastAnnouncement: "",
  playerToken: null,
  pollTimer: null,
  polling: false,
  room: null,
  rules: null,
};

function setStatus(message, tone = "neutral") {
  elements.status.textContent = message;
  elements.status.dataset.tone = tone;
}

function integer(value, label, minimum, maximum) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`The server returned an invalid ${label}.`);
  }
  return value;
}

function boundedString(value, label, maximum = 128) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new Error(`The server returned an invalid ${label}.`);
  }
  return value;
}

function normalizeRules(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("The server returned invalid game rules.");
  }
  const expected = [
    "boardSize",
    "finishPosition",
    "maxPlayers",
    "matchSeconds",
    "minPlayers",
    "name",
    "objective",
    "protocolVersion",
    "rules",
    "scoringWaveSeconds",
    "startPosition",
    "transitions",
    "turnSeconds",
  ];
  if (Object.keys(raw).sort().join("|") !== expected.sort().join("|")) {
    throw new Error("The server returned a different game contract.");
  }
  if (
    raw.name !== "Serpent Circuit"
    || raw.objective !== "board-progress"
    || raw.protocolVersion !== 5
    || raw.boardSize !== 100
    || raw.startPosition !== 0
    || raw.finishPosition !== 100
    || raw.minPlayers !== 4
    || raw.maxPlayers !== 4
    || raw.turnSeconds !== 30
    || raw.matchSeconds !== 300
    || raw.scoringWaveSeconds !== 60
  ) {
    throw new Error("The server returned a different Serpent Circuit contract.");
  }
  if (!Array.isArray(raw.rules) || raw.rules.length !== 8) {
    throw new Error("The server returned invalid game instructions.");
  }
  const origins = new Set();
  const transitions = raw.transitions.map((transition) => {
    if (
      !transition
      || typeof transition !== "object"
      || Array.isArray(transition)
      || Object.keys(transition).sort().join("|") !== "from|kind|to"
    ) {
      throw new Error("The server returned an invalid board transition.");
    }
    const from = integer(transition.from, "transition origin", 2, 99);
    const to = integer(transition.to, "transition destination", 2, 99);
    if (
      origins.has(from)
      || (transition.kind !== "ladder" && transition.kind !== "snake")
      || (transition.kind === "ladder" && to <= from)
      || (transition.kind === "snake" && to >= from)
    ) {
      throw new Error("The server returned an impossible board transition.");
    }
    origins.add(from);
    return { from, kind: transition.kind, to };
  });
  if (transitions.length !== 14) {
    throw new Error("The server returned the wrong board.");
  }
  return { ...raw, transitions };
}

function normalizePlayer(raw) {
  if (
    !raw
    || typeof raw !== "object"
    || Array.isArray(raw)
    || Object.keys(raw).sort().join("|") !== "color|id|name|position"
  ) {
    throw new Error("The server returned an invalid player.");
  }
  const color = boundedString(raw.color, "player color", 16);
  if (
    !VALID_COLORS.has(color)
  ) {
    throw new Error("The server returned invalid player metadata.");
  }
  return {
    color,
    id: boundedString(raw.id, "player ID"),
    name: boundedString(raw.name, "player name", 48),
    position: integer(raw.position, "player position", 0, 100),
  };
}

function normalizeLastMove(raw, playerIds) {
  if (raw === null) {
    return null;
  }
  if (
    !raw
    || typeof raw !== "object"
    || Array.isArray(raw)
    || Object.keys(raw).sort().join("|")
      !== "automatic|extraTurn|from|playerId|roll|to|transportFrom|transportTo"
    || typeof raw.automatic !== "boolean"
    || typeof raw.extraTurn !== "boolean"
  ) {
    throw new Error("The server returned an invalid last move.");
  }
  const playerId = boundedString(raw.playerId, "moving player ID");
  if (!playerIds.has(playerId)) {
    throw new Error("The server returned a move for an unknown player.");
  }
  const transportFrom = raw.transportFrom === null
    ? null
    : integer(raw.transportFrom, "transport origin", 2, 99);
  const transportTo = raw.transportTo === null
    ? null
    : integer(raw.transportTo, "transport destination", 2, 99);
  if ((transportFrom === null) !== (transportTo === null)) {
    throw new Error("The server returned an incomplete transport.");
  }
  return {
    automatic: raw.automatic,
    extraTurn: raw.extraTurn,
    from: integer(raw.from, "move origin", 0, 100),
    playerId,
    roll: integer(raw.roll, "die roll", 1, 6),
    to: integer(raw.to, "move destination", 0, 100),
    transportFrom,
    transportTo,
  };
}

function normalizeRoom(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("The server returned invalid room state.");
  }
  const expected = [
    "currentPlayerId",
    "lastMove",
    "matchDeadline",
    "maxPlayers",
    "minPlayers",
    "players",
    "queueAhead",
    "scoreStatus",
    "serverTime",
    "status",
    "turnDeadline",
    "turnNumber",
    "winnerId",
    "you",
  ];
  if (Object.keys(raw).sort().join("|") !== expected.sort().join("|")) {
    throw new Error("The server returned a different room contract.");
  }
  if (
    !["waiting", "playing", "finished"].includes(raw.status)
    || raw.minPlayers !== 4
    || raw.maxPlayers !== 4
    || !Array.isArray(raw.players)
    || raw.players.length < 1
    || raw.players.length > 4
    || !raw.you
    || typeof raw.you !== "object"
    || Object.keys(raw.you).join("|") !== "id"
  ) {
    throw new Error("The server returned invalid room metadata.");
  }
  const players = raw.players.map(normalizePlayer);
  const ids = new Set(players.map((player) => player.id));
  const localPlayer = players.find((player) => player.id === raw.you.id);
  if (
    ids.size !== players.length
    || !localPlayer
    || !Number.isSafeInteger(raw.queueAhead)
    || raw.queueAhead < 0
    || raw.queueAhead > 500
    || ![
      "pending",
      "recorded",
      "outside-scoring-window",
    ].includes(raw.scoreStatus)
  ) {
    throw new Error("The server returned an invalid room roster.");
  }
  const currentPlayerId = raw.currentPlayerId === null
    ? null
    : boundedString(raw.currentPlayerId, "current player ID");
  const winnerId = raw.winnerId === null
    ? null
    : boundedString(raw.winnerId, "winner ID");
  if (
    (currentPlayerId !== null && !ids.has(currentPlayerId))
    || (winnerId !== null && !ids.has(winnerId))
    || (raw.status === "playing" && currentPlayerId === null)
    || (raw.status !== "playing" && currentPlayerId !== null)
    || (raw.status !== "finished" && winnerId !== null)
  ) {
    throw new Error("The server returned an inconsistent match state.");
  }
  const turnDeadline = raw.turnDeadline === null
    ? null
    : integer(raw.turnDeadline, "turn deadline", 0, Number.MAX_SAFE_INTEGER);
  if ((raw.status === "playing") !== (turnDeadline !== null)) {
    throw new Error("The server returned an inconsistent turn deadline.");
  }
  const matchDeadline = raw.matchDeadline === null
    ? null
    : integer(raw.matchDeadline, "match deadline", 0, Number.MAX_SAFE_INTEGER);
  if ((raw.status === "playing") !== (matchDeadline !== null)) {
    throw new Error("The server returned an inconsistent match deadline.");
  }
  return {
    ...raw,
    currentPlayerId,
    lastMove: normalizeLastMove(raw.lastMove, ids),
    matchDeadline,
    players,
    serverTime: integer(raw.serverTime, "server time", 0, Number.MAX_SAFE_INTEGER),
    turnDeadline,
    turnNumber: integer(raw.turnNumber, "turn number", 0, 1_000),
    winnerId,
    you: { id: boundedString(raw.you.id, "local player ID") },
  };
}

async function requestJson(path, body = null) {
  const sent = performance.now();
  const options = {
    cache: "no-store",
    headers: { Accept: "application/json" },
  };
  if (body !== null) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const received = performance.now();
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new Error("The game server returned an unexpected response.");
  }
  const raw = await response.text();
  if (raw.length > 64 * 1024) {
    throw new Error("The game server response was too large.");
  }
  let document;
  try {
    document = JSON.parse(raw);
  } catch {
    throw new Error("The game server returned malformed JSON.");
  }
  if (!response.ok) {
    const message = document && typeof document.error === "string"
      ? document.error
      : `Request failed with HTTP ${response.status}.`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return { document, received, sent };
}

function boardCoordinates(position) {
  const index = position - 1;
  const rowFromBottom = Math.floor(index / 10);
  const offset = index % 10;
  const column = rowFromBottom % 2 === 0 ? offset : 9 - offset;
  return {
    x: column * 100 + 50,
    y: (9 - rowFromBottom) * 100 + 50,
  };
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NAMESPACE, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function renderLadder(layer, transition) {
  const start = boardCoordinates(transition.from);
  const end = boardCoordinates(transition.to);
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  const perpendicularX = (-dy / length) * 12;
  const perpendicularY = (dx / length) * 12;
  const group = svgElement("g", { class: "ladder-art" });
  group.append(
    svgElement("line", {
      x1: start.x + perpendicularX,
      y1: start.y + perpendicularY,
      x2: end.x + perpendicularX,
      y2: end.y + perpendicularY,
    }),
    svgElement("line", {
      x1: start.x - perpendicularX,
      y1: start.y - perpendicularY,
      x2: end.x - perpendicularX,
      y2: end.y - perpendicularY,
    }),
  );
  for (let step = 1; step < 7; step += 1) {
    const ratio = step / 7;
    const x = start.x + dx * ratio;
    const y = start.y + dy * ratio;
    group.append(svgElement("line", {
      class: "ladder-rung",
      x1: x + perpendicularX,
      y1: y + perpendicularY,
      x2: x - perpendicularX,
      y2: y - perpendicularY,
    }));
  }
  layer.append(group);
}

function renderSnake(layer, transition) {
  const head = boardCoordinates(transition.from);
  const tail = boardCoordinates(transition.to);
  const dx = tail.x - head.x;
  const dy = tail.y - head.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const bendX = (-dy / length) * Math.min(58, length / 4);
  const bendY = (dx / length) * Math.min(58, length / 4);
  const path = [
    `M ${head.x} ${head.y}`,
    `C ${head.x + dx * 0.25 + bendX} ${head.y + dy * 0.25 + bendY},`,
    `${head.x + dx * 0.5 - bendX} ${head.y + dy * 0.5 - bendY},`,
    `${head.x + dx * 0.68} ${head.y + dy * 0.68}`,
    `S ${tail.x - bendX * 0.35} ${tail.y - bendY * 0.35}, ${tail.x} ${tail.y}`,
  ].join(" ");
  const group = svgElement("g", { class: "snake-art" });
  group.append(
    svgElement("path", { class: "snake-shadow", d: path }),
    svgElement("path", { class: "snake-body", d: path }),
    svgElement("circle", { class: "snake-head", cx: head.x, cy: head.y, r: 17 }),
    svgElement("circle", { class: "snake-eye", cx: head.x - 5, cy: head.y - 3, r: 2.5 }),
    svgElement("circle", { class: "snake-eye", cx: head.x + 5, cy: head.y - 3, r: 2.5 }),
  );
  layer.append(group);
}

function buildBoard() {
  elements.board.textContent = "";
  for (let visualRow = 9; visualRow >= 0; visualRow -= 1) {
    const numbers = Array.from({ length: 10 }, (_unused, index) => visualRow * 10 + index + 1);
    if (visualRow % 2 === 1) {
      numbers.reverse();
    }
    for (const number of numbers) {
      const cell = document.createElement("div");
      cell.className = `board-cell board-tone-${(number - 1) % 4}`;
      cell.dataset.position = String(number);
      cell.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "cell-number";
      label.textContent = String(number);
      cell.append(label);
      elements.board.append(cell);
    }
  }
  const layer = svgElement("svg", {
    "aria-hidden": "true",
    class: "transition-layer",
    preserveAspectRatio: "none",
    viewBox: "0 0 1000 1000",
  });
  for (const transition of state.rules.transitions) {
    if (transition.kind === "ladder") {
      renderLadder(layer, transition);
    } else {
      renderSnake(layer, transition);
    }
  }
  elements.board.append(layer);
}

function playerById(playerId) {
  return state.room?.players.find((player) => player.id === playerId) || null;
}

function playerLabel(player) {
  const labels = [];
  if (player.id === state.room.you.id) labels.push("you");
  return labels.length ? `${player.name} (${labels.join(", ")})` : player.name;
}

function renderPieces() {
  for (const existing of elements.board.querySelectorAll(".piece")) {
    existing.remove();
  }
  if (!state.room) {
    return;
  }
  const summaries = [];
  for (const [index, player] of state.room.players.entries()) {
    summaries.push(`${playerLabel(player)} is ${player.position === 0 ? "at Start" : `on square ${player.position}`}`);
    if (player.position === 0) {
      continue;
    }
    const cell = elements.board.querySelector(`[data-position="${player.position}"]`);
    if (!cell) {
      continue;
    }
    const piece = document.createElement("span");
    piece.className = `piece piece-${player.color}`;
    piece.textContent = String(index + 1);
    piece.setAttribute("aria-hidden", "true");
    cell.append(piece);
  }
  elements.boardSummary.textContent = summaries.join(". ");
}

function renderPlayers() {
  elements.playerList.textContent = "";
  elements.playerCount.textContent = `${state.room.players.length}/${state.room.maxPlayers}`;
  for (const [index, player] of state.room.players.entries()) {
    const item = document.createElement("li");
    item.className = "player-item";
    if (player.id === state.room.currentPlayerId) {
      item.classList.add("is-current");
    }
    if (player.id === state.room.winnerId) {
      item.classList.add("is-winner");
    }
    const marker = document.createElement("span");
    marker.className = `player-marker piece-${player.color}`;
    marker.textContent = String(index + 1);
    marker.setAttribute("aria-hidden", "true");
    const details = document.createElement("span");
    details.className = "player-details";
    const name = document.createElement("strong");
    name.textContent = playerLabel(player);
    const role = document.createElement("small");
    const tags = [];
    if (player.id === state.room.currentPlayerId) tags.push("Current turn");
    if (player.id === state.room.winnerId) tags.push("Winner");
    role.textContent = tags.join(" · ") || "Player";
    details.append(name, role);
    const position = document.createElement("span");
    position.className = "player-position";
    position.textContent = player.position === 0 ? "Start" : String(player.position);
    position.setAttribute("aria-label", player.position === 0 ? "At start" : `Square ${player.position}`);
    item.append(marker, details, position);
    elements.playerList.append(item);
  }
}

function describeLastMove() {
  const move = state.room.lastMove;
  if (!move) {
    return state.room.status === "waiting"
      ? "No rolls yet. The server starts the match automatically."
      : "The match started. Waiting for the first roll.";
  }
  const player = playerById(move.playerId);
  const name = player ? playerLabel(player) : "A player";
  let description = `${name} ${move.automatic ? "auto-rolled" : "rolled"} ${move.roll}`;
  if (move.from === move.to && move.from + move.roll > state.rules.finishPosition) {
    description += ` and stayed on ${move.to} because the roll overshot 100`;
  } else {
    description += ` and moved from ${move.from === 0 ? "Start" : move.from} to ${move.to}`;
  }
  if (move.transportFrom !== null) {
    description += move.transportTo > move.transportFrom
      ? `, climbing the ladder from ${move.transportFrom} to ${move.transportTo}`
      : `, sliding down the snake from ${move.transportFrom} to ${move.transportTo}`;
  }
  if (move.extraTurn) {
    description += ". A six earns another turn";
  }
  return `${description}.`;
}

function matchOutcomeMessage() {
  const winner = state.room.winnerId ? playerById(state.room.winnerId) : null;
  return winner ? `${playerLabel(winner)} won the match!` : "The match ended in a draw.";
}

function renderTurn() {
  const room = state.room;
  elements.newRoom.hidden = room.status !== "finished";

  if (room.status === "waiting") {
    const missing = room.maxPlayers - room.players.length;
    elements.die.textContent = "?";
    elements.turnCountdown.textContent = `${room.players.length} of ${room.maxPlayers} teams ready`;
    elements.turnLabel.textContent = room.queueAhead > 0
      ? `${room.queueAhead} match${room.queueAhead === 1 ? "" : "es"} ahead`
      : missing > 0 ? `Waiting for ${missing} more team${missing === 1 ? "" : "s"}` : "Next match ready";
    elements.gameAction.textContent = "Match starts automatically";
    elements.gameAction.disabled = true;
    return;
  }

  if (room.status === "finished") {
    const winner = room.winnerId ? playerById(room.winnerId) : null;
    elements.die.textContent = room.lastMove ? DIE_FACES[room.lastMove.roll] : "—";
    elements.turnCountdown.textContent = room.scoreStatus === "recorded"
      ? "Final position recorded"
      : "Match complete";
    elements.turnLabel.textContent = winner ? `${playerLabel(winner)} wins!` : "Match ended in a draw";
    elements.gameAction.textContent = "Game over";
    elements.gameAction.disabled = true;
    return;
  }

  const current = playerById(room.currentPlayerId);
  const isYourTurn = current.id === room.you.id;
  elements.die.textContent = room.lastMove ? DIE_FACES[room.lastMove.roll] : "?";
  elements.turnLabel.textContent = isYourTurn ? "Your turn — roll the dice" : `${current.name}'s turn`;
  elements.gameAction.textContent = isYourTurn ? "Roll dice" : "Waiting for roll";
  elements.gameAction.disabled = !isYourTurn || state.busy;
}

function renderCountdown() {
  if (!state.room || state.room.status !== "playing" || !state.clock) {
    return;
  }
  let turnRemaining;
  let matchRemaining;
  try {
    const current = serverNow(state.clock, performance.now());
    turnRemaining = Math.max(0, state.room.turnDeadline - current);
    matchRemaining = Math.max(0, state.room.matchDeadline - current);
  } catch {
    elements.turnCountdown.textContent = "Syncing timer…";
    return;
  }
  const matchSeconds = Math.ceil(matchRemaining / 1_000);
  const minutes = Math.floor(matchSeconds / 60);
  const seconds = String(matchSeconds % 60).padStart(2, "0");
  elements.turnCountdown.textContent = `${Math.ceil(turnRemaining / 1_000)}s turn · ${minutes}:${seconds} match`;
}

function renderRoom() {
  const room = state.room;
  elements.roomHelp.textContent = room.status === "waiting"
    ? room.queueAhead > 0
      ? "Another assigned match is still playing. Keep this page open to hold your queue seat."
      : "The server is filling this group in arrival order. Keep this page open."
    : room.status === "playing"
      ? `Ranked match · Turn ${room.turnNumber} · highest square wins at the deadline`
      : "This assigned match is complete.";
  renderPlayers();
  renderPieces();
  renderTurn();
  renderCountdown();

  const announcement = describeLastMove();
  elements.lastMove.textContent = announcement;
  if (announcement !== state.lastAnnouncement && room.lastMove) {
    state.lastAnnouncement = announcement;
  }
}

function saveSeat() {
  try {
    localStorage.setItem(SAVED_ROOM_KEY, JSON.stringify({
      playerToken: state.playerToken,
    }));
  } catch {
    // The in-memory seat remains playable when storage is unavailable.
  }
}

function savedSeat() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SAVED_ROOM_KEY) || "null");
    if (
      parsed
      && typeof parsed.playerToken === "string"
      && parsed.playerToken.length >= 16
    ) {
      return parsed;
    }
  } catch {
    // Ignore corrupt or unavailable browser storage.
  }
  return null;
}

function clearSeat() {
  state.playerToken = null;
  state.room = null;
  state.clock = null;
  state.lastAnnouncement = "";
  try {
    localStorage.removeItem(SAVED_ROOM_KEY);
  } catch {
    // Nothing else needs clearing.
  }
}

function showEntry() {
  stopPolling();
  elements.entryPanel.hidden = false;
  elements.gamePanel.hidden = true;
  elements.newRoom.hidden = true;
  elements.playerName.focus();
  const saved = savedSeat();
  elements.reconnectRoom.hidden = saved === null;
  if (saved) {
    elements.reconnectRoom.textContent = "Reconnect to saved match";
  }
}

function showGame() {
  elements.entryPanel.hidden = true;
  elements.gamePanel.hidden = false;
  elements.roomTitle.focus();
  startPolling();
}

function applyRoom(raw, sent, received) {
  const room = normalizeRoom(raw);
  state.clock = makeServerClock(room.serverTime, sent, received);
  state.room = room;
  renderRoom();
}

function normalizeSeatResponse(raw) {
  if (
    !raw
    || typeof raw !== "object"
    || Array.isArray(raw)
    || Object.keys(raw).sort().join("|") !== "playerToken|room"
  ) {
    throw new Error("The server returned an invalid room seat.");
  }
  return {
    playerToken: boundedString(raw.playerToken, "room player token"),
    room: raw.room,
  };
}

function formValues() {
  const name = elements.playerName.value.trim();
  const token = elements.capability.value.trim();
  elements.playerName.setAttribute("aria-invalid", name ? "false" : "true");
  elements.capability.setAttribute("aria-invalid", token ? "false" : "true");
  if (!name || !token) {
    throw new Error("Enter a display name and your current KotH capability.");
  }
  return { name, token };
}

async function enterQueue() {
  if (state.busy) return;
  try {
    const values = formValues();
    state.busy = true;
    elements.joinQueue.disabled = true;
    setStatus("Joining the fair matchmaking queue…");
    const response = await requestJson("/api/matchmaking", {
      name: values.name,
      token: values.token,
    });
    const seat = normalizeSeatResponse(response.document);
    state.playerToken = seat.playerToken;
    elements.capability.value = "";
    applyRoom(seat.room, response.sent, response.received);
    saveSeat();
    showGame();
    setStatus(seat.room.status === "playing" ? "Your match started." : "Queue seat assigned.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    state.busy = false;
    elements.joinQueue.disabled = false;
    if (state.room) renderTurn();
  }
}

function actionId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

async function runGameAction() {
  if (state.busy || !state.room) return;
  try {
    state.busy = true;
    renderTurn();
    setStatus("Rolling the server die…");
    const response = await requestJson("/api/matchmaking/roll", {
      actionId: actionId(),
      playerToken: state.playerToken,
    });
    applyRoom(response.document, response.sent, response.received);
    if (state.room.status === "finished") {
      setStatus(matchOutcomeMessage(), "success");
      stopPolling();
    } else {
      setStatus(describeLastMove(), "success");
    }
  } catch (error) {
    setStatus(error.message, "error");
    await pollRoom();
  } finally {
    state.busy = false;
    if (state.room) renderTurn();
  }
}

async function pollRoom() {
  if (state.polling || !state.playerToken) return;
  state.polling = true;
  try {
    const response = await requestJson("/api/matchmaking/state", {
      playerToken: state.playerToken,
    });
    const previousStatus = state.room?.status;
    const previousMove = state.room?.turnNumber;
    applyRoom(response.document, response.sent, response.received);
    if (previousStatus === "waiting" && state.room.status === "playing") {
      setStatus("Four teams are ready. Your match started automatically.", "success");
    } else if (previousMove !== state.room.turnNumber && state.room.lastMove) {
      setStatus(describeLastMove(), "success");
    }
    if (state.room.status === "finished") {
      setStatus(matchOutcomeMessage(), "success");
      stopPolling();
    }
  } catch (error) {
    setStatus(error.message, "error");
    if ([403, 404, 410].includes(error.status)) {
      clearSeat();
      showEntry();
    }
  } finally {
    state.polling = false;
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = window.setInterval(pollRoom, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (state.pollTimer !== null) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function reconnect() {
  const saved = savedSeat();
  if (!saved) {
    setStatus("No saved match is available.", "warning");
    return;
  }
  state.playerToken = saved.playerToken;
  setStatus("Reconnecting to your assigned match…");
  await pollRoom();
  if (state.room) {
    showGame();
    setStatus("Reconnected to your assigned match.", "success");
  }
}

function renderRules() {
  elements.rulesList.textContent = "";
  for (const rule of state.rules.rules) {
    const item = document.createElement("li");
    item.textContent = rule;
    elements.rulesList.append(item);
  }
}

async function initialize() {
  try {
    const rulesResponse = await requestJson("/api/rules");
    state.rules = normalizeRules(rulesResponse.document);
    renderRules();
    buildBoard();

    const healthResponse = await requestJson("/healthz");
    const health = healthResponse.document;
    elements.runtimeBadge.textContent = health.mode === "local-development"
      ? "Local game"
      : health.scoringReady ? "Leaderboard ready" : "Leaderboard warming up";
    elements.runtimeBadge.dataset.ready = String(Boolean(health.scoringReady));
    showEntry();
    if (savedSeat()) {
      await reconnect();
      if (state.room) return;
    }
    setStatus("Enter your capability to join fair four-team matchmaking.", "success");
  } catch (error) {
    elements.runtimeBadge.textContent = "Unavailable";
    setStatus(error.message, "error");
  }
}

elements.roomForm.addEventListener("submit", (event) => {
  event.preventDefault();
  enterQueue();
});
elements.gameAction.addEventListener("click", runGameAction);
elements.reconnectRoom.addEventListener("click", reconnect);
elements.newRoom.addEventListener("click", () => {
  clearSeat();
  showEntry();
  setStatus("Enter your capability to join another fair match.");
});
for (const input of [elements.playerName, elements.capability]) {
  input.addEventListener("input", () => input.setAttribute("aria-invalid", "false"));
}
window.setInterval(renderCountdown, 250);
window.addEventListener("beforeunload", stopPolling);

initialize();
