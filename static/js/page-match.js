// The match page: lobby -> countdown -> live scoreboard -> result + timeline.
import { $, api, cfProblemUrl, el, emptyState, fmtDuration, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { connect, on, send, serverNow, subscribe, syncClock } from "./ws.js";
import { fmtPts, flipNew, okSet, standingsTable, teamBadge, teamClass, teamStandingsTable } from "./standings.js";
import { fmtCountdown, fmtLocal } from "./time.js";
import { icon } from "./icons.js";
import { notify } from "./notify.js";
import { teamChat } from "./team-chat.js";

mountTopbar("");
const app = $("#app");
const code = location.pathname.split("/").pop().toUpperCase();
// Per-team invite links: /m/CODE?team=2 (1-based) opens straight on "Join Team B".
const inviteTeam = (() => { const t = Number(new URLSearchParams(location.search).get("team")); return Number.isInteger(t) && t >= 1 ? t - 1 : null; })();
const me = await getMe();
const myId = me.user ? me.user.id : null;

let M = null;           // latest match.state
let prevOk = null;      // accepted cells at the last render
let revealing = false;  // freeze reveal animation in progress
let timeline = null;
let showTags = false;
try { showTags = localStorage.getItem("showTags") === "1"; } catch { /* ignore */ }

const isPlayer = () => M && M.me !== null;
const isTeam = () => !!(M && M.teams);
const isContest = () => M && M.mode === "contest";
const isHost = () => !!(M && M.is_host);
const myRow = () => M && M.standings.find((r) => (isTeam() ? r.team === M.my_team : r.user_id === myId));
const teamName = (t) => (M && M.teams && M.teams[t] ? M.teams[t].name : `Team ${"ABCDEF"[t]}`);
// Contests with a freeze: the final result stays hidden until the host's reveal is done.
const revealStatus = () => (M && M.reveal ? M.reveal.status : null);
const resultLocked = () => revealStatus() === "waiting" || revealStatus() === "running";
const chat = teamChat(code, myId);
let announceOpen = false;   // "show all" on the announcements list
let renaming = null;        // team index whose name is being edited

// The standings grid for this match: players, or teams with their members.
function board() {
  return isTeam()
    ? teamStandingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myTeam: isPlayer() ? M.my_team : null, myId })
    : standingsTable({ rows: M.standings, labels: labelsFor(), rules: M.rules, myId });
}

// ---------- live connection ----------
on("match.state", (data) => {
  if (data.code !== code) return;
  syncClock(data.server_time);
  const was = M;
  M = data;
  if (was && (was.status !== M.status || was.result_status !== M.result_status
    || (was.reveal && was.reveal.status) !== (M.reveal && M.reveal.status))) timeline = null;
  chat.update(M);
  if (!revealing) render();
});
on("match.reveal", (d) => {
  if (!M || revealing) return;
  M.reveal = d.reveal;
  if (d.standings) M.standings = d.standings;
  render();
});
on("standings", (data) => {
  if (!M || revealing) return;
  M.standings = data.standings;
  for (const c of data.changed || []) {
    if (c.user_id === myId || !c.accepted || !isPlayer()) continue;
    if (isTeam() && c.team === M.my_team) toast(`Teammate ${c.handle} solved ${c.label}`);
    else notify(isTeam() ? `${c.handle} (${teamName(c.team)}) solved ${c.label}` : `${c.handle} solved ${c.label}`, "opponent");
  }
  renderStandingsOnly();
});
on("match.ended", () => notify("Match over", "ended"));
on("match.warning", (d) => { if (d.code === code) notify(`${d.minutes_left} minutes left`, "warning"); });
on("match.result", (data) => {
  if (data.reveal && M && M.status === "ended") playReveal(data);
});
on("lobby.blocked", (d) => toast(d.message, true));
on("match.announcement", (d) => {
  if (!M) return;
  M.announcements = [d, ...(M.announcements || []).filter((a) => a.id !== d.id)];
  if (!isHost()) { toast(`Announcement: ${d.body}`); notify(`Announcement: ${d.body}`, "announcement", { always: false }); }
  const box = $("#announcements");
  if (box && !revealing) box.replaceWith(announcementsPanel()); else if (!revealing) render();
});
on("match.kicked", (d) => { if (d.code === code) toast("The host removed you from this contest.", true); });
on("match.dropped", (d) => { if (d.code === code) toast(d.message, true); });
on("draft.pick", (d) => toast(d.auto ? `Time's up: ${d.handle} goes to ${d.name}` : `${d.name} picked ${d.handle}`));
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
  if ((e.key === " " || e.key === "ArrowRight") && isHost() && resultLocked() && M.result_status === "final" && !e.target.closest("button, a")) {
    e.preventDefault();
    api(`/api/contests/${code}/reveal`, { method: "POST", body: { action: revealStatus() === "waiting" ? "start" : "next" } }).catch((ex) => toast(ex.message, true));
  }
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

