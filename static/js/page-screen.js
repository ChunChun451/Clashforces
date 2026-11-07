// The big screen: a read-only scoreboard for a projector, live over the WebSocket as a spectator.
// The host also drives the freeze reveal from here (button, Space or →).
import { $, api, el, fmtDuration, getMe, handleEl, toast } from "./api.js";
import { brandMark, icon } from "./icons.js";
import { connect, on, screenView, serverNow, subscribe, syncClock } from "./ws.js";
import { fmtPts, teamBadge, teamClass } from "./standings.js";
import { fmtCountdown } from "./time.js";

const root = $("#screen");
const code = location.pathname.split("/")[2].toUpperCase();
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const me = await getMe();
const myId = me.user ? me.user.id : null;

let M = null;              // latest spectator state
let firstSeen = null;      // labels somebody has solved (for first-solve toasts)
let page = 0;              // pagination when the rows don't fit
let online = true;

const isTeam = () => !!(M && M.teams);
const isHost = () => !!(M && myId && M.creator_id === myId);
const revealing = () => !!(M && M.reveal && M.reveal.status === "running");
const locked = () => !!(M && M.reveal && (M.reveal.status === "waiting" || M.reveal.status === "running"));

// ---------- layout ----------
const head = el("header.sc-head");
const banner = el("div.banner.wait.sc-banner", { hidden: true });
const board = el("section.sc-board", { "aria-label": "Scoreboard" });
const foot = el("footer.sc-foot");
const toasts = el("div.sc-toasts", { role: "status", "aria-live": "polite" });
root.append(head, banner, board, foot);
document.body.append(toasts);

// ---------- live connection ----------
screenView();
on("match.state", (d) => {
  if (d.code !== code) return;
  syncClock(d.server_time);
  const was = M;
  M = d;
  render(was);
});
on("standings", (d) => {
  if (!M || revealing()) return;
  M.standings = d.standings;
  M.frozen = d.frozen;
  renderBoard({ animate: true, toasts: true });
});
on("match.reveal", (d) => {
  if (!M) return;
  M.reveal = d.reveal;
  if (d.standings) M.standings = d.standings;
  renderBoard({ animate: true });
  renderFoot();
  tick();
});
on("match.announcement", (d) => {
  if (!M) return;
  M.announcements = [d, ...(M.announcements || []).filter((a) => a.id !== d.id)];
  renderBanner(true);
});
on("socket.open", () => { online = true; renderFoot(); });
on("socket.closed", () => { online = false; renderFoot(); });
on("error", (d) => showToast(el("span", d.message)));

subscribe(code);
connect();

// ---------- render ----------
function render(was) {
  renderHead();
  renderBanner(false);
  renderBoard({ animate: !!was, toasts: false });
  renderFoot();
  tick();
}

function title() {
  if (!M) return "";
  if (M.contest) return M.contest.title;
  const n = M.team_count || 2;
  return { friend: "Friend duel", group: "Group room", ranked: "Ranked", team: n === 2 ? `Team duel · ${M.team_size}v${M.team_size}` : `Team duel · ${n} teams` }[M.mode] || "Match";
}

function renderHead() {
  document.title = `${title()} · Big screen`;
  const n = M.team_count || 2;
  const format = isTeam() ? `${n} teams × ${M.team_size}` : M.contest ? "Solo" : null;
  head.replaceChildren(
    el("div.sc-title",
      el("span.sc-brand", brandMark(), el("span.brand-word", "clash", el("span", "forces"))),
      el("h1", title()),
      el("div.sc-meta",
        M.contest ? el("span.chip", "Contest") : null,
        format ? el("span.chip", format) : null,
        el("span.chip", M.rules === "icpc" ? "ICPC" : M.settings && M.settings.sprint ? "Sprint" : "Lockout"),
        M.contest && M.contest.host ? el("span.chip", "Host", handleEl(M.contest.host.handle, M.contest.host.cf_rating, { link: false })) : null)),
    el("div.sc-clock",
      el("div.sc-label", { id: "sc-label" }),
      el("div.sc-time", { id: "sc-time", role: "timer" }),
      el("div.sc-sub", { id: "sc-sub" })));
}

