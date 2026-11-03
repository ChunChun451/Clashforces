// The match page: lobby -> countdown -> live scoreboard -> result + timeline.
import { $, api, cfProblemUrl, el, fmtDuration, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { connect, on, send, serverNow, subscribe, syncClock } from "./ws.js";
import { fmtPts, flipNew, okSet, standingsTable } from "./standings.js";
import { notify } from "./notify.js";

mountTopbar("");
const app = $("#app");
const code = location.pathname.split("/").pop().toUpperCase();
const me = await getMe();
const myId = me.user ? me.user.id : null;

let M = null;           // latest match.state
let prevOk = null;      // accepted cells at the last render
let revealing = false;  // freeze reveal animation in progress
let timeline = null;
let showTags = false;
try { showTags = localStorage.getItem("showTags") === "1"; } catch { /* ignore */ }

const isPlayer = () => M && M.me !== null;
const myRow = () => M && M.standings.find((r) => r.user_id === myId);

// ---------- live connection ----------
on("match.state", (data) => {
  if (data.code !== code) return;
  syncClock(data.server_time);
  const was = M;
  M = data;
  if (was && (was.status !== M.status || was.result_status !== M.result_status)) timeline = null;
  if (!revealing) render();
});
on("standings", (data) => {
  if (!M || revealing) return;
  M.standings = data.standings;
  for (const c of data.changed || []) {
    if (c.user_id !== myId && c.accepted && isPlayer()) notify(`${c.handle} solved ${c.label}`, "opponent");
  }
  renderStandingsOnly();
});
on("match.ended", () => notify("Match over", "ended"));
on("match.warning", (d) => { if (d.code === code) notify(`${d.minutes_left} minutes left`, "warning"); });
on("match.result", (data) => {
  if (data.reveal && M && M.status === "ended") playReveal(data);
});
on("lobby.blocked", (d) => toast(d.message, true));
on("rematch.offer", (d) => toast(`${d.from} wants a rematch — open ${d.code}`));
on("api.status", (d) => { if (M) { M.cf_api = d.cf_api; render(); } });
on("error", (d) => toast(d.message, true));
on("hello", (d) => { if (M) M.cf_api = d.cf_api; });

subscribe(code);
connect();

// The tab-return soft hint: the player was probably on Codeforces.
let hiddenAt = null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) hiddenAt = Date.now();
  else if (hiddenAt && M && M.status === "running" && isPlayer()) {
    send("focus", { code, away_s: (Date.now() - hiddenAt) / 1000 });
    hiddenAt = null;
  }
});

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "s" || e.key === "S") { if (M && M.status === "running" && isPlayer()) submitted(); }
  if (e.key === "t" || e.key === "T") toggleTags();
});

// ---------- actions ----------
async function act(path, body) {
  try { return await api(`/api/matches/${code}/${path}`, { method: "POST", body }); }
  catch (e) { toast(e.message, true); return null; }
}

async function submitted() {
  const r = await act("submitted");
  if (r) toast(r.queued ? "Checking Codeforces in a few seconds." : "Already checking — give it a moment.");
}

function toggleTags() {
  showTags = !showTags;
  try { localStorage.setItem("showTags", showTags ? "1" : "0"); } catch { /* ignore */ }
  if (M) render();
}

// ---------- helpers ----------
function playerById(id) { return M.players.find((p) => p.user_id === id); }

function labelsFor() {
  // Shared labels, or the viewer's own labels for per-player sets (they share letters).
  const seen = new Map();
  for (const p of M.problems) if (!seen.has(p.label)) seen.set(p.label, { label: p.label, points: p.points });
  return [...seen.values()];
}

function myEnd() {
  if (M.me && M.me.end_time) return M.me.end_time;
  return M.end_time;
}

function settingsSummary(s) {
  const items = [
    ["Rules", M.rules === "icpc" ? "ICPC" : "Lockout"],
    ["Problems", s.custom_problems ? `Custom: ${s.custom_problems}` : `${s.count} · ${s.rating_min}–${s.rating_max}`],
  ];
  if (!s.custom_problems && s.tags_include.length) items.push(["Tags", s.tags_include.join(", ")]);
  if (!s.custom_problems && s.tags_avoid.length) items.push(["Avoid", s.tags_avoid.join(", ")]);
  if (!s.custom_problems && s.avoid_interactive) items.push(["Interactive", "avoided"]);
  items.push(["Duration", `${s.duration_min} min`]);
  if (s.freeze_min) items.push(["Freeze", `last ${s.freeze_min} min`]);
  if (s.handicap) items.push(["Handicap", handicapText(s.handicap)]);
  return el("dl.settings-list", items.map(([k, v]) => [el("dt", k), el("dd", v)]));
}