function settingsSummary(s, wide = false) {
  const items = [
    ["Rules", M.rules === "icpc" ? "ICPC" : "Lockout"],
    ["Problems", s.custom_problems ? `Custom: ${s.custom_problems}` : `${s.count} · ${s.rating_min}–${s.rating_max}`],
  ];
  if (!s.custom_problems && s.tags_include.length) items.push(["Tags", s.tags_include.join(", ")]);
  if (!s.custom_problems && s.tags_avoid.length) items.push(["Avoid", s.tags_avoid.join(", ")]);
  if (!s.custom_problems && s.since_year) items.push(["From", `${s.since_year} on`]);
  if (!s.custom_problems && s.avoid_interactive) items.push(["Interactive", "avoided"]);
  items.push(["Duration", `${s.duration_min} min`]);
  if (s.freeze_min) items.push(["Freeze", `last ${s.freeze_min} min`]);
  if (s.handicap) items.push(["Handicap", handicapText(s.handicap)]);
  if (isContest()) {
    items.splice(1, 1, ["Problems", `${M.contest.problem_count} · set by the host`]);
    if (!isTeam()) items.splice(1, 0, ["Players", `up to ${M.contest.max_players} · solo`]);
    items.push(["Starts", M.contest.scheduled_at ? fmtLocal(M.contest.scheduled_at) : "when the host starts it"]);
  }
  if (isTeam()) {
    const count = M.team_count || 2;
    const teams = count === 2 ? `${M.team_size} vs ${M.team_size}` : `${count} teams of ${M.team_size}`;
    items.splice(1, 0, ["Teams", `${teams}${s.fill === "draft" ? " · captains' draft" : ""}${isContest() ? "" : " · unrated"}`]);
  }
  if (wide) return el("dl.settings-grid", items.map(([k, v]) => el("div", el("dt", k), el("dd", v))));
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
  else if (M.status === "cancelled" && isContest()) parts.push(emptyState({ iconName: "calendar", title: "This contest was cancelled", text: "The host cancelled it before the start.", action: el("a.btn.small.primary", { href: "/events" }, "Browse events") }));
  else if (M.status === "cancelled") parts.push(el("div.empty-state.boxed", el("strong", "This match was cancelled"), el("p", "Everyone left the lobby before it started."), el("a.btn.small.primary", { href: "/new" }, "Start a new duel")));
  else if (M.status === "running" && serverNow() < M.start_time) parts.push(countdown());
  else if (M.status === "running") parts.push(...live());
  else parts.push(...ended());
  const kept = keepInputs();
  app.replaceChildren(el("div.page", parts));
  restoreInputs(kept);
  afterRender();
}

// Live updates re-render the page; don't throw away what someone is typing.
function keepInputs() {
  const out = [];
  for (const node of app.querySelectorAll("input[id], textarea[id]")) {
    out.push({ id: node.id, value: node.value, focused: document.activeElement === node,
      start: node.selectionStart, end: node.selectionEnd });
  }
  return out;
}

function restoreInputs(kept) {
  for (const k of kept) {
    const node = document.getElementById(k.id);
    if (!node) continue;
    node.value = k.value;
    if (k.focused) {
      node.focus();
      try { node.setSelectionRange(k.start, k.end); } catch { /* number inputs */ }
    }
  }
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
  box.replaceChildren(board());
  const cards = $("#problem-cards");
  if (cards) cards.replaceWith(problemCards());
  afterRender();
}

function header() {
  const count = M.team_count || 2;
  const teamTitle = count === 2 ? `Team duel · ${M.team_size}v${M.team_size}` : `Team duel · ${count} teams of ${M.team_size}`;
  const modeName = isContest() ? M.contest.title
    : { friend: "Friend duel", group: "Group room", ranked: "Ranked", team: teamTitle }[M.mode] || M.mode;
  const link = `${location.origin}/m/${code}`;
  const format = isContest() ? (isTeam() ? `${count} teams × ${M.team_size}` : "Solo") : null;
  return el("div.match-head",
    el("div",
      el("h1", modeName),
      el("div.match-meta",
        isContest() ? el("span.chip", "Contest") : null,
        format ? el("span.chip", format) : null,
        el("span.chip", M.rules === "icpc" ? "ICPC" : "Lockout"),
        el("span.code-pill", { title: "Match code" }, code),
        isHost() && isContest() ? el("span.chip.on", "Hosting") : isPlayer() ? null : el("span.chip", "Spectating"),
        isContest() && !isHost() && M.contest.host ? el("span.small.muted", "Hosted by ", handleEl(M.contest.host.handle, M.contest.host.cf_rating)) : null,
        isTeam() && isPlayer() && M.my_team !== null ? el("span.chip.team-chip", teamBadge(M.my_team), `You're on ${teamName(M.my_team)}`) : null),
      isContest() && M.contest.description ? el("p.lede.small.contest-desc", M.contest.description) : null),
    el("div.share",
      isHost() && isContest() ? el("a.btn", { href: `/m/${code}/screen`, target: "_blank", rel: "noopener", title: "A scoreboard for a projector or TV" }, icon("screen", 16), "Open big screen")
        : !isPlayer() ? el("a.btn.ghost.small", { href: `/m/${code}/screen`, target: "_blank", rel: "noopener", title: "A scoreboard for a projector or TV" }, "Big screen") : null,
      el("button.btn", { type: "button", onclick: () => copy(link) }, M.status === "lobby" ? "Copy invite link" : "Copy watch link")));
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Link copied"); }
  catch { prompt("Copy this link", text); }
}

function lobby() {
  if (isTeam()) return teamLobby();
  if (isContest()) return soloContestLobby();
  const s = M.settings;
  const creator = M.creator_id === myId;
  const needed = M.mode === "group" ? 3 : 2;
  const maxPlayers = M.mode === "group" ? 10 : 2;
  const rows = M.players.map((p) => el("div.lobby-player",
    el("span.who", el("span", { class: `ready-dot${p.ready ? " on" : ""}`, "aria-hidden": "true" }),
      handleEl(p.handle, p.cf_rating),
      p.user_id === M.creator_id ? el("span.chip", "host") : null,
      p.user_id === myId ? el("span.chip.you", "you") : null,
      p.needs_reverify ? el("span.danger.small", "must re-verify handle") : null),
    el("span", { class: p.ready ? "side small accent" : "side small muted" }, p.ready ? "Ready" : "Not ready")));
  // 1v1: show the empty seat, so the panel reads as "waiting for one more".
  if (M.mode !== "group") for (let i = M.players.length; i < maxPlayers; i++) {
    rows.push(el("div.lobby-player.slot", el("span.who", el("span.ready-dot", { "aria-hidden": "true" }), M.mode === "ranked" ? "Opponent" : "Waiting for your friend")));
  }
  const waiting = M.players.length < needed
    ? (M.mode === "group" ? [`Waiting for players: ${M.players.length} of at least ${needed}.`,
      isPlayer() ? [" Share the link or ", el("a", { href: "/players" }, "challenge someone"), "."] : ""]
      : isPlayer() && M.mode !== "ranked" ? ["Share the invite link or ", el("a", { href: "/players" }, "challenge a player"), " to fill the empty seat."]
        : "Share the invite link to fill the empty seat.")
    : M.players.every((p) => p.ready) ? "Everyone is ready." : `Waiting for ${M.players.filter((p) => !p.ready).map((p) => p.handle).join(", ")} to press Ready.`;

  let actions;
  if (!myId) {
    actions = el("div.actions", el("a.btn.primary", { href: `/login?next=/m/${code}` }, "Log in to join"));
  } else if (!isPlayer()) {
    actions = M.players.length < maxPlayers && M.mode !== "ranked"
      ? el("div.actions", el("button.btn.primary", { type: "button", onclick: () => act("join") }, "Join match"))
      : el("p.muted", "This match is full. You can watch it.");
  } else {
    const ready = M.me.ready;
    actions = el("div.actions",
      M.status === "starting"
        ? el("span.muted", el("span.spinner"), "  Checking handles and picking problems…")
        : el("button.btn" + (ready ? "" : ".primary"), { type: "button", onclick: () => act("ready", { ready: !ready }) },
          ready ? "Not ready" : "Ready"),
      M.mode !== "ranked" ? el("button.btn.ghost", { type: "button", onclick: async () => { if (await act("leave")) location.href = "/"; } }, "Leave") : null,
      creator && M.mode !== "ranked" ? el("a.btn.ghost", { href: `/new?edit=${code}` }, "Edit settings") : null);
  }

  return el("div.stack.lobby",
    el("div.lobby-cols",
      el("section.panel.lobby-panel",
        el("div.panel-head", el("h2", "Players"), el("span.team-meta", `${M.players.length}/${maxPlayers}`)),
        el("div.team-slots", rows),
        el("p.panel-foot", { role: "status" }, waiting)),
      el("section.panel.lobby-panel",
        el("div.panel-head", el("h2", "Settings")),
        el("div.panel-body", settingsSummary(s)),
        el("p.panel-foot", "Problems are revealed when everyone is ready. You submit on Codeforces; we track it."))),
    M.mode === "ranked" && isPlayer() ? el("p.note", "Opponent found. Press Ready within 60 seconds or the match is cancelled.") : null,
    el("div.lobby-bar", actions),
    creator && M.mode !== "ranked" && M.players.length >= 2 ? handicapEditor() : null);
}