function renderBanner(fresh) {
  const a = (M.announcements || [])[0];
  banner.hidden = !a;
  root.classList.toggle("has-banner", !!a);
  if (!a) return;
  banner.replaceChildren(
    el("span.wait-label", icon("megaphone", 18), "Announcement"),
    el("span.grow", { title: a.body }, a.body.replace(/\s*\n\s*/g, " · ")),
    el("span.meta", new Date(a.created_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })));
  banner.classList.remove("fresh");
  if (fresh) { void banner.offsetWidth; banner.classList.add("fresh"); }
}

function labels() {
  const seen = new Map();
  for (const p of M.problems || []) if (!seen.has(p.label)) seen.set(p.label, { label: p.label, points: p.points });
  if (!seen.size) for (const r of M.standings || []) for (const c of r.cells) if (!seen.has(c.label)) seen.set(c.label, { label: c.label });
  return [...seen.values()];
}

// The earliest accepted time per problem (ICPC), or the owner (Lockout): those cells get the first-solve dot.
function firstSolves(rows) {
  const first = new Map();
  for (const r of rows) for (const c of r.cells) {
    if (c.state !== "ok") continue;
    if (M.rules === "lockout") { if (c.first) first.set(c.label, -1); continue; }
    const t = c.time ?? c.minute * 60;
    if (!first.has(c.label) || t < first.get(c.label)) first.set(c.label, t);
  }
  return first;
}

function isFirst(c, first) {
  if (c.state !== "ok") return false;
  if (M.rules === "lockout") return !!c.first;
  return first.get(c.label) === (c.time ?? c.minute * 60);
}

function cellNode(c, first, extra = "") {
  const cls = (s) => `cell ${s}${isFirst(c, first) ? " first" : ""}${extra}`;
  switch (c.state) {
    case "ok": {
      const late = M.rules === "lockout" && !c.first;
      const top = M.rules === "lockout" ? (c.first ? `+${fmtPts(c.points)}` : "✓") : (c.tries ? `+${c.tries}` : "+");
      return el("div", { class: cls(late ? "ok-late" : "ok"), dataset: { label: c.label } }, el("span", top), el("span.t", `${c.minute}'`));
    }
    case "wrong": return el("div", { class: cls("wrong"), dataset: { label: c.label } }, `−${c.tries}`);
    case "testing": return el("div", { class: cls("testing"), dataset: { label: c.label } }, el("span.spinner"));
    case "frozen": return el("div", { class: cls("frozen"), dataset: { label: c.label } },
      el("span", "?"), el("span.t", c.tries ? `−${c.tries} · ${c.frozen_tries}` : `${c.frozen_tries} ${c.frozen_tries === 1 ? "try" : "tries"}`));
    default: return el("div", { class: cls("none"), dataset: { label: c.label } });
  }
}

// Rows before the start: teams (or players) with no problems yet.
function lobbyRows() {
  if (isTeam()) {
    return M.teams.map((t) => ({ key: `t${t.team}`, team: t.team, name: t.name, place: null, cells: [],
      members: M.players.filter((p) => p.team === t.team).map((p) => ({ handle: p.handle, cf_rating: p.cf_rating })) }));
  }
  return M.players.map((p) => ({ key: String(p.user_id), handle: p.handle, cf_rating: p.cf_rating, place: null, cells: [] }));
}

function rowKey(r) { return r.key || String(r.user_id); }

function geometry(n, cols) {
  const boxH = board.clientHeight || (innerHeight - 320);
  const perCol = Math.ceil(n / cols);
  const headH = 56;
  const raw = Math.floor((boxH - headH - 2) / Math.max(1, perCol));
  return { rowH: Math.max(26, Math.min(120, raw)), perPage: Math.max(1, Math.floor((boxH - headH - 2) / 26)), perCol };
}

