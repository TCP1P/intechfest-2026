const dom = Object.fromEntries(
  ["phase", "selection", "countdown", "deadline-label", "owner", "hold", "heat", "commit", "threshold", "performer-count", "roster"]
    .map(id => [id, document.getElementById(id)])
);

let state = null;
const phaseLabels = {
  enrollment: "Enrollment open",
  reveal: "Mod selection open",
  running: "Heat live",
  submission: "Seal the run",
  results: "Wave finalized"
};

function number(value) {
  return new Intl.NumberFormat("en-US").format(value || 0);
}

function deadlineFor(view) {
  const heat = view.heat;
  if (heat.phase === "enrollment") return [heat.enrollment_ends_unix_ms, "ENROLLMENT CLOSES"];
  if (heat.phase === "reveal") return [heat.start_at_unix_ms, "SHARED START"];
  if (heat.phase === "running") return [heat.end_at_unix_ms, "INPUT CLOSES"];
  if (heat.phase === "submission") return [heat.submission_ends_unix_ms, "MANIFEST CLOSES"];
  return [heat.next_heat_at_unix_ms, "NEXT HEAT"];
}

function render(view) {
  state = view;
  const heat = view.heat;
  dom.phase.textContent = phaseLabels[heat.phase] || heat.phase;
  dom.heat.textContent = `#${heat.index} / ${heat.id}`;
  dom.commit.textContent = heat.commitment.slice(0, 14) + "…";
  dom.commit.title = heat.commitment;
  dom.threshold.textContent = number(view.crown_threshold);
  dom.owner.textContent = view.crown_owner_name || "Unclaimed";
  dom.hold.textContent = view.crown_since_unix_ms
    ? `Protected since ${new Date(view.crown_since_unix_ms).toLocaleTimeString()}`
    : "No protected hold";
  dom.selection.textContent = heat.selection
    ? `${heat.selection.song_title} · ${heat.selection.ruleset.toUpperCase()} · ${heat.selection.control_profile}`
    : "Song and discipline remain committed until enrollment closes.";

  const performers = heat.performers || [];
  dom["performer-count"].textContent = `${performers.length} enrolled`;
  dom.roster.innerHTML = performers.length ? performers.map(p => `
    <tr>
      <td>${escapeHtml(p.team_name)}</td>
      <td class="${p.ready ? "yes" : "no"}">${p.ready ? `READY · ${escapeHtml(p.applied_mods?.acronym || "NM")}` : "WAIT"}</td>
      <td>${number(p.frames)}</td>
      <td>${p.raw_basis ? number(p.raw_basis) : "—"}</td>
      <td>${p.settled_score ? number(p.settled_score) : "—"}</td>
      <td class="${p.eligible ? "eligible" : p.completion_sent ? "yes" : "no"}">${p.eligible ? "BOOSTED" : p.completion_sent ? "SCORED" : "OPEN"}</td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">Enrollment is empty.</td></tr>`;
}

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}

function tick() {
  if (!state) return;
  const [deadline, label] = deadlineFor(state);
  const remaining = Math.max(0, deadline - Date.now());
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  const millis = remaining % 1000;
  dom.countdown.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  dom["deadline-label"].textContent = label;
}

const stream = new EventSource("/api/v1/events");
stream.addEventListener("state", event => render(JSON.parse(event.data)));
stream.onerror = () => { dom.phase.textContent = "Coordinator reconnecting"; };
setInterval(tick, 43);