// A player row in a lobby. Hosts get move / kick controls; captains are marked.
function playerRow(p, { teamIdx = null, captainOf = null } = {}) {
  const contest = isContest();
  const manage = isHost() && M.status === "lobby";
  const controls = [];
  if (manage && contest && isTeam()) {
    controls.push(el("select.move-select", { "aria-label": `Move ${p.handle}`, onchange: (e) => {
      const v = e.target.value;
      if (v !== "") act("move", { user_id: p.user_id, team: v === "pool" ? null : Number(v) });
    } },
    el("option", { value: "" }, "Move to…"),
    M.teams.filter((t) => t.team !== teamIdx).map((t) => el("option", { value: t.team }, t.name)),
    M.settings.fill === "draft" && teamIdx !== null ? el("option", { value: "pool" }, "Back to the pool") : null));
  }
  if (manage && contest) {
    controls.push(el("button.btn.ghost.small.danger", { type: "button", title: `Remove ${p.handle} from the contest`,
      onclick: async () => { if (confirm(`Remove ${p.handle}? They can't rejoin unless you allow it.`)) await hostAct("kick", { user_id: p.user_id }); } }, "Remove"));
  }
  const status = contest ? null
    : el("span", { class: p.ready ? "side small accent" : "side small muted" }, p.ready ? "Ready" : "Not ready");
  const row = el("div.lobby-player", {
    draggable: manage && isTeam() ? "true" : null,
    dataset: { player: String(p.user_id) },
    ondragstart: manage && isTeam() ? (e) => { e.dataTransfer.setData("text/plain", String(p.user_id)); e.dataTransfer.effectAllowed = "move"; } : null,
  },
  el("span.who", contest ? null : el("span", { class: `ready-dot${p.ready ? " on" : ""}`, "aria-hidden": "true" }),
    handleEl(p.handle, p.cf_rating),
    !contest && p.user_id === M.creator_id ? el("span.chip", "host") : null,
    captainOf !== null ? el("span.chip", "captain") : null,
    p.user_id === myId ? el("span.chip.you", "you") : null,
    p.needs_reverify ? el("span.danger.small", "must re-verify handle") : null),
  controls.length ? el("span.side.row-tools", status, controls) : status);
  return row;
}