function handicapText(h) {
  const who = (id) => (playerById(id) || {}).handle || "?";
  if (h.type === "penalty") return `${who(h.user_id)} starts at −${h.minutes} penalty`;
  if (h.type === "extra_time") return `${who(h.user_id)} gets +${h.minutes} min`;
  return "Each player gets problems near their own rating";
}

// ---------- render ----------
function render() {
  if (!M) return;
  const parts = [header()];
  if (M.cf_api === "down") parts.push(el("div.banner.bad", "Codeforces API is not answering. Timers keep running; results will be confirmed once it's back."));
  if (M.status === "lobby" || M.status === "starting") parts.push(lobby());
  else if (M.status === "cancelled") parts.push(el("div.empty", "This match was cancelled."));
  else if (M.status === "running" && serverNow() < M.start_time) parts.push(countdown());
  else if (M.status === "running") parts.push(...live());
  else parts.push(...ended());
  app.replaceChildren(el("div.stack", parts));
  afterRender();
}

function afterRender() {
  const box = $("#standings-box");
  if (box) flipNew(box, prevOk, M.standings);
  prevOk = okSet(M.standings);
  tick();
}

function renderStandingsOnly() {
  const box = $("#standings-box");
  if (!box) return render();
  box.replaceChildren(standingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myId }));
  const cards = $("#problem-cards");
  if (cards) cards.replaceWith(problemCards());
  afterRender();
}

function header() {
  const modeName = { friend: "Friend duel", group: "Group room", ranked: "Ranked" }[M.mode] || M.mode;
  const link = `${location.origin}/m/${code}`;
  return el("div.match-head",
    el("div",
      el("h1", modeName),
      el("div.match-meta",
        el("span.chip", M.rules === "icpc" ? "ICPC" : "Lockout"),
        el("span.code-pill.small", code),
        isPlayer() ? null : el("span.chip", "Spectating"))),
    el("div.share",
      el("button.btn.small", { type: "button", onclick: () => copy(link) }, M.status === "lobby" ? "Copy invite link" : "Copy watch link")));
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Link copied"); }
  catch { prompt("Copy this link", text); }
}

function lobby() {
  const s = M.settings;
  const creator = M.creator_id === myId;
  const needed = M.mode === "group" ? 3 : 2;
  const players = el("div.lobby-players",
    M.players.map((p) => el("div.lobby-player",
      el("span", el("span", { class: `ready-dot${p.ready ? " on" : ""}`, "aria-hidden": "true" }),
        handleEl(p.handle, p.cf_rating),
        p.user_id === M.creator_id ? el("span.muted.small", "  host") : null,
        p.needs_reverify ? el("span.danger.small", "  must re-verify handle") : null),
      el("span.small.muted", p.ready ? "Ready" : "Not ready"))));

  const maxPlayers = M.mode === "group" ? 10 : 2;
  const waiting = M.players.length < needed
    ? el("p.muted", M.mode === "group" ? `Waiting for players (${M.players.length}/${needed} minimum).` : "Waiting for your friend to join.")
    : null;

  let actions;
  if (!myId) {
    actions = el("a.btn.primary", { href: `/login?next=/m/${code}` }, "Log in to join");
  } else if (!isPlayer()) {
    actions = M.players.length < maxPlayers && M.mode !== "ranked"
      ? el("button.btn.primary.big", { type: "button", onclick: () => act("join") }, "Join match")
      : el("p.muted", "This match is full.");
  } else {
    const ready = M.me.ready;
    actions = el("div.actions",
      M.status === "starting"
        ? el("span.muted", el("span.spinner"), "  Checking handles and picking problems…")
        : el("button.btn.big" + (ready ? "" : ".primary"), { type: "button", onclick: () => act("ready", { ready: !ready }) },
          ready ? "Not ready" : "Ready"),
      M.mode !== "ranked" ? el("button.btn.ghost", { type: "button", onclick: async () => { if (await act("leave")) location.href = "/"; } }, "Leave") : null,
      creator && M.mode !== "ranked" ? el("a.btn.ghost", { href: `/new?edit=${code}` }, "Edit settings") : null);
  }

  return el("div.stack",
    el("section.stack", el("div.section-title", "Players"), players, waiting),
    actions,
    el("section.stack", el("div.section-title", "Settings"), settingsSummary(s)),
    creator && M.mode !== "ranked" && M.players.length >= 2 ? handicapEditor() : null,
    el("p.small.muted", "Problems are revealed when everyone is ready. You submit on Codeforces; we track it."));
}

