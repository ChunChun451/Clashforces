// Everyone on Clashforces: who's around, what's happening, and challenges.
import { $, api, el, emptyState, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { fmtCountdown } from "./time.js";

mountTopbar("/players");
const app = $("#app");
const me = await getMe();

let players = [];
let feed = { live: [], items: [], server_time: Date.now() / 1000 };
let challenges = { incoming: [], outgoing: [] };
let query = "";
let offset = 0; // server time - local time (s)
const now = () => Date.now() / 1000 + offset;

const MODE_NAME = { friend: "Friend duel", group: "Group room", ranked: "Ranked", team: "Team duel", contest: "Contest" };

function ago(t) {
  const s = Math.max(0, now() - t);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function names(list, max = 3) {
  return [list.slice(0, max).map((p, i) => [i ? ", " : "", handleEl(p.handle, p.cf_rating)]),
    list.length > max ? el("span.muted", ` +${list.length - max}`) : ""];
}

function settingsLine(s) {
  const parts = [s.rules === "icpc" ? "ICPC" : "Lockout"];
  if (s.team_size) parts.unshift(Array(s.team_count).fill(s.team_size).join("v"));
  if (s.custom) parts.push("own problem list");
  else parts.push(`${s.count} ${s.count === 1 ? "problem" : "problems"}`, `${s.rating_min}–${s.rating_max}`);
  if (s.duration_min) parts.push(`${s.duration_min} min`);
  return parts.join(" · ");
}

async function load() {
  const [p, a, c] = await Promise.all([
    api("/api/players").then((r) => r.players).catch(() => players),
    api("/api/activity").catch(() => feed),
    me.user ? api("/api/challenges").catch(() => challenges) : Promise.resolve(challenges),
  ]);
  players = p; feed = a; challenges = c;
  offset = feed.server_time - Date.now() / 1000;
  render();
}

// ---------- actions ----------

async function challenge(handle) {
  if (!me.user) { location.href = `/login?next=${encodeURIComponent("/players")}`; return; }
  const fresh = await api("/api/me");
  const active = fresh.active;
  if (active && active.kind === "queue") { toast("Leave the ranked queue first.", true); return; }
  if (active && active.status !== "lobby") { toast("Finish your current match first.", true); return; }
  if (!active) { location.href = `/new?challenge=${encodeURIComponent(handle)}`; return; }
  try {
    const r = await api(`/api/matches/${active.code}/invite`, { method: "POST", body: { handle } });
    toast(r.online ? `Challenge sent to ${r.handle}.` : `Challenge saved. ${r.handle} sees it next time they're on.`);
    setTimeout(() => { location.href = `/m/${active.code}`; }, 900);
  } catch (e) { toast(e.message, true); }
}

async function accept(c) {
  try {
    await api(`/api/matches/${c.code}/join`, { method: "POST" });
    location.href = `/m/${c.code}`;
  } catch (e) {
    toast(e.message, true);
    await load();
  }
}

async function decline(c) {
  try { await api(`/api/challenges/${c.id}/decline`, { method: "POST" }); } catch (e) { toast(e.message, true); }
  await load();
}

// ---------- pieces ----------

function challengesSection() {
  const { incoming, outgoing } = challenges;
  if (!incoming.length && !outgoing.length) return null;
  const items = [
    ...incoming.map((c) => el("li",
      el("div.ch-main",
        el("span", handleEl(c.from.handle, c.from.cf_rating), " challenged you to a ", el("strong", (MODE_NAME[c.mode] || c.mode).toLowerCase())),
        el("span.meta", settingsLine(c.settings), " · ", ago(c.at))),
      el("div.row",
        el("button.btn.ghost.small", { type: "button", onclick: () => decline(c) }, "Decline"),
        el("button.btn.primary.small", { type: "button", onclick: () => accept(c) }, "Accept")))),
    ...outgoing.map((c) => el("li",
      el("div.ch-main",
        el("span", "You challenged ", handleEl(c.to.handle, c.to.cf_rating)),
        el("span.meta", { class: `meta ${c.status === "declined" ? "danger" : ""}` },
          { waiting: "Waiting for them to join", joined: "Joined your lobby", declined: "Declined" }[c.status], " · ", ago(c.at))),
      el("a.btn.small", { href: `/m/${c.code}` }, "Open lobby"))),
  ];
  return el("section.panel.challenges",
    el("div.panel-head", el("h2", "Challenges", incoming.length ? el("span.count", String(incoming.length)) : null),
      el("span.small.muted", "Open while the lobby is")),
    el("ul.panel-list", items));
}

// One line per member: dot, handle, then a quiet status. The Challenge button shows on hover
// (always on touch screens), so the list doesn't read as a wall of buttons.
function status(p) {
  if (p.is_me) return "you";
  if (p.playing) return el("a", { href: `/m/${p.playing.code}` }, "in a match · watch");
  if (p.online) return "online";
  const seen = p.last_seen_at || p.joined_at;
  return seen ? ago(seen) : "";
}

function playerRow(p) {
  const canChallenge = !p.is_me && !p.playing;
  return el("li", { class: canChallenge ? "pl-row" : null },
    el("div.pl-main",
      el("span", { class: "online-dot" + (p.online || p.playing ? " on" : ""), "aria-hidden": "true" }),
      el("span.pl-name", handleEl(p.handle, p.cf_rating), p.is_friend ? el("span.pl-tag", { title: "You follow them" }, "friend") : null)),
    el("div.pl-side",
      el("span.meta", { class: `meta${p.online || p.playing ? " accent" : ""}` }, status(p)),
      canChallenge ? el("button.btn.small.pl-act", { type: "button", onclick: () => challenge(p.handle),
        title: p.online ? `Challenge ${p.handle}` : `${p.handle} is offline. They'll see it when they're back.` }, "Challenge") : null));
}

function playersPanel() {
  const q = query.trim().toLowerCase();
  const list = q ? players.filter((p) => p.handle.toLowerCase().includes(q)) : players;
  const online = players.filter((p) => p.online || p.playing).length;
  const search = el("input.mono.pl-search", { type: "search", placeholder: "Find a handle", value: query, "aria-label": "Find a player",
    autocomplete: "off", spellcheck: "false", oninput: (e) => { query = e.target.value; refreshList(); } });
  return el("section.panel.players-panel",
    el("div.panel-head", el("h2", "Members"), el("span.small.muted", `${online} online · ${players.length}`)),
    el("div.pl-search-wrap", search),
    el("div.panel-scroll", { id: "pl-list" }, listBody(list, q)));
}

function listBody(list, q) {
  if (!list.length) {
    return q ? emptyState({ iconName: "users", title: "No match", text: `Nobody here has a handle with “${q}”.` })
      : emptyState({ iconName: "users", title: "No members yet", text: "Players show up here once they log in." });
  }
  return el("ul.panel-list.pl-list", list.map(playerRow));
}

function refreshList() {
  const q = query.trim().toLowerCase();
  const box = $("#pl-list");
  if (box) box.replaceChildren(listBody(q ? players.filter((p) => p.handle.toLowerCase().includes(q)) : players, q));
}

const feedRow = (main, side) => el("li", el("span.feed-main", main), el("span.feed-side", side));

function liveRow(m) {
  const left = m.end_time ? m.end_time - now() : null;
  return feedRow(
    [el("span.live-word", el("span.pulse", { "aria-hidden": "true" }), "Live"), " ", m.title ? el("strong", m.title) : names(m.players, 2)],
    [left !== null ? el("span.mono", { dataset: { until: String(m.end_time) } }, `${fmtCountdown(left)} left`) : "",
      el("a", { href: `/m/${m.code}` }, "Watch")]);
}

function sideText(s) {
  return s.name ? el("strong", s.name) : names(s.players, 2);
}

function matchItem(it) {
  const [a, b] = it.sides;
  let line;
  if (it.mode === "contest") line = [el("strong", it.title || "Contest"), a ? [" won by ", sideText(a)] : " finished"];
  else if (it.n_sides > 2) line = [sideText(a), it.draw ? " tied first of " : " won a room of ", String(it.n_sides)];
  else if (it.draw) line = [sideText(a), " drew with ", sideText(b)];
  else line = [sideText(a), it.end_reason === "forfeit" ? " won by forfeit vs " : " beat ", sideText(b)];
  return feedRow(line, el("a", { href: `/m/${it.code}`, title: `${MODE_NAME[it.mode] || it.mode} · ${it.rules === "icpc" ? "ICPC" : "Lockout"}` }, ago(it.at)));
}

// Sign-ups next to each other in time collapse into one line.
function joinedGroup(list) {
  const shown = list.slice(0, 3);
  return feedRow(
    [shown.map((it, i) => [i ? ", " : "", handleEl(it.handle, it.cf_rating)]),
      list.length > 3 ? el("span.muted", ` +${list.length - 3}`) : "", " joined"],
    ago(list[0].at));
}

function activityPanel() {
  const rows = feed.live.map(liveRow);
  let joins = [];
  const flush = () => { if (joins.length) rows.push(joinedGroup(joins)); joins = []; };
  for (const it of feed.items) {
    if (it.kind === "joined") { joins.push(it); continue; }
    flush();
    rows.push(matchItem(it));
  }
  flush();
  return el("section.panel.activity-panel",
    el("div.panel-head", el("h2", "Activity"), feed.live.length ? el("span.small.accent", `${feed.live.length} live`) : null),
    rows.length
      ? el("div.panel-scroll", el("ul.panel-list.feed", rows))
      : el("div.panel-fill", emptyState({ iconName: "history", title: "Quiet so far", text: "Finished matches and new members show up here." })));
}

function render() {
  app.replaceChildren(el("div.page",
    el("div.page-head",
      el("div", el("h1", "Players"),
        el("p.lede", "Challenge anyone, even if they're offline. They'll see it when they're back.")),
      me.user ? el("a.btn.ghost", { href: "/friends" }, "Friends & leaderboard") : el("a.btn.primary", { href: "/login?next=/players" }, "Log in to challenge")),
    challengesSection(),
    el("div.grid-2.equal.community", playersPanel(), activityPanel())));
}

function tick() {
  for (const node of app.querySelectorAll("[data-until]")) {
    const left = Number(node.dataset.until) - now();
    if (left <= 0) { load(); return; }
    node.textContent = `${fmtCountdown(left)} left`;
  }
}

await load();
setInterval(tick, 1000);
setInterval(() => { if (!document.hidden && !document.activeElement?.matches?.(".pl-search")) load(); }, 30000);

if (me.user) {
  const ws = await import("./ws.js");
  ws.on("challenges", () => load());
  ws.on("challenge_declined", () => load());
  ws.on("presence", () => load());
}