function teamLobby() {
  const size = M.team_size;
  const count = M.team_count || 2;
  const manage = isHost() && M.status === "lobby";
  const contest = isContest();
  const draft = M.draft;
  const drafting = draft && draft.active;
  const rating = (p) => p.cf_rating || 1200;
  const pool = M.players.filter((p) => p.team === null || p.team === undefined);

  const teamPanel = (t) => {
    const team = M.teams[t];
    const members = M.players.filter((p) => p.team === t);
    const total = members.reduce((a, p) => a + rating(p), 0);
    const open = size - members.length;
    let action = null;
    const free = !draft;
    if (free && myId && isPlayer() && M.my_team !== t && open > 0 && M.status === "lobby") {
      action = el("button.btn.small", { type: "button", onclick: () => act("team", { team: t }) }, "Switch here");
    } else if (free && myId && !isPlayer() && !isHost() && open > 0 && M.status === "lobby") {
      action = el(`button.btn.small${inviteTeam === t || (inviteTeam === null && count === 2) ? ".primary" : ""}`, { type: "button", onclick: () => joinTeam(t) }, `Join ${team.name}`);
    }
    const captain = team.captain_id;
    const rows = members.map((p) => playerRow(p, { teamIdx: t, captainOf: p.user_id === captain ? t : null }));
    if (manage && draft && !draft.active && !captain && pool.length) {
      rows.push(el("div.lobby-player.slot", el("span.who", "No captain yet"),
        el("select.move-select", { "aria-label": `Captain for ${team.name}`, onchange: (e) => { if (e.target.value) act("captain", { team: t, user_id: Number(e.target.value) }); } },
          el("option", { value: "" }, "Pick a captain…"), pool.map((p) => el("option", { value: p.user_id }, p.handle)))));
    }
    for (let i = rows.length; i < size; i++) rows.push(el("div.lobby-player.slot", el("span.who", el("span.ready-dot", { "aria-hidden": "true" }), "Open slot")));
    const turn = drafting && draft.team === t;
    const nameNode = !manage ? team.name : renaming === t ? renameForm(t)
      : el("button.team-rename", { type: "button", title: "Rename team", "aria-label": `Rename ${team.name}`,
        onclick: () => { renaming = t; render(); const i = $(`#rename-${t}`); if (i) { i.focus(); i.select(); } } }, team.name);
    const panel = el(`section.panel.team-panel.${teamClass(t)}${turn ? ".picking" : ""}`, { "aria-label": team.name },
      el("div.panel-head",
        el("h2.team-name", teamBadge(t, team.name), nameNode),
        el("div.row.tight",
          el("span.team-meta", { title: "Players and total Codeforces rating" }, `${members.length}/${size}`, el("span.sep", "·"), `Σ ${total}`),
          action,
          manage && (contest || count > 2) ? el("button.btn.ghost.small.icon-sm", { type: "button", title: `Copy the invite link for ${team.name}`,
            "aria-label": `Copy the invite link for ${team.name}`, onclick: () => copy(`${location.origin}/m/${code}?team=${t + 1}`) }, icon("link", 16)) : null)),
      el("div.team-slots", rows));
    if (manage) {
      panel.addEventListener("dragover", (e) => { e.preventDefault(); panel.classList.add("drop"); });
      panel.addEventListener("dragleave", () => panel.classList.remove("drop"));
      panel.addEventListener("drop", (e) => {
        e.preventDefault();
        panel.classList.remove("drop");
        const uid = Number(e.dataTransfer.getData("text/plain"));
        if (uid) act("move", { user_id: uid, team: t });
      });
    }
    return panel;
  };

  const notReady = M.players.filter((p) => !p.ready);
  let status;
  if (M.status === "starting") status = el("div.banner", { role: "status" }, el("span.spinner"), contest ? "Starting the contest…" : "Checking handles and picking problems…");
  else if (drafting) {
    const picker = M.players.find((p) => p.user_id === M.teams[draft.team].captain_id);
    status = el("div.banner.wait", { role: "status" }, el("span.wait-label", "Draft"),
      el("span.grow", el("span.team-name", teamBadge(draft.team), `${teamName(draft.team)} is picking`), picker ? el("span.muted", ` · captain ${picker.handle}`) : null),
      el("span.mono.draft-clock", { id: "draft-clock" }, fmtDuration(draft.deadline - serverNow())));
  } else if (contest) status = scheduleBanner();
  else if (M.start_blocked) status = el("div.banner.wait", { role: "status" }, el("span.wait-label", "Can't start yet"), el("span.grow", M.start_blocked));
  else if (notReady.length) status = el("div.banner.wait", { role: "status" }, el("span.wait-label", "Teams are set"),
    el("span.grow", `Waiting for ${notReady.map((p) => p.handle).join(", ")} to press Ready.`));
  else status = el("div.banner.good", { role: "status" }, el("span.pulse"), "Everyone is ready. Starting…");

  let actions;
  if (!myId) {
    actions = el("div.actions", el("a.btn.primary", { href: `/login?next=${encodeURIComponent(`/m/${code}${location.search}`)}` }, contest ? "Log in to join" : "Log in to join"));
  } else if (isHost() && contest) {
    actions = null;
  } else if (!isPlayer()) {
    const room = M.players.length < count * size;
    actions = room
      ? el("div.actions", el("button.btn.primary", { type: "button", onclick: () => (inviteTeam !== null && !draft ? joinTeam(inviteTeam) : act("join")) },
        inviteTeam !== null && !draft && M.teams[inviteTeam] ? `Join ${M.teams[inviteTeam].name}` : contest ? "Join contest" : "Join match"),
      el("span.small.muted", draft ? "the captains pick the teams" : "or pick a team above"))
      : el("p.muted", contest ? "This contest is full. You can watch it." : "This match is full. You can watch it.");
  } else {
    const ready = M.me.ready;
    actions = el("div.actions",
      M.status === "starting" || contest ? null
        : el("button.btn" + (ready ? "" : ".primary"), { type: "button", onclick: () => act("ready", { ready: !ready }) }, ready ? "Not ready" : "Ready"),
      el("button.btn.ghost", { type: "button", onclick: async () => { if (await act("leave")) location.href = contest ? "/events" : "/"; } }, contest ? "Leave contest" : "Leave"),
      isHost() && !contest ? el("a.btn.ghost", { href: `/new?edit=${code}` }, "Edit settings") : null);
  }
  const tools = manage && M.players.length >= 2 && M.status === "lobby" && !drafting
    ? el("div.actions.tools",
      draft && !draft.active && !draft.done ? el("button.btn", { type: "button", onclick: () => act("draft/start"), disabled: M.teams.some((t) => !t.captain_id) || null,
        title: "Captains take turns picking players" }, "Start draft") : null,
      el("button.btn", { type: "button", onclick: () => arrange("shuffle"), title: "Random sides" }, "Shuffle"),
      el("button.btn", { type: "button", onclick: () => arrange("balance"), title: "Split players so the total ratings are as close as possible" }, "Balance by rating"))
    : null;

  const grid = count === 2
    ? el("div.teams-grid", teamPanel(0), el("div.vs", { "aria-hidden": "true" }, "vs"), teamPanel(1))
    : el(`div.teams-grid.many.n${count}`, M.teams.map((t) => teamPanel(t.team)));

  return el("div.stack.team-lobby",
    grid,
    pool.length || (draft && !draft.done) ? poolPanel(pool, draft) : null,
    status,
    actions || tools ? el("div.lobby-bar", actions || el("span"), tools) : null,
    contest ? contestPanels() : el("section.panel", el("div.panel-head", el("h2", "Settings")), el("div.panel-body", settingsSummary(M.settings, true))),
    contest ? null : el("p.small.muted", count === 2
      ? "Problems are revealed when both teams are full and everyone is ready. A problem counts for your team when any member solves it."
      : "Problems are revealed when every team is full and everyone is ready. A problem counts for your team when any member solves it."));
}