function handicapEditor() {
  const s = M.settings;
  const h = s.handicap || { type: "none" };
  const types = [["none", "None"], ["extra_time", "Extra time"]];
  if (M.rules === "icpc") types.splice(1, 0, ["penalty", "Penalty bonus"]);
  if (M.rules === "icpc" && !s.custom_problems) types.push(["per_player_range", "Own rating range"]);
  const typeSel = el("select", { id: "h-type" }, types.map(([v, t]) => el("option", { value: v, selected: v === h.type }, t)));
  const playerSel = el("select", { id: "h-player" }, M.players.map((p) =>
    el("option", { value: p.user_id, selected: p.user_id === h.user_id }, p.handle)));
  const minutes = el("input", { type: "number", id: "h-min", min: 1, max: 300, value: h.minutes || (h.type === "extra_time" ? 15 : 40) });
  const ranges = {};
  const ratingOpts = (v) => { const o = []; for (let r = 800; r <= 3500; r += 100) o.push(el("option", { value: r, selected: r === v }, String(r))); return o; };
  const rangeRows = M.players.map((p) => {
    const def = (h.ranges && h.ranges[String(p.user_id)]) || defaultRange(p.cf_rating);
    const lo = el("select", { "aria-label": `${p.handle} from` }, ratingOpts(def[0]));
    const hi = el("select", { "aria-label": `${p.handle} to` }, ratingOpts(def[1]));
    ranges[p.user_id] = [lo, hi];
    return el("div.row", el("span", { style: "min-width:120px" }, handleEl(p.handle, p.cf_rating, { link: false })), lo, el("span.muted", "–"), hi);
  });
  const body = el("div.stack");
  const draw = () => {
    const t = typeSel.value;
    body.replaceChildren(
      t === "penalty" || t === "extra_time"
        ? el("div.grid-2",
          el("div.field", el("label", { for: "h-player" }, "Player"), playerSel),
          el("div.field", { style: "margin-top:0" }, el("label", { for: "h-min" }, t === "penalty" ? "Starts at minus (minutes)" : "Extra minutes"), minutes))
        : t === "per_player_range" ? el("div.stack", rangeRows) : el("span"));
  };
  typeSel.addEventListener("change", draw);
  draw();
  const save = async () => {
    const t = typeSel.value;
    let handicap = null;
    if (t === "penalty" || t === "extra_time") handicap = { type: t, user_id: Number(playerSel.value), minutes: Number(minutes.value) };
    if (t === "per_player_range") {
      handicap = { type: t, ranges: {} };
      for (const [uid, [lo, hi]] of Object.entries(ranges)) handicap.ranges[uid] = [Number(lo.value), Number(hi.value)];
    }
    try {
      await api(`/api/matches/${code}/settings`, { method: "PATCH", body: { rules: M.rules, settings: { ...M.settings, handicap } } });
      toast("Handicap saved");
    } catch (e) { toast(e.message, true); }
  };
  return el("section.stack.narrow-section",
    el("div.section-title", "Handicap"),
    el("div.field", el("label", { for: "h-type" }, "Type"), typeSel),
    body,
    el("button.btn.small", { type: "button", onclick: save }, "Save handicap"));
}

function defaultRange(r) {
  const base = Math.round((r > 0 ? r : 1200) / 100) * 100;
  return [Math.max(800, base - 100), Math.min(3500, base + 200)];
}

function countdown() {
  return el("div", { "aria-live": "assertive" }, el("div.countdown", { id: "countdown" }, "3"),
    el("p.muted", { style: "text-align:center" }, "Problems coming up. Open Codeforces in another tab."));
}

function live() {
  const parts = [];
  const end = myEnd();
  parts.push(el("div.timer-wrap",
    el("div.timer", { id: "timer", role: "timer", "aria-label": "Time left" }, fmtDuration(end - serverNow())),
    el("div.timer-sub", { id: "timer-sub" }, M.me && M.me.end_time !== M.end_time ? "Your time (includes extra time)" : "")));
  if (M.frozen) parts.push(el("div.banner.freeze", "Scoreboard frozen. You see your own results; others are hidden until the end."));
  if (M.me && M.me.forfeited) parts.push(el("div.banner.bad", "You forfeited this match."));
  parts.push(problemCards());
  parts.push(el("div.actions",
    isPlayer() && !M.me.forfeited ? el("button.btn.primary", { type: "button", onclick: submitted, title: "Shortcut: S" }, "I submitted") : null,
    el("button.btn.ghost.small", { type: "button", onclick: toggleTags, "aria-pressed": String(showTags), title: "Shortcut: T" }, showTags ? "Hide tags" : "Show tags"),
    isPlayer() && !M.me.forfeited ? el("button.btn.ghost.danger.small", { type: "button", onclick: confirmForfeit }, "Forfeit") : null));
  parts.push(el("section.stack", el("div.section-title", "Standings"),
    el("div", { id: "standings-box" }, standingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myId }))));
  parts.push(el("p.small.muted", "Verdicts can take a while to appear when many people are playing. Press “I submitted” (or S) after you submit and we'll check right away. Times always come from Codeforces."));
  return parts;
}

