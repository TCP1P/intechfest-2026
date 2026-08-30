import { makeServerClock, serverNow } from "./clock.mjs";

(() => {
  "use strict";

  const MAX_RESPONSE_BYTES = 256 * 1024;
  const REQUEST_TIMEOUT_MS = 10_000;
  const MAX_BOARD_SIZE = 400;
  const MAX_TURNS = 64;

  const elements = {
    acceptanceChip: document.querySelector("#acceptance-chip"),
    board: document.querySelector("#board"),
    boardRange: document.querySelector("#board-range"),
    boardSummary: document.querySelector("#board-summary"),
    connectionBadge: document.querySelector(".runtime-badge"),
    connectionState: document.querySelector("#connection-state"),
    efficiency: document.querySelector("#efficiency"),
    finalPosition: document.querySelector("#final-position"),
    finishPosition: document.querySelector("#finish-position"),
    gameName: document.querySelector("#game-name"),
    gamePanel: document.querySelector("#game-panel"),
    joinButton: document.querySelector("#join-button"),
    joinPanel: document.querySelector("#join-panel"),
    landingList: document.querySelector("#landing-list"),
    objective: document.querySelector("#objective"),
    optimalPosition: document.querySelector("#optimal-position"),
    playButton: document.querySelector("#play-button"),
    playForm: document.querySelector("#play-form"),
    protocolVersion: document.querySelector("#protocol-version"),
    restartButton: document.querySelector("#restart-button"),
    resultPanel: document.querySelector("#result-panel"),
    resultTitle: document.querySelector("#result-title"),
    rulesList: document.querySelector("#rules-list"),
    rulesTurnCount: document.querySelector("#rules-turn-count"),
    status: document.querySelector("#status"),
    token: document.querySelector("#token"),
    tokenForm: document.querySelector("#token-form"),
    transitionList: document.querySelector("#transition-list"),
    turnCount: document.querySelector("#turn-count"),
    turnList: document.querySelector("#turn-list"),
    waveCountdown: document.querySelector("#wave-countdown"),
    waveId: document.querySelector("#wave-id"),
    waveSeconds: document.querySelector("#wave-seconds"),
    waveTitle: document.querySelector("#wave-title"),
  };

  const state = {
    countdownTimer: null,
    expired: false,
    rules: null,
    serverClock: null,
    session: null,
    submitting: false,
  };

  function setStatus(message, tone = "info") {
    elements.status.textContent = message;
    elements.status.dataset.tone = tone;
  }

  function setConnection(label, connectionState) {
    elements.connectionState.textContent = label;
    elements.connectionBadge.dataset.state = connectionState;
  }

  function setButtonBusy(button, busy, busyLabel, idleLabel) {
    button.disabled = busy;
    button.textContent = busy ? busyLabel : idleLabel;
  }

  function assertInteger(value, label, minimum, maximum) {
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`The server returned an invalid ${label}.`);
    }
    return value;
  }

  function boundedString(value, label, maximum = 512) {
    if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
      throw new Error(`The server returned an invalid ${label}.`);
    }
    return value;
  }

  function boundedIdentifier(value, label, maximum = 128) {
    if (
      (typeof value !== "string" && typeof value !== "number")
      || (typeof value === "number" && !Number.isFinite(value))
    ) {
      throw new Error(`The server returned an invalid ${label}.`);
    }
    return boundedString(String(value), label, maximum);
  }

  async function requestJson(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch(path, {
        ...options,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {}),
        },
        signal: controller.signal,
      });

      const text = await response.text();
      if (text.length > MAX_RESPONSE_BYTES) {
        throw new Error("The server response was larger than the client accepts.");
      }

      let body = null;
      if (text.length > 0) {
        try {
          body = JSON.parse(text);
        } catch {
          throw new Error("The server returned malformed JSON.");
        }
      }

      if (!response.ok) {
        const serverMessage = body && typeof body === "object"
          ? body.error || body.message
          : null;
        throw new Error(
          typeof serverMessage === "string" && serverMessage.length <= 240
            ? serverMessage
            : `The server rejected the request (HTTP ${response.status}).`,
        );
      }

      if (body === null || typeof body !== "object" || Array.isArray(body)) {
        throw new Error("The server returned an unexpected response.");
      }

      return body;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error("The game server did not answer in time.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function normalizeRules(raw) {
    const boardSize = assertInteger(raw.boardSize, "board size", 2, MAX_BOARD_SIZE);
    const startPosition = assertInteger(raw.startPosition, "start position", 0, boardSize);
    const finishPosition = assertInteger(raw.finishPosition, "finish position", 1, boardSize);
    const turnCount = assertInteger(raw.turnCount, "turn count", 1, MAX_TURNS);
    const waveSeconds = assertInteger(raw.waveSeconds, "wave length", 1, 86_400);

    if (
      raw.name !== "Serpent Circuit"
      || raw.objective !== "route-efficiency"
      || raw.protocolVersion !== 1
      || boardSize !== 100
      || startPosition !== 1
      || finishPosition !== 100
      || turnCount !== 12
      || waveSeconds !== 60
    ) {
      throw new Error("The server returned a different Serpent Circuit contract.");
    }
    if (!Array.isArray(raw.transitions) || raw.transitions.length > boardSize) {
      throw new Error("The server returned an invalid transition list.");
    }
    if (!Array.isArray(raw.rules) || raw.rules.length > 32) {
      throw new Error("The server returned an invalid rules list.");
    }

    const seenTransitionStarts = new Set();
    const transitions = raw.transitions.map((transition) => {
      if (!transition || typeof transition !== "object" || Array.isArray(transition)) {
        throw new Error("The server returned an invalid board transition.");
      }
      const from = assertInteger(transition.from, "transition start", 1, boardSize);
      const to = assertInteger(transition.to, "transition end", 1, boardSize);
      const kind = boundedString(transition.kind, "transition kind", 32).toLowerCase();
      if (from === to || seenTransitionStarts.has(from)) {
        throw new Error("The server returned an ambiguous board transition.");
      }
      seenTransitionStarts.add(from);
      return { from, to, kind };
    });

    const rules = raw.rules.map((rule) => boundedString(rule, "rule", 360));

    return {
      boardSize,
      finishPosition,
      name: boundedString(raw.name, "game name", 120),
      objective: boundedString(raw.objective, "objective", 600),
      protocolVersion: boundedIdentifier(raw.protocolVersion, "protocol version", 64),
      rules,
      startPosition,
      transitions,
      turnCount,
      waveSeconds,
    };
  }

  function normalizeHealth(raw) {
    return {
      mode: boundedString(raw.mode, "runtime mode", 64),
    };
  }

  function normalizeWaveEnd(value) {
    if (Number.isSafeInteger(value) && value >= 0) {
      return value;
    }
    throw new Error("The server returned an invalid wave deadline.");
  }

  function normalizeSession(raw) {
    if (!Array.isArray(raw.turns) || raw.turns.length < 1 || raw.turns.length > MAX_TURNS) {
      throw new Error("The server returned an invalid turn list.");
    }

    const seenTurns = new Set();
    const turns = raw.turns.map((turn) => {
      if (!turn || typeof turn !== "object" || Array.isArray(turn)) {
        throw new Error("The server returned an invalid turn.");
      }
      const turnNumber = assertInteger(turn.turn, "turn number", 1, MAX_TURNS);
      if (seenTurns.has(turnNumber) || !Array.isArray(turn.options) || turn.options.length !== 2) {
        throw new Error("The server returned an ambiguous turn.");
      }
      seenTurns.add(turnNumber);
      const options = turn.options.map((option) => assertInteger(option, "dice choice", 1, 64));
      return { options, turn: turnNumber };
    });

    turns.sort((left, right) => left.turn - right.turn);
    if (turns.some((turn, index) => turn.turn !== index + 1)) {
      throw new Error("The server returned turns outside the required order.");
    }
    const serverTime = assertInteger(
      raw.serverTime,
      "server time",
      0,
      Number.MAX_SAFE_INTEGER,
    );
    const waveEndsAt = normalizeWaveEnd(raw.waveEndsAt);
    if (
      !state.rules
      || waveEndsAt <= serverTime
      || waveEndsAt - serverTime > state.rules.waveSeconds * 1000
    ) {
      throw new Error("The server returned an impossible wave deadline.");
    }
    return {
      serverTime,
      sessionId: boundedString(raw.sessionId, "session ID", 256),
      turns,
      waveEndsAt,
      waveId: boundedIdentifier(raw.waveId, "wave ID"),
    };
  }

  function normalizeResult(raw) {
    if (typeof raw.accepted !== "boolean") {
      throw new Error("The server returned an invalid acceptance verdict.");
    }
    if (!raw.efficiency || typeof raw.efficiency !== "object" || Array.isArray(raw.efficiency)) {
      throw new Error("The server returned invalid efficiency evidence.");
    }
    if (!Array.isArray(raw.landings) || raw.landings.length > MAX_TURNS) {
      throw new Error("The server returned an invalid landing list.");
    }

    const boardSize = state.rules.boardSize;
    const landings = raw.landings.map((landing) => {
      if (!landing || typeof landing !== "object" || Array.isArray(landing)) {
        throw new Error("The server returned an invalid landing.");
      }
      const normalized = {
        from: assertInteger(landing.from, "landing origin", 0, boardSize),
        roll: assertInteger(landing.roll, "confirmed roll", 1, 64),
        to: assertInteger(landing.to, "landing destination", 0, boardSize),
        turn: assertInteger(landing.turn, "landing turn", 1, MAX_TURNS),
        transportFrom: landing.transportFrom,
        transportTo: landing.transportTo,
      };

      const hasTransport = landing.transportFrom !== null && landing.transportFrom !== undefined;
      if (hasTransport !== (landing.transportTo !== null && landing.transportTo !== undefined)) {
        throw new Error("The server returned incomplete transition evidence.");
      }
      if (hasTransport) {
        normalized.transportFrom = assertInteger(
          landing.transportFrom,
          "transition origin",
          1,
          boardSize,
        );
        normalized.transportTo = assertInteger(
          landing.transportTo,
          "transition destination",
          1,
          boardSize,
        );
      } else {
        normalized.transportFrom = null;
        normalized.transportTo = null;
      }
      return normalized;
    });

    const earned = raw.efficiency.earned;
    const possible = raw.efficiency.possible;
    if (
      !Number.isInteger(earned)
      || !Number.isInteger(possible)
      || earned < 0
      || possible < 1
      || earned > possible
    ) {
      throw new Error("The server returned invalid efficiency evidence.");
    }
    if (
      !state.session
      || landings.length !== state.session.turns.length
      || landings.some((landing, index) => landing.turn !== index + 1)
    ) {
      throw new Error("The server returned incomplete or unordered landing evidence.");
    }

    return {
      accepted: raw.accepted,
      efficiency: { earned, possible },
      finalPosition: assertInteger(raw.finalPosition, "final position", 0, boardSize),
      landings,
      optimalPosition: assertInteger(raw.optimalPosition, "optimal position", 0, boardSize),
      waveId: boundedIdentifier(raw.waveId, "wave ID"),
    };
  }

  function boardPositions(boardSize, columns) {
    const rows = Math.ceil(boardSize / columns);
    const positions = [];

    for (let visualRow = 0; visualRow < rows; visualRow += 1) {
      const logicalRow = rows - visualRow - 1;
      const first = logicalRow * columns + 1;
      const last = Math.min(first + columns - 1, boardSize);
      const row = [];
      for (let position = first; position <= last; position += 1) {
        row.push(position);
      }
      if (logicalRow % 2 === 1) {
        row.reverse();
      }
      positions.push(...row);
    }

    return positions;
  }

  function transitionLabel(transition) {
    const direction = transition.to > transition.from ? "Boost" : "Serpent";
    const suppliedKind = transition.kind.replace(/[-_]+/g, " ");
    const kind = suppliedKind === "ladder" || suppliedKind === "boost"
      ? "Boost"
      : suppliedKind === "snake" || suppliedKind === "serpent" || suppliedKind === "drop"
        ? "Serpent"
        : `${direction} (${suppliedKind})`;
    return `${kind} from ${transition.from} to ${transition.to}`;
  }

  function renderBoard(lastPosition = null) {
    const rules = state.rules;
    const columns = Math.max(2, Math.ceil(Math.sqrt(rules.boardSize)));
    const transitionByStart = new Map(rules.transitions.map((item) => [item.from, item]));
    const fragment = document.createDocumentFragment();

    elements.board.replaceChildren();
    boardPositions(rules.boardSize, columns).forEach((position) => {
      const cell = document.createElement("div");
      const number = document.createElement("span");
      const icon = document.createElement("span");
      const transition = transitionByStart.get(position);

      cell.className = "board-cell";
      number.className = "board-number";
      number.textContent = String(position);
      icon.className = "board-icon";
      cell.append(number, icon);

      if (transition) {
        const isBoost = transition.to > transition.from;
        cell.classList.add(isBoost ? "is-boost" : "is-drop");
        icon.textContent = isBoost ? "↗" : "↘";
      }
      if (position === rules.startPosition) {
        icon.textContent = "S";
      }
      if (position === rules.finishPosition) {
        icon.textContent = "F";
      }
      if (position === lastPosition) {
        cell.classList.add("is-player");
        icon.textContent = "◆";
      }

      fragment.append(cell);
    });

    elements.board.append(fragment);
    elements.boardRange.textContent = `${rules.startPosition} → ${rules.finishPosition}`;
    elements.board.setAttribute(
      "aria-label",
      lastPosition === null
        ? `Circuit board from position ${rules.startPosition} to ${rules.finishPosition}.`
        : `Circuit board from position ${rules.startPosition} to ${rules.finishPosition}. The last confirmed landing is ${lastPosition}.`,
    );
    elements.boardSummary.textContent = lastPosition === null
      ? `Start at ${rules.startPosition}. Server transitions are listed below the board.`
      : `The server confirmed the route's last landing at square ${lastPosition}.`;
  }

  function renderRuleReference() {
    const rules = state.rules;
    document.title = `${rules.name} · INTECHFEST 2026`;
    elements.gameName.textContent = rules.name;
    elements.objective.textContent = rules.objective;
    elements.protocolVersion.textContent = `protocol ${rules.protocolVersion}`;
    elements.waveSeconds.textContent = `${rules.waveSeconds} seconds`;
    elements.rulesTurnCount.textContent = `${rules.turnCount} turns`;
    elements.finishPosition.textContent = `square ${rules.finishPosition}`;

    elements.rulesList.replaceChildren();
    if (rules.rules.length === 0) {
      const item = document.createElement("li");
      item.textContent = "Follow the choices and submit before the wave closes.";
      elements.rulesList.append(item);
    } else {
      rules.rules.forEach((rule) => {
        const item = document.createElement("li");
        item.textContent = rule;
        elements.rulesList.append(item);
      });
    }

    elements.transitionList.replaceChildren();
    if (rules.transitions.length === 0) {
      const item = document.createElement("li");
      item.textContent = "No board transitions are active.";
      elements.transitionList.append(item);
    } else {
      [...rules.transitions]
        .sort((left, right) => left.from - right.from)
        .forEach((transition) => {
          const item = document.createElement("li");
          item.textContent = transitionLabel(transition);
          elements.transitionList.append(item);
        });
    }

    renderBoard();
  }

  function renderTurns(turns) {
    const fragment = document.createDocumentFragment();
    elements.turnList.replaceChildren();

    turns.forEach((turn) => {
      const fieldset = document.createElement("fieldset");
      const legend = document.createElement("legend");
      const choiceGrid = document.createElement("div");

      fieldset.className = "turn-card";
      fieldset.dataset.turn = String(turn.turn);
      legend.textContent = `Turn ${turn.turn}`;
      choiceGrid.className = "choice-grid";
      fieldset.append(legend, choiceGrid);

      turn.options.forEach((roll, optionIndex) => {
        const inputId = `turn-${turn.turn}-option-${optionIndex + 1}`;
        const label = document.createElement("label");
        const input = document.createElement("input");
        const choiceIndicator = document.createElement("span");
        const optionLabel = document.createElement("span");
        const optionRoll = document.createElement("span");

        label.className = "roll-choice";
        label.htmlFor = inputId;
        input.id = inputId;
        input.type = "radio";
        input.name = `turn-${turn.turn}`;
        input.value = String(optionIndex);
        input.required = true;
        choiceIndicator.className = "choice-indicator";
        choiceIndicator.setAttribute("aria-hidden", "true");
        optionLabel.className = "choice-label";
        optionLabel.textContent = `Option ${optionIndex + 1}`;
        optionRoll.className = "choice-roll";
        optionRoll.textContent = String(roll);
        label.append(input, choiceIndicator, optionLabel, optionRoll);
        choiceGrid.append(label);
      });

      fragment.append(fieldset);
    });

    elements.turnList.append(fragment);
  }

  function stopCountdown() {
    if (state.countdownTimer !== null) {
      window.clearInterval(state.countdownTimer);
      state.countdownTimer = null;
    }
  }

  function updateCountdown() {
    if (!state.session) {
      elements.waveCountdown.textContent = "—";
      return;
    }

    const milliseconds = state.session.waveEndsAt - serverNow(
      state.serverClock,
      performance.now(),
    );
    if (milliseconds <= 0) {
      elements.waveCountdown.textContent = "closed";
      stopCountdown();
      if (!state.expired && !elements.resultPanel.hidden) {
        return;
      }
      if (!state.expired) {
        state.expired = true;
        elements.playButton.disabled = true;
        setStatus("This wave closed. Start a new session to receive the current route.", "warning");
      }
      return;
    }

    const totalSeconds = Math.ceil(milliseconds / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    elements.waveCountdown.textContent = `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function startCountdown() {
    stopCountdown();
    updateCountdown();
    if (!state.expired) {
      state.countdownTimer = window.setInterval(updateCountdown, 1000);
    }
  }

  function renderSession(session) {
    elements.waveId.textContent = session.waveId;
    elements.turnCount.textContent = String(session.turns.length);
    renderTurns(session.turns);
    elements.joinPanel.hidden = true;
    elements.gamePanel.hidden = false;
    elements.resultPanel.hidden = true;
    elements.playButton.disabled = false;
    state.expired = false;
    startCountdown();
    elements.waveTitle.focus();
  }

  function renderResult(result) {
    const efficiencyText = result.efficiency.possible === 0
      ? `${result.efficiency.earned} / 0`
      : `${result.efficiency.earned} / ${result.efficiency.possible}`;

    elements.resultTitle.textContent = result.accepted ? "Route accepted" : "Route rejected";
    elements.acceptanceChip.textContent = result.accepted ? "Accepted" : "Rejected";
    elements.acceptanceChip.dataset.accepted = String(result.accepted);
    elements.finalPosition.textContent = String(result.finalPosition);
    elements.optimalPosition.textContent = String(result.optimalPosition);
    elements.efficiency.textContent = efficiencyText;
    elements.landingList.replaceChildren();

    if (result.landings.length === 0) {
      const item = document.createElement("li");
      item.className = "landing";
      const route = document.createElement("span");
      route.className = "landing-route";
      route.textContent = "No landings were confirmed.";
      item.append(route);
      elements.landingList.append(item);
    } else {
      result.landings.forEach((landing) => {
        const item = document.createElement("li");
        const route = document.createElement("span");
        const roll = document.createElement("span");

        item.className = "landing";
        route.className = "landing-route";
        route.textContent = `Turn ${landing.turn}: ${landing.from} → ${landing.to}`;
        roll.className = "landing-roll";
        roll.textContent = `roll ${landing.roll}`;

        if (landing.transportFrom !== null) {
          const transport = document.createElement("span");
          transport.className = "landing-transport";
          transport.textContent = `Transition ${landing.transportFrom} → ${landing.transportTo}`;
          route.append(transport);
        }

        item.append(route, roll);
        elements.landingList.append(item);
      });
    }

    elements.resultPanel.hidden = false;
    elements.playButton.disabled = true;
    elements.turnList.querySelectorAll("input").forEach((input) => {
      input.disabled = true;
    });
    renderBoard(result.finalPosition);
    stopCountdown();
    elements.resultTitle.focus();
  }

  async function loadRules() {
    try {
      const [rawRules, rawHealth] = await Promise.all([
        requestJson("/api/rules"),
        requestJson("/healthz"),
      ]);
      state.rules = normalizeRules(rawRules);
      const health = normalizeHealth(rawHealth);
      renderRuleReference();
      const modeLabel = health.mode.replace(/[-_]+/g, " ");
      const unavailable = health.mode === "misconfigured" || health.mode === "unconfigured";
      setConnection(modeLabel, health.mode === "misconfigured" ? "error" : "ready");
      setStatus(
        unavailable
          ? `Rules loaded. Gameplay is currently ${modeLabel}; wait for the managed target to be prepared.`
          : "Rules loaded. Enter your player capability when you are ready.",
        unavailable ? "warning" : "success",
      );
    } catch (error) {
      setConnection("Unavailable", "error");
      setStatus(error instanceof Error ? error.message : "Could not load game rules.", "error");
      elements.joinButton.disabled = true;
    }
  }

  async function createSession(event) {
    event.preventDefault();
    if (state.submitting || !state.rules) {
      return;
    }

    const token = elements.token.value.trim();
    if (token.length === 0) {
      setStatus("Enter your player capability before starting a wave.", "error");
      elements.token.focus();
      return;
    }

    state.submitting = true;
    setButtonBusy(elements.joinButton, true, "Starting…", "Start wave");
    setStatus("Authenticating capability and requesting this wave's route…");

    try {
      const requestStarted = performance.now();
      const raw = await requestJson("/api/session", {
        body: JSON.stringify({ token }),
        method: "POST",
      });
      const responseReceived = performance.now();
      const session = normalizeSession(raw);
      if (session.turns.length !== state.rules.turnCount) {
        throw new Error("The session does not match the advertised turn count.");
      }
      state.serverClock = makeServerClock(
        session.serverTime,
        requestStarted,
        responseReceived,
      );
      state.session = session;
      elements.token.value = "";
      renderSession(session);
      setStatus(`Wave ${session.waveId} is ready. Choose one roll for every turn.`, "success");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not start a session.", "error");
      elements.token.focus();
    } finally {
      state.submitting = false;
      setButtonBusy(elements.joinButton, false, "Starting…", "Start wave");
    }
  }

  function collectSelections() {
    const selections = [];
    for (const turn of state.session.turns) {
      const checked = elements.playForm.querySelector(`input[name="turn-${turn.turn}"]:checked`);
      if (!checked) {
        throw new Error(`Choose a roll for turn ${turn.turn}.`);
      }
      selections.push({ choice: Number(checked.value), turn: turn.turn });
    }
    return selections;
  }

  async function submitRoute(event) {
    event.preventDefault();
    if (state.submitting || !state.session || state.expired) {
      return;
    }

    let selections;
    try {
      selections = collectSelections();
    } catch (error) {
      setStatus(error.message, "error");
      const firstMissing = elements.playForm.querySelector("fieldset:not(:has(input:checked)) input");
      if (firstMissing) {
        firstMissing.focus();
      }
      return;
    }

    state.submitting = true;
    setButtonBusy(elements.playButton, true, "Verifying…", "Lock route");
    setStatus("Sending the complete route for server-side verification…");

    try {
      const raw = await requestJson("/api/play", {
        body: JSON.stringify({ selections, sessionId: state.session.sessionId }),
        method: "POST",
      });
      const result = normalizeResult(raw);
      if (result.waveId !== state.session.waveId) {
        throw new Error("The server verdict belongs to a different wave.");
      }
      renderResult(result);
      setStatus(
        result.accepted
          ? `Wave ${result.waveId} accepted. Server-confirmed efficiency: ${result.efficiency.earned} of ${result.efficiency.possible}.`
          : `Wave ${result.waveId} rejected the route. Start again if the wave is still active.`,
        result.accepted ? "success" : "warning",
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not verify the route.", "error");
    } finally {
      state.submitting = false;
      if (!state.expired && elements.resultPanel.hidden) {
        setButtonBusy(elements.playButton, false, "Verifying…", "Lock route");
      }
    }
  }

  function resetSession() {
    stopCountdown();
    state.expired = false;
    state.session = null;
    elements.playForm.reset();
    elements.turnList.replaceChildren();
    elements.gamePanel.hidden = true;
    elements.resultPanel.hidden = true;
    elements.joinPanel.hidden = false;
    elements.waveCountdown.textContent = "—";
    renderBoard();
    setStatus("Session cleared. Enter a capability to join the current wave.");
    elements.token.focus();
  }

  elements.tokenForm.addEventListener("submit", createSession);
  elements.playForm.addEventListener("submit", submitRoute);
  elements.restartButton.addEventListener("click", resetSession);
  window.addEventListener("pagehide", stopCountdown);

  loadRules();
})();