// Players waiting for a team: the captains' draft pool (or anyone not seated yet).
function poolPanel(pool, draft) {
  const myTurn = draft && draft.active && M.teams[draft.team] && M.teams[draft.team].captain_id === myId;
  const rows = pool.map((p) => el("div.lobby-player",
    el("span.who", handleEl(p.handle, p.cf_rating), el("span.mono.small.muted", String(p.cf_rating || "unrated")),
      p.user_id === myId ? el("span.chip.you", "you") : null),
    el("span.side",
      myTurn ? el("button.btn.small.primary", { type: "button", onclick: () => act("draft/pick", { user_id: p.user_id }) }, "Pick") : null,
      isHost() && M.status === "lobby" && isContest() ? el("button.btn.ghost.small.danger", { type: "button",
        onclick: async () => { if (confirm(`Remove ${p.handle}?`)) await hostAct("kick", { user_id: p.user_id }); } }, "Remove") : null)));
  const note = draft && draft.active ? (myTurn ? "Your pick. The strongest player left is picked for you when time runs out." : "Captains pick in snake order.")
    : draft && !draft.done ? (isHost() ? "Pick a captain for every team, then start the draft." : "The host is choosing captains.")
      : "Not on a team yet. They join the smallest team at the start.";
  return el("section.panel.pool-panel",
    el("div.panel-head", el("h2", draft ? "Draft pool" : "Waiting for a team"), el("span.team-meta", `${pool.length} left`)),
    rows.length ? el("div.team-slots.pool-grid", rows) : el("div.panel-body", emptyState({ title: "Everyone has a team", text: "Nobody is waiting to be picked." })),
    el("p.panel-foot", { role: "status" }, note));
}

function renameForm(t) {
  const input = el("input.team-name-input", { type: "text", id: `rename-${t}`, value: M.teams[t].name, maxlength: 24, "aria-label": "Team name" });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") { renaming = null; render(); } });
  return el("form.rename-form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api(`/api/matches/${code}/teams/${t}/name`, { method: "PUT", body: { name: input.value } });
      renaming = null;
      render();
    } catch (ex) { toast(ex.message, true); }
  } }, input, el("button.btn.small", { type: "submit" }, "Save"));
}

// ---------- hosted contests ----------

async function hostAct(path, body) {
  try { return await api(`/api/contests/${code}/${path}`, { method: "POST", body }); }
  catch (e) { toast(e.message, true); return null; }
}

function scheduleBanner() {
  const c = M.contest;
  if (M.status === "starting") return el("div.banner", { role: "status" }, el("span.spinner"), "Starting the contest…");
  if (c.start_note) return el("div.banner.wait", { role: "status" }, el("span.wait-label", "Can't start yet"), el("span.grow", c.start_note));
  if (!c.scheduled_at) return el("div.banner.wait", { role: "status" }, el("span.wait-label", "Start"), el("span.grow", isHost() ? "Press Start now when everyone is here." : "The host starts the contest."));
  const left = c.scheduled_at - serverNow();
  if (left <= 0) return el("div.banner", { role: "status" }, el("span.spinner"), "Starting now…");
  return el("div.banner.wait.schedule", { role: "status" }, el("span.wait-label", "Starts in"),
    el("span.mono.sched-clock", { id: "sched-clock" }, fmtCountdown(left)),
    el("span.grow.muted", fmtLocal(c.scheduled_at), " · your time"));
}

function soloContestLobby() {
  const max = M.contest.max_players;
  const rows = M.players.map((p) => playerRow(p));
  if (!rows.length) rows.push(el("div.lobby-player.slot", el("span.who", "Nobody has joined yet. Share the link.")));
  if (rows.length % 2 === 1) rows.push(el("div.lobby-player.slot", el("span.who", M.players.length < max ? "Open slot" : "")));
  let actions = null;
  if (!myId) actions = el("div.actions", el("a.btn.primary", { href: `/login?next=${encodeURIComponent(`/m/${code}`)}` }, "Log in to join"));
  else if (isPlayer()) actions = el("div.actions", el("button.btn.ghost", { type: "button", onclick: async () => { if (await act("leave")) location.href = "/events"; } }, "Leave contest"));
  else if (!isHost()) actions = M.players.length < max
    ? el("div.actions", el("button.btn.primary", { type: "button", onclick: () => act("join") }, "Join contest"))
    : el("p.muted", "This contest is full. You can watch it.");
  return el("div.stack.lobby",
    scheduleBanner(),
    el("section.panel.lobby-panel",
      el("div.panel-head", el("h2", "Players"), el("span.team-meta", `${M.players.length}/${max}`)),
      el("div.team-slots.two-col", rows),
      el("p.panel-foot", "Everyone plays for themselves on one scoreboard. Problems appear at the start.")),
    actions,
    contestPanels());
}

function contestPanels() {
  return el("div.stack",
    isHost() ? hostLobbyPanel() : null,
    el("div.grid-2.equal.contest-pair",
      el("section.panel",
        el("div.panel-head", el("h2", "Settings")),
        el("div.panel-body.panel-fill", settingsSummary(M.settings)),
        el("p.panel-foot", "Problems stay hidden until the start. You submit on Codeforces; we track it.")),
      announcementsPanel()));
}