function renderBoard({ animate = false, toasts: wantToasts = false } = {}) {
  if (!M) return;
  const started = (M.problems || []).length > 0 && M.status !== "lobby";
  const rows = started ? (M.standings || []) : lobbyRows();
  const labs = started ? labels() : [];
  if (!rows.length) {
    board.className = "sc-board";
    board.replaceChildren(el("div.sc-empty", M.status === "cancelled" ? "This match was cancelled." : "Nobody has joined yet."));
    return;
  }

  // first-solve toasts: a problem nobody had solved on this board now has an accepted cell
  const first = firstSolves(rows);
  if (firstSeen && wantToasts && !revealing()) {
    for (const [label] of first) if (!firstSeen.has(label)) toastFirst(label, rows, first);
  }
  firstSeen = new Set(first.keys());

  const twoCols = !isTeam() && rows.length > 20 && innerWidth >= 1280;
  const cols = twoCols ? 2 : 1;
  const geo = geometry(rows.length, cols);
  // more rows than fit even at the smallest height: show them a page at a time
  let shown = rows;
  let pages = 1;
  if (geo.rowH <= 26 && rows.length > geo.perPage * cols) {
    pages = Math.ceil(rows.length / (geo.perPage * cols));
    page %= pages;
    shown = rows.slice(page * geo.perPage * cols, (page + 1) * geo.perPage * cols);
  }
  const perCol = Math.ceil(shown.length / cols);
  const rowH = pages > 1 ? 26 : geo.rowH;

  const before = new Map();
  if (animate && !reduceMotion) for (const n of board.querySelectorAll(".sc-row[data-key]")) before.set(n.dataset.key, n.getBoundingClientRect());

  const lockout = M.rules === "lockout";
  const solvedBy = new Map(labs.map((l) => [l.label, 0]));
  for (const r of rows) for (const c of r.cells) if (c.state === "ok") solvedBy.set(c.label, (solvedBy.get(c.label) || 0) + 1);
  const grid = `minmax(48px, 64px) minmax(0, ${labs.length ? (twoCols ? "1.4fr" : "2.6fr") : "1fr"}) ${lockout ? "minmax(80px, 120px)" : "minmax(64px, 100px) minmax(80px, 120px)"}${labs.length ? ` repeat(${labs.length}, minmax(0, 1fr))` : ""}`;
  const nextKey = revealing() && M.reveal.next ? String(M.reveal.next.row) : null;
  const lastKey = revealing() && M.reveal.last ? String(M.reveal.last.row) : null;
  const done = M.status === "ended" && M.result_status === "final" && !locked();

  const headRow = () => el("div.sc-row.head",
    el("span.sc-place", { style: "font-size:14px" }, "#"),
    el("span", isTeam() ? "Team" : "Player"),
    lockout ? el("span.sc-num", "Points") : [el("span.sc-num", "Solved"), el("span.sc-num", "Penalty")],
    labs.map((l) => el("span.sc-p", el("b", l.label),
      el("small", lockout && l.points ? `${fmtPts(l.points)} pts` : `${solvedBy.get(l.label) || 0} ✓`))));

  const rowNode = (r) => {
    const key = rowKey(r);
    const classes = ["sc-row"];
    if (r.team !== undefined && r.team !== null) classes.push(teamClass(r.team));
    if (key === nextKey) classes.push("resolving");
    if (done && r.place === 1) classes.push("winner");
    const who = isTeam()
      ? el("span.sc-who", teamBadge(r.team, r.name), el("span.sc-names", el("span.sc-name", r.name),
        rowH >= 74 && r.members && r.members.length ? el("span.sc-members", r.members.map((m) => handleEl(m.handle, m.cf_rating, { link: false }))) : null))
      : el("span.sc-who", el("span.sc-names", el("span.sc-name", handleEl(r.handle, r.cf_rating, { link: false }))));
    const cells = labs.map((l) => {
      const c = r.cells.find((x) => x.label === l.label) || { label: l.label, state: "none" };
      let extra = "";
      if (key === nextKey && M.reveal.next.label === l.label) extra = " next";
      return cellNode(c, first, extra);
    });
    return el("div", { class: classes.join(" "), dataset: { key } },
      el("span.sc-place", r.place === null || r.place === undefined ? "" : String(r.place)),
      who,
      lockout ? el("span.sc-num", r.place === null ? "" : fmtPts(r.points))
        : [el("span.sc-num", r.place === null ? "" : String(r.solved)), el("span.sc-num.pen", r.place === null ? "" : String(r.penalty))],
      cells);
  };

  const tables = [];
  for (let i = 0; i < cols; i++) {
    const part = shown.slice(i * perCol, (i + 1) * perCol);
    const t = el("div.sc-table", { style: `--grid: ${grid}; --row-h: ${rowH}px` }, headRow(), part.map(rowNode));
    tables.push(t);
  }
  board.className = `sc-board${twoCols ? " two" : ""}`;
  board.replaceChildren(...tables);
  if (!started && M.status !== "cancelled") board.append(el("p.sc-note", "Problems appear when the contest starts."));
  if (pages > 1) board.append(el("p.sc-note", `Page ${page + 1} of ${pages}`));

  // the cell just revealed: green flips in, red flashes
  if (lastKey && M.reveal.last) {
    const cell = board.querySelector(`.sc-row[data-key="${CSS.escape(lastKey)}"] .cell[data-label="${CSS.escape(M.reveal.last.label)}"]`);
    if (cell && animate) cell.classList.add(cell.classList.contains("ok") ? "flip" : "bad-flash");
  }
  if (animate && !revealing()) {
    for (const r of shown) for (const c of r.cells) {
      if (c.state === "ok" && prevOk && !prevOk.has(`${rowKey(r)}:${c.label}`)) {
        const n = board.querySelector(`.sc-row[data-key="${CSS.escape(rowKey(r))}"] .cell[data-label="${CSS.escape(c.label)}"]`);
        if (n) n.classList.add("flip");
      }
    }
  }
  prevOk = new Set(rows.flatMap((r) => r.cells.filter((c) => c.state === "ok").map((c) => `${rowKey(r)}:${c.label}`)));

  // FLIP: rows slide from where they were to their new place
  if (before.size) {
    const moved = [];
    for (const n of board.querySelectorAll(".sc-row[data-key]")) {
      const b = before.get(n.dataset.key);
      if (!b) continue;
      const a = n.getBoundingClientRect();
      const dx = b.left - a.left, dy = b.top - a.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      n.style.transition = "none";
      n.style.transform = `translate(${dx}px, ${dy}px)`;
      n.classList.add(dy > 0 ? "rising" : "moving");
      moved.push(n);
    }
    if (moved.length) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        for (const n of moved) { n.style.transition = ""; n.style.transform = ""; }
        setTimeout(() => moved.forEach((n) => n.classList.remove("moving", "rising")), 800);
      }));
    }
  }
}
let prevOk = null;