async function confirmForfeit() {
  if (!confirm("Forfeit this match? It counts as a loss.")) return;
  await act("forfeit");
}

function problemCards() {
  const owners = new Map(); // label -> handle that owns it (lockout)
  const solvedByMe = new Set();
  for (const r of M.standings) {
    for (const c of r.cells) {
      if (c.state === "ok" && c.first) owners.set(c.label, r);
      if (c.state === "ok" && r.user_id === myId) solvedByMe.add(c.label);
    }
  }
  const perPlayer = M.problems.some((p) => p.owner !== null);
  const list = perPlayer && isPlayer() ? M.problems.filter((p) => p.owner === myId) : M.problems;
  return el("div.problems", { id: "problem-cards" }, list.map((p) => {
    const owner = M.rules === "lockout" ? owners.get(p.label) : null;
    const cls = ["problem", solvedByMe.has(p.label) && (!owner || owner.user_id === myId) ? "solved" : "",
      owner && owner.user_id !== myId ? "taken" : ""].join(" ");
    const ownerPlayer = perPlayer && p.owner ? playerById(p.owner) : null;
    return el("article", { class: cls },
      el("div.problem-top",
        el("span.problem-letter", p.label),
        el("a.problem-name", { href: p.url || cfProblemUrl(p.key), target: "_blank", rel: "noopener" }, p.name)),
      el("div.problem-meta",
        el("span", p.key),
        el("span", p.rating ? String(p.rating) : "—"),
        M.rules === "lockout" ? el("span.pts", `${fmtPts(p.points)} pts`) : null),
      showTags && p.tags.length ? el("div.chips", p.tags.map((t) => el("span.chip", t))) : null,
      owner ? el("div.owner.muted", "Taken by ", handleEl(owner.handle, owner.cf_rating, { link: false })) : null,
      ownerPlayer && !isPlayer() ? el("div.owner.muted", "For ", handleEl(ownerPlayer.handle, ownerPlayer.cf_rating, { link: false })) : null);
  }));
}

function resultTitle() {
  if (M.result_status !== "final") return el("div.banner", el("span.spinner"), "Waiting for Codeforces to confirm the last submissions…");
  const top = M.standings.filter((r) => r.place === 1);
  let text;
  if (top.length > 1) text = top.some((r) => r.user_id === myId) ? "Draw" : "Draw at the top";
  else if (top[0] && top[0].user_id === myId) text = "You won";
  else if (isPlayer()) text = M.mode === "group" ? `You placed ${myRow() ? myRow().place : "—"}` : "You lost";
  else text = top[0] ? `${top[0].handle} wins` : "Match over";
  const reason = { clinched: "Decided early — nobody could catch up.", forfeit: "Ended by forfeit.", all_solved: "Everything was solved." }[M.end_reason];
  return el("div.stack", el("h2.result-title", text), reason ? el("p.muted", reason) : null);
}

function ratingChanges() {
  const rows = M.players.filter((p) => p.rating_before !== null && p.rating_after !== null);
  if (!rows.length) return null;
  return el("section.stack", el("div.section-title", "Duel rating"),
    el("div.row", rows.map((p) => {
      const d = p.rating_after - p.rating_before;
      return el("div.card",
        handleEl(p.handle, p.cf_rating),
        el("div.kpi", String(p.rating_after)),
        el("div", { class: `rating-delta mono ${d >= 0 ? "up" : "down"}` }, `${d >= 0 ? "+" : ""}${d}`));
    })));
}

function ended() {
  const parts = [resultTitle()];
  parts.push(ratingChanges());
  parts.push(el("div.actions",
    isPlayer() && M.mode !== "ranked" ? el("button.btn.primary", { type: "button", onclick: rematch }, M.rematch_code ? "Join rematch" : "Rematch") : null,
    isPlayer() && M.mode === "ranked" ? el("a.btn.primary", { href: "/queue" }, "Queue again") : null,
    el("a.btn.ghost", { href: "/" }, "Home")));
  parts.push(el("section.stack", el("div.section-title", "Final standings"),
    el("div", { id: "standings-box" }, standingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myId }))));
  parts.push(problemList());
  parts.push(timelineSection());
  return parts;
}