function hostLobbyPanel() {
  const h = M.host || { problems: [], warnings: [], kicked: [], unsynced: [] };
  const blocked = M.start_blocked || M.contest.start_note;
  const lockout = M.rules === "lockout";
  return el("section.panel.host-panel",
    el("div.panel-head", el("div", el("h2", "Host controls"), el("p.small.muted", "Only you see this. The problems stay hidden from everyone else until the start.")),
      el("span.chip.on", "Host")),
    el("div.panel-body.stack",
      el("div.actions",
        el("button.btn.primary", { type: "button", disabled: M.status !== "lobby" || null, onclick: () => hostAct("start", { force: false }) }, "Start now"),
        isTeam() && blocked ? el("button.btn", { type: "button", title: "Start with the teams as they are, even if uneven", onclick: () => { if (confirm("Start with uneven teams?")) hostAct("start", { force: true }); } }, "Start anyway") : null,
        el("a.btn", { href: `/events/new?edit=${code}` }, "Edit contest"),
        el("button.btn.ghost.danger", { type: "button", onclick: async () => { if (confirm("Cancel this contest for everyone?") && await hostAct("cancel")) toast("Contest cancelled"); } }, "Cancel contest")),
      blocked && M.start_blocked ? el("p.small.warn", M.start_blocked) : null,
      h.warnings.length ? el("div.stack.host-warnings", h.warnings.map((w) => el("p.small.warn", w))) : null,
      h.unsynced.length ? el("p.small.muted", `Still loading the Codeforces history of ${h.unsynced.join(", ")}. It's re-checked 5 minutes before the start.`) : null),
    h.problems.length ? el("div.host-problems", el("table",
      el("thead", el("tr", el("th", ""), el("th", "Problem"), el("th.num", "Rating"), lockout ? el("th.num", "Points") : null, el("th", "Already tried"))),
      el("tbody", h.problems.map((p) => el("tr",
        el("td.mono", p.label),
        el("td", el("a", { href: p.url || cfProblemUrl(p.key), target: "_blank", rel: "noopener" }, p.name), el("span.muted.small.mono", `  ${p.key}`)),
        el("td.num", p.rating ? String(p.rating) : "—"),
        lockout ? el("td.num", String(p.points)) : null,
        el("td", p.tried_by.length ? el("span.warn.small", p.tried_by.join(", ")) : el("span.muted.small", "nobody"))))))) : null,
    h.kicked.length ? el("div.panel-foot.kicked",
      el("span", "Removed: "),
      h.kicked.map((k) => el("span.kicked-item", el("span.mono", k.handle),
        el("button.btn.ghost.small", { type: "button", onclick: () => hostAct("allow", { user_id: k.user_id }) }, "Allow back")))) : null);
}

function hostRunningPanel() {
  const minutes = el("input.mono", { type: "number", id: "extend-min", min: 1, max: 120, value: 10, inputmode: "numeric", "aria-label": "Minutes to add" });
  const extend = async (m) => { const r = await hostAct("extend", { minutes: m }); if (r) toast(`Extended by ${m} min`); };
  return el("section.panel.host-panel",
    el("div.panel-head", el("h2", "Host controls"), el("span.chip.on", "Host")),
    el("div.panel-body.stack.panel-fill",
      el("div.field", el("label", { for: "extend-min" }, "Extend time for everyone (minutes)"),
        el("div.inline-form", minutes, el("button.btn", { type: "button", onclick: () => extend(Number(minutes.value)) }, "Extend"))),
      el("div.chips", [5, 15, 30].map((m) => el("button.chip", { type: "button", onclick: () => extend(m) }, `+${m} min`)))),
    el("div.panel-foot.spread", el("span", `Ends ${fmtLocal(M.end_time, { weekday: false })}`),
      el("button.btn.ghost.small.danger", { type: "button", onclick: async () => { if (confirm("End the contest now for everyone?")) await hostAct("end"); } }, "End contest now")));
}

function announcementsPanel() {
  const list = M.announcements || [];
  const shown = announceOpen ? list : list.slice(0, 4);
  const box = el("textarea.announce-input", { id: "announce-text", maxlength: 500, rows: 2, placeholder: "Clarification, schedule change, ...", "aria-label": "Announcement" });
  const composer = isHost() && M.status !== "cancelled" ? el("form.announce-form", { onsubmit: async (e) => {
    e.preventDefault();
    const r = await hostAct("announcements", { body: $("#announce-text").value });
    if (r && $("#announce-text")) $("#announce-text").value = "";
  } }, box, el("button.btn.small.primary", { type: "submit" }, "Post")) : null;
  return el("section.panel.announce-panel", { id: "announcements" },
    el("div.panel-head", el("h2", "Announcements", list.length ? el("span.count", String(list.length)) : null),
      list.length > 4 ? el("button.btn.ghost.small", { type: "button", onclick: () => { announceOpen = !announceOpen; $("#announcements").replaceWith(announcementsPanel()); } }, announceOpen ? "Show fewer" : "Show all") : null),
    composer ? el("div.panel-body.small-pad", composer) : null,
    list.length
      ? el("div.panel-fill", el("ul.panel-list.announce-list", shown.map((a) => el("li",
        el("p.announce-body", a.body),
        el("span.meta.mono", { title: fmtLocal(a.created_at) }, new Date(a.created_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))))))
      : el("div.panel-fill", emptyState({ iconName: "megaphone", title: "No announcements yet",
        text: isHost() ? "Post clarifications here. Everyone watching sees them right away." : "Messages from the host show up here, live." })));
}

async function joinTeam(t) {
  await act("join", { team: t });
}