function toastFirst(label, rows, first) {
  const t = first.get(label);
  const r = rows.find((x) => x.cells.some((c) => c.label === label && c.state === "ok" && isFirst(c, first)));
  if (!r) return;
  const c = r.cells.find((x) => x.label === label);
  const who = isTeam()
    ? el("span", teamBadge(r.team, r.name), " ", el("strong", r.name), c.by_handle ? el("span.muted", ` · ${c.by_handle}`) : null)
    : el("strong", handleEl(r.handle, r.cf_rating, { link: false }));
  showToast(el("span.cell-letter", label), el("span", "First solve · "), who, el("span.muted", `${c.minute}'`));
  void t;
}

function showToast(...children) {
  const n = el("div.sc-toast", children);
  toasts.append(n);
  while (toasts.children.length > 3) toasts.firstElementChild.remove();
  setTimeout(() => { n.classList.add("out"); setTimeout(() => n.remove(), 450); }, 6000);
}

// ---------- footer: watch link, reveal progress, host controls ----------
async function revealAct(action) {
  try { await api(`/api/contests/${code}/reveal`, { method: "POST", body: { action } }); }
  catch (e) { toast(e.message, true); }
}

function renderFoot() {
  if (!M) return;
  const url = el("span.sc-url", `${location.host}/m/`, el("b", code));
  let middle = el("span");
  const rv = M.reveal;
  if (rv && rv.status === "running") {
    middle = el("div.sc-progress", `Revealing ${rv.step} of ${rv.total}`,
      el("div.bar", el("span", { style: `width: ${rv.total ? (rv.step / rv.total) * 100 : 0}%` })));
  } else if (!online) middle = el("span.sc-offline", "Reconnecting…");
  const controls = el("div.sc-controls");
  if (isHost() && rv && (rv.status === "waiting" || rv.status === "running") && M.result_status === "final") {
    controls.append(
      rv.status === "waiting"
        ? el("button.btn.primary", { type: "button", onclick: () => revealAct("start") }, "Start reveal")
        : el("button.btn.primary", { type: "button", onclick: () => revealAct("next"), title: "Space or →" }, "Next", el("kbd", "Space")),
      el("button.btn", { type: "button", onclick: () => revealAct(rv.auto ? "pause" : "auto"), "aria-pressed": String(!!rv.auto) }, rv.auto ? "Pause" : "Auto-play"),
      el("button.btn.ghost", { type: "button", onclick: () => { if (confirm("Skip to the final standings?")) revealAct("skip"); } }, "Skip to final"));
  }
  controls.append(el("button.btn.ghost.icon", { type: "button", title: "Full screen (F)", "aria-label": "Full screen", onclick: fullscreen }, icon("expand", 18)));
  foot.replaceChildren(url, middle, controls);
}

function fullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => {});
}

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, button")) return;
  if (e.key === "f" || e.key === "F") { fullscreen(); return; }
  if (!isHost() || !M || !locked() || M.result_status !== "final") return;
  if (e.key === " " || e.key === "ArrowRight" || e.key === "Enter") {
    e.preventDefault();
    revealAct(M.reveal.status === "waiting" ? "start" : "next");
  } else if (e.key === "a" || e.key === "A") revealAct(M.reveal.auto ? "pause" : "auto");
});

// The cursor and host controls hide when the mouse rests, so the projection stays clean.
let idleTimer = null;
document.addEventListener("mousemove", () => {
  document.body.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => document.body.classList.add("idle"), 3000);
});

// ---------- clock ----------
function tick() {
  if (!M) return;
  const label = $("#sc-label"), time = $("#sc-time"), sub = $("#sc-sub");
  if (!label) return;
  const now = serverNow();
  let l = "", t = "", s = "", cls = "", chip = null;
  if (M.status === "lobby" || M.status === "starting") {
    const at = M.contest && M.contest.scheduled_at;
    if (M.status === "starting") { l = "Starting"; t = "…"; cls = "word"; }
    else if (at && at > now) { l = "Starts in"; t = fmtCountdown(at - now); }
    else { l = "Waiting"; t = "Soon"; cls = "word"; s = M.contest ? "The host starts the contest." : "Waiting for everyone to be ready."; }
  } else if (M.status === "running" && now < M.start_time) {
    l = "Starts in"; t = String(Math.max(0, Math.ceil(M.start_time - now)));
  } else if (M.status === "running") {
    const left = M.end_time - now;
    l = "Time left"; t = fmtDuration(left);
    cls = left <= 300 ? "low" : "";
    if (M.frozen) chip = el("span.chip.frozen-chip", "Frozen");
    else if (M.freeze_start && M.freeze_start > now) s = `Scoreboard freezes in ${fmtCountdown(M.freeze_start - now)}`;
  } else if (M.status === "ended") {
    const rv = M.reveal;
    if (M.result_status !== "final") { l = "Contest over"; t = "Ended"; cls = "word done"; s = "Confirming the last submissions…"; chip = M.frozen ? el("span.chip.frozen-chip", "Frozen") : null; }
    else if (rv && rv.status === "waiting") { l = "Contest over"; t = "Frozen"; cls = "word"; s = "Results are revealed next."; }
    else if (rv && rv.status === "running") { l = "Reveal"; t = `${rv.step}/${rv.total}`; s = rv.auto ? "Auto-play" : ""; }
    else {
      l = "Final standings"; t = "Final"; cls = "word";
      const top = (M.standings || []).filter((r) => r.place === 1);
      s = top.length ? `Winner: ${top.map((r) => r.name || r.handle).join(", ")}` : "";
    }
  } else if (M.status === "cancelled") { l = "Cancelled"; t = "—"; cls = "word done"; }
  label.replaceChildren(chip || "", l);
  time.textContent = t;
  time.className = `sc-time ${cls}`.trim();
  sub.textContent = s;
}
setInterval(tick, 250);

// pages flip every 12 s when there are more rows than fit; the board refits when the window changes
setInterval(() => { if (M && !revealing()) { page += 1; renderBoard(); } }, 12000);
addEventListener("resize", () => renderBoard());