async function rematch() {
  const r = await act("rematch");
  if (r) location.href = `/m/${r.code}`;
}

function problemList() {
  return el("section.stack", el("div.section-title", "Problems"),
    el("div.table-wrap", el("table",
      el("thead", el("tr", el("th", ""), el("th", "Problem"), el("th.num", "Rating"), el("th", "Links"))),
      el("tbody", M.problems.map((p) => el("tr",
        el("td.mono", p.label),
        el("td", el("a", { href: p.url || cfProblemUrl(p.key), target: "_blank", rel: "noopener" }, p.name), el("span.muted.small.mono", `  ${p.key}`)),
        el("td.num", p.rating ? String(p.rating) : "—"),
        el("td", p.contest_url ? el("a.small", { href: p.contest_url, target: "_blank", rel: "noopener", title: "The editorial is listed under Contest materials" }, "Editorial") : null)))))),
    el("p.small.muted", "Problems you didn't solve were added to your ", el("a", { href: "/upsolve" }, "upsolve list"), "."));
}

function timelineSection() {
  const box = el("section.stack", { id: "timeline" }, el("div.section-title", "Timeline"));
  if (M.result_status !== "final") { box.append(el("p.muted.small", "The timeline appears once the result is confirmed.")); return box; }
  if (timeline === null) {
    timeline = "loading";
    api(`/api/matches/${code}/timeline`).then((t) => { timeline = t; if (!revealing) render(); }).catch(() => { timeline = { items: [] }; });
    box.append(el("p.muted.small", "Loading…"));
    return box;
  }
  if (timeline === "loading") { box.append(el("p.muted.small", "Loading…")); return box; }
  if (!timeline.items.length) { box.append(el("p.empty", "No submissions.")); return box; }
  box.append(el("div.timeline", timeline.items.map((it) => {
    const cls = it.verdict === "OK" ? "ok" : it.verdict === "COMPILATION_ERROR" || it.verdict === "TESTING" ? "neutral" : "bad";
    return el("div.tl-item",
      el("span.tl-min", `${it.minute}'`),
      el("span", handleEl(it.handle, it.cf_rating), el("span.mono", `  ${it.label}`)),
      it.url ? el("a", { class: `verdict ${cls}`, href: it.url, target: "_blank", rel: "noopener" }, verdictText(it.verdict))
        : el("span", { class: `verdict ${cls}` }, verdictText(it.verdict)));
  })));
  return box;
}

function verdictText(v) {
  return { OK: "Accepted", WRONG_ANSWER: "Wrong answer", TIME_LIMIT_EXCEEDED: "Time limit", MEMORY_LIMIT_EXCEEDED: "Memory limit",
    RUNTIME_ERROR: "Runtime error", COMPILATION_ERROR: "Compilation error", TESTING: "Testing", IDLENESS_LIMIT_EXCEEDED: "Idleness limit" }[v] || v.replaceAll("_", " ").toLowerCase();
}

// ---------- freeze reveal ----------
async function playReveal(data) {
  revealing = true;
  M.standings = data.reveal.start;
  render();
  await sleep(900);
  for (const step of data.reveal.steps) {
    const before = okSet(M.standings);
    M.standings = step.rows;
    const box = $("#standings-box");
    if (box) {
      box.replaceChildren(standingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myId }));
      flipNew(box, before, M.standings);
      const cell = box.querySelector(`tr[data-user="${step.user_id}"] .cell[data-label="${step.label}"]`);
      if (cell && !cell.classList.contains("flip")) cell.closest("tr").classList.add("row-flash");
    }
    await sleep(650);
  }
  M.standings = data.rows;
  revealing = false;
  render();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- clock ----------
function tick() {
  if (!M || M.status !== "running") return;
  const now = serverNow();
  const cd = $("#countdown");
  if (cd) {
    const left = Math.ceil(M.start_time - now);
    if (left <= 0) render(); else cd.textContent = String(left);
    return;
  }
  const t = $("#timer");
  if (!t) return;
  const left = myEnd() - now;
  t.textContent = fmtDuration(left);
  t.classList.toggle("low", left > 0 && left <= 300);
  t.classList.toggle("done", left <= 0);
  if (left <= 0) $("#timer-sub").textContent = "Time's up — waiting for the others to finish";
}
setInterval(tick, 250);