async function arrange(how) {
  const r = await act("teams", { how });
  if (r) toast(how === "balance" ? "Teams balanced by rating" : "Teams shuffled");
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
    return el("div.row.range-row", el("span.who", handleEl(p.handle, p.cf_rating, { link: false })), lo, el("span.muted", "–"), hi);
  });
  const body = el("div.stack");
  const draw = () => {
    const t = typeSel.value;
    body.replaceChildren(
      t === "penalty" || t === "extra_time"
        ? el("div.grid-2",
          el("div.field", el("label", { for: "h-player" }, "Player"), playerSel),
          el("div.field", el("label", { for: "h-min" }, t === "penalty" ? "Starts at minus (minutes)" : "Extra minutes"), minutes))
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
  return el("section.panel",
    el("div.panel-head", el("h2", "Handicap"), el("span.small.muted", "Optional. Evens out a rating gap.")),
    el("div.panel-body.handicap-body",
      el("div.field", el("label", { for: "h-type" }, "Type"), typeSel),
      body,
      el("div.handicap-save", el("button.btn", { type: "button", onclick: save }, "Save handicap"))));
}

function defaultRange(r) {
  const base = Math.round((r > 0 ? r : 1200) / 100) * 100;
  return [Math.max(800, base - 100), Math.min(3500, base + 200)];
}

function countdown() {
  return el("div", { "aria-live": "assertive" }, el("div.countdown", { id: "countdown" }, "3"),
    el("p.muted.center", "Problems coming up. Open Codeforces in another tab."));
}

function live() {
  const parts = [];
  const end = myEnd();
  parts.push(el("div.timer-wrap",
    el("div.timer-label", "Time left"),
    el("div.timer", { id: "timer", role: "timer", "aria-label": "Time left" }, fmtDuration(end - serverNow())),
    el("div.timer-sub", { id: "timer-sub" }, M.me && M.me.end_time !== M.end_time ? "Your time (includes extra time)" : ""),
    el("div.timer-bar", { "aria-hidden": "true" }, el("span", { id: "timer-bar" }))));
  const many = (M.team_count || 2) > 2;
  if (M.frozen) parts.push(el("div.banner.freeze", isTeam()
    ? (isPlayer() ? (many ? "Scoreboard frozen. You see your team live; other teams' new submissions are hidden until the end."
      : "Scoreboard frozen. You see your team live; the other team's new submissions are hidden until the end.")
      : many ? "Scoreboard frozen. New submissions from every team are hidden until the end."
        : "Scoreboard frozen. New submissions from both teams are hidden until the end.")
    : isPlayer() ? "Scoreboard frozen. You see your own results; others are hidden until the end."
      : "Scoreboard frozen. New submissions are hidden until the end."));
  if (M.me && M.me.forfeited) parts.push(el("div.banner.bad", "You forfeited this match."));
  parts.push(el("div.stack", problemCards(), el("div.actions",
    isPlayer() && !M.me.forfeited ? el("button.btn.primary", { type: "button", onclick: submitted, title: "Shortcut: S" }, "I submitted") : null,
    el("button.btn.ghost", { type: "button", onclick: toggleTags, "aria-pressed": String(showTags), title: "Shortcut: T" }, showTags ? "Hide tags" : "Show tags"),
    isPlayer() && !M.me.forfeited ? el("button.btn.ghost.danger", { type: "button", onclick: confirmForfeit }, "Forfeit") : null)));
  if (isContest()) {
    parts.push(isHost() ? el("div.grid-2.equal.contest-pair", hostRunningPanel(), announcementsPanel()) : announcementsPanel());
  }
  parts.push(el("section.stack", el("h2.section-title", "Standings"),
    el("div", { id: "standings-box" }, board())));
  parts.push(el("p.small.muted", "Verdicts can take a while to appear when many people are playing. Press “I submitted” (or S) after you submit and we'll check right away. Times always come from Codeforces."));
  return parts;
}

async function confirmForfeit() {
  if (!confirm("Forfeit this match? It counts as a loss.")) return;
  await act("forfeit");
}

function problemCards() {
  const owners = new Map(); // label -> row that owns it (lockout)
  const solvedByMe = new Set();
  const mine = (r) => (isTeam() ? isPlayer() && r.team === M.my_team : r.user_id === myId);
  for (const r of M.standings) {
    for (const c of r.cells) {
      if (c.state === "ok" && c.first) owners.set(c.label, { ...r, by: c.by_handle });
      if (c.state === "ok" && mine(r)) solvedByMe.add(c.label);
    }
  }
  const perPlayer = M.problems.some((p) => p.owner !== null);
  const list = perPlayer && isPlayer() ? M.problems.filter((p) => p.owner === myId) : M.problems;
  // Columns follow the problem count so the last row is never a lone card: 3 → 3, 5 → 3+2, 8 → 4+4.
  const n = list.length;
  const cols = n <= 4 ? n : { 5: 3, 6: 3, 7: 4, 8: 4, 9: 3, 10: 5 }[n] || 4;
  const colsMd = n <= 3 ? n : n % 2 === 0 ? 2 : 3; // tablets
  return el("div.problems", { id: "problem-cards", style: `--cols: ${Math.max(1, cols)}; --cols-md: ${Math.max(1, colsMd)}` }, list.map((p) => {
    const owner = M.rules === "lockout" ? owners.get(p.label) : null;
    const cls = ["problem", solvedByMe.has(p.label) && (!owner || mine(owner)) ? "solved" : "",
      owner && !mine(owner) ? "taken" : ""].join(" ");
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
      owner && isTeam() ? el("div.owner.muted", "Taken by ", teamBadge(owner.team), ` ${owner.name}`, owner.by ? ` · ${owner.by}` : "") : null,
      owner && !isTeam() ? el("div.owner.muted", "Taken by ", handleEl(owner.handle, owner.cf_rating, { link: false })) : null,
      ownerPlayer && !isPlayer() ? el("div.owner.muted", "For ", handleEl(ownerPlayer.handle, ownerPlayer.cf_rating, { link: false })) : null);
  }));
}

function resultTitle() {
  if (M.result_status !== "final") return el("div.banner", el("span.spinner"), "Waiting for Codeforces to confirm the last submissions…");
  if (resultLocked()) {
    const rv = M.reveal;
    return el("div.stack",
      el("h2.result-title", rv.status === "running" ? "Revealing the results" : "Results are frozen"),
      el("p.muted", rv.status === "running"
        ? `Frozen submissions are shown one at a time, from the bottom up · ${rv.step} of ${rv.total}`
        : isHost() ? "Start the reveal when the room is ready. The big screen follows along." : "The host reveals the frozen submissions next. Watch the big screen."));
  }
  const top = M.standings.filter((r) => r.place === 1);
  let text;
  const reason = { clinched: "Decided early — nobody could catch up.", forfeit: "Ended by forfeit.", all_solved: "Everything was solved.", host: "Ended early by the host." }[M.end_reason];
  if (isTeam()) {
    const many = M.standings.length > 2;
    const won = top.length === 1 ? top[0] : null;
    const mine = myRow();
    if (!won) text = many && isPlayer() && mine && mine.place !== 1 ? `Your team placed ${mine.place}` : many ? "Shared first place" : "Draw";
    else if (isPlayer()) text = won.team === M.my_team ? "Your team won" : many && mine ? `Your team placed ${mine.place} of ${M.standings.length}` : "Your team lost";
    else text = `${won.name} wins`;
    const names = won ? won.members.map((m) => m.handle).join(" & ") : "";
    const sub = won ? el("p.team-name.muted", teamBadge(won.team), isPlayer() ? `${won.name} won · ${names}` : names)
      : el("p.muted", many ? `${top.map((r) => r.name).join(" and ")} share first place.` : "Both teams finished level.");
    return el("div.stack", el("h2.result-title", text), sub, reason ? el("p.muted", reason) : null);
  }
  if (top.length > 1) text = top.some((r) => r.user_id === myId) ? "Draw" : "Draw at the top";
  else if (top[0] && top[0].user_id === myId) text = "You won";
  else if (isPlayer()) text = M.mode === "group" || isContest() ? `You placed ${myRow() ? myRow().place : "—"}` : "You lost";
  else text = top[0] ? `${top[0].handle} wins` : "Match over";
  return el("div.stack", el("h2.result-title", text), reason ? el("p.muted", reason) : null);
}

function ratingChanges() {
  const rows = M.players.filter((p) => p.rating_before !== null && p.rating_after !== null);
  if (!rows.length) return null;
  return el("section.stack", el("h2.section-title", "Duel rating"),
    el("div.rating-cards", rows.map((p) => {
      const d = p.rating_after - p.rating_before;
      return el("div.card",
        handleEl(p.handle, p.cf_rating),
        el("div.kpi", String(p.rating_after)),
        el("div", { class: `rating-delta mono ${d >= 0 ? "up" : "down"}` }, `${d >= 0 ? "+" : ""}${d}`));
    })));
}

function ended() {
  const parts = [resultTitle()];
  if (isHost() && resultLocked()) parts.push(revealPanel());
  parts.push(ratingChanges());
  parts.push(el("div.actions",
    isPlayer() && M.mode !== "ranked" && !isContest() ? el("button.btn.primary", { type: "button", onclick: rematch }, M.rematch_code ? "Join rematch" : "Rematch") : null,
    isPlayer() && M.mode === "ranked" ? el("a.btn.primary", { href: "/queue" }, "Queue again") : null,
    isContest() ? el("a.btn.primary", { href: "/events" }, "Back to events") : null,
    el("a.btn.ghost", { href: "/" }, "Home")));
  parts.push(el("section.stack", el("h2.section-title", resultLocked() ? (M.frozen ? "Standings (frozen)" : "Standings") : "Final standings"),
    el("div", { id: "standings-box" }, board())));
  if (isContest() && (M.announcements || []).length) parts.push(announcementsPanel());
  parts.push(problemList());
  parts.push(timelineSection());
  return parts;
}

// The host runs the freeze reveal here or on the big screen (Space / →); every screen follows.
function revealPanel() {
  const rv = M.reveal;
  const go = async (action) => {
    try { await api(`/api/contests/${code}/reveal`, { method: "POST", body: { action } }); }
    catch (e) { toast(e.message, true); }
  };
  const running = rv.status === "running";
  return el("section.panel.host-panel.reveal-panel",
    el("div.panel-head", el("div", el("h2", "Freeze reveal"),
      el("p.small.muted", "From the last place up, each frozen cell is resolved and the board re-ranks. Everyone watching sees the same step.")),
    el("span.chip.on", "Host")),
    el("div.panel-body",
      el("div.actions",
        running ? el("button.btn.primary", { type: "button", onclick: () => go("next") }, "Next cell")
          : el("button.btn.primary", { type: "button", onclick: () => go("start") }, "Start reveal"),
        el("button.btn", { type: "button", "aria-pressed": String(!!rv.auto), onclick: () => go(rv.auto ? "pause" : "auto") }, rv.auto ? "Pause" : "Auto-play"),
        el("button.btn.ghost", { type: "button", onclick: () => { if (confirm("Skip to the final standings?")) go("skip"); } }, "Skip to final"))),
    el("div.panel-foot.spread",
      el("span", running ? `Step ${rv.step} of ${rv.total}` : `${rv.total} frozen ${rv.total === 1 ? "cell" : "cells"} to reveal`),
      el("div.reveal-bar", { "aria-hidden": "true" }, el("span", { style: `width: ${rv.total ? (rv.step / rv.total) * 100 : 0}%` }))));
}

async function rematch() {
  const r = await act("rematch");
  if (r) location.href = `/m/${r.code}`;
}

function problemList() {
  return el("section.stack", el("h2.section-title", "Problems"),
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
  const box = el("section.stack", { id: "timeline" }, el("h2.section-title", "Timeline"));
  if (M.result_status !== "final") { box.append(el("p.muted.small", "The timeline appears once the result is confirmed.")); return box; }
  if (resultLocked()) { box.append(el("p.muted.small", "The timeline appears after the reveal.")); return box; }
  if (timeline === null) {
    timeline = "loading";
    api(`/api/matches/${code}/timeline`).then((t) => { timeline = t; if (!revealing) render(); }).catch(() => { timeline = { items: [] }; });
    box.append(el("p.muted.small", "Loading…"));
    return box;
  }
  if (timeline === "loading") { box.append(el("p.muted.small", "Loading…")); return box; }
  if (!timeline.items.length) { box.append(el("div.empty-state.boxed", el("p", "Nobody submitted during this match."))); return box; }
  box.append(el("div.timeline", timeline.items.map((it) => {
    const cls = it.verdict === "OK" ? "ok" : it.verdict === "COMPILATION_ERROR" || it.verdict === "TESTING" ? "neutral" : "bad";
    return el("div.tl-item", { class: it.team !== null && it.team !== undefined ? `tl-item ${teamClass(it.team)}` : "tl-item" },
      el("span.tl-min", `${it.minute}'`),
      el("span.tl-who", it.team !== null && it.team !== undefined ? teamBadge(it.team, it.team_name) : null,
        handleEl(it.handle, it.cf_rating), el("span.mono", it.label)),
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
      box.replaceChildren(board());
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
  if (!M) return;
  const now = serverNow();
  if (M.status === "lobby") {
    const sc = $("#sched-clock");
    if (sc && M.contest && M.contest.scheduled_at) {
      const left = M.contest.scheduled_at - now;
      if (left <= 0) render(); else sc.textContent = fmtCountdown(left);
    }
    const dc = $("#draft-clock");
    if (dc && M.draft && M.draft.deadline) dc.textContent = fmtDuration(M.draft.deadline - now);
    return;
  }
  if (M.status !== "running") return;
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
  const bar = $("#timer-bar");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, (left / Math.max(1, myEnd() - M.start_time)) * 100))}%`;
  t.classList.toggle("low", left > 0 && left <= 300);
  t.classList.toggle("done", left <= 0);
  if (left <= 0) $("#timer-sub").textContent = "Time's up — waiting for the others to finish";
}
setInterval(tick, 250);
