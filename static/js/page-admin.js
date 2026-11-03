// Admin page: who the users are and what is happening. The server answers 404 to everyone else.
import { $, api, el, emptyState, handleEl, mountTopbar, toast } from "./api.js";
import { fmtLocal, fmtMinutes } from "./time.js";

mountTopbar("/admin");
const app = $("#app");

const MODES = { friend: "Friend duel", group: "Group room", team: "Team duel", ranked: "Ranked", contest: "Contest" };
const STATES = { lobby: "Lobby", starting: "Starting", running: "Running", ended: "Finished", cancelled: "Cancelled" };
const SORTS = { joined: "Newest", seen: "Last seen", matches: "Most matches", rating: "CF rating", duel: "Duel rating" };

const state = {
  stats: null, activity: null, users: null, matches: null, skew: 0,
  uq: { q: "", sort: "joined", page: 1 },
  mq: { state: "", mode: "", page: 1 },
};

const now = () => Date.now() / 1000 + state.skew;

// "3h ago" (or "in 2d" for the future); the absolute time is in the tooltip.
function ago(ts) {
  if (!ts) return el("span.when", "—");
  const d = Math.round(now() - ts), a = Math.abs(d);
  let t;
  if (a < 60) t = "just now";
  else {
    const n = a < 3600 ? `${Math.floor(a / 60)}m` : a < 86400 ? `${Math.floor(a / 3600)}h` : a < 60 * 86400 ? `${Math.floor(a / 86400)}d` : null;
    t = n === null ? fmtLocal(ts, { weekday: false, year: true }) : d >= 0 ? `${n} ago` : `in ${n}`;
  }
  return el("span.when", { title: fmtLocal(ts, { year: true }) }, t);
}

const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v !== null)).toString();
const num = (n) => Number(n || 0).toLocaleString();

// ---------- data ----------

async function loadStats() {
  [state.stats, state.activity] = await Promise.all([api("/api/admin/stats"), api("/api/admin/activity")]);
  state.skew = state.stats.server_time - Date.now() / 1000;
}
async function loadUsers() { state.users = await api(`/api/admin/users?${qs(state.uq)}`); }
async function loadMatches() { state.matches = await api(`/api/admin/matches?${qs(state.mq)}`); }

async function refresh() {
  try {
    await Promise.all([loadStats(), loadUsers(), loadMatches()]);
    render();
  } catch (e) {
    if (e.status === 404 || e.status === 401) { location.href = "/"; return; }
    toast(e.message, true);
  }
}

async function act(question, path, done) {
  if (!confirm(question)) return;
  try {
    await api(path, { method: "POST" });
    toast(done);
    await refresh();
  } catch (e) { toast(e.message, true); }
}

// ---------- overview ----------

function stat(label, value, sub) {
  return el("div.admin-stat", el("div.kpi", value), el("div.kpi-label", label), el("div.sub", sub || ""));
}

function overview() {
  const s = state.stats, u = s.users, m = s.matches, by = m.by_state;
  const lanes = Object.entries(s.cf.lanes || {}).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ");
  return el("section.panel",
    el("div.panel-head", el("h2", "Overview"),
      el("span.cf-state", { title: lanes ? `Codeforces request queue: ${lanes}` : "Codeforces API" },
        el("span", { class: "online-dot" + (s.cf.status === "ok" ? " on" : "") }),
        `Codeforces ${s.cf.status === "ok" ? "OK" : "down"}`, lanes ? el("span.hide-sm", ` · ${lanes}`) : null)),
    el("div.admin-stats",
      stat("Users", num(u.total), u.banned ? `${u.banned} banned` : "all in good standing"),
      stat("New · 24h", num(u.new_24h), `${num(u.new_7d)} in 7 days`),
      stat("Active now", num(u.active_now), "seen in last 5 min"),
      stat("Online", num(s.live.users_online), `${num(s.live.sockets)} sockets`),
      stat("In queue", num(m.in_queue), `${num(s.live.watched_players)} players polled`),
      stat("Lobbies", num(by.lobby), by.starting ? `${by.starting} starting` : "waiting to start"),
      stat("Running", num(by.running), "matches live"),
      stat("Finished", num(by.ended), `${num(by.cancelled)} cancelled`),
      stat("Matches · 24h", num(m.last_24h), "created"),
      stat("Upcoming contests", num(m.contests_upcoming), "hosted, not started")));
}

// ---------- tables ----------

function pager(data, q, load) {
  const from = data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = Math.min(data.total, data.page * data.page_size);
  const go = async (p) => { q.page = p; try { await load(); } catch (e) { toast(e.message, true); } };
  return el("div.pager",
    el("span", `${num(from)}–${num(to)} of ${num(data.total)}`),
    el("div.actions",
      el("button.btn.small", { type: "button", disabled: data.page <= 1, onclick: () => go(data.page - 1) }, "Previous"),
      el("button.btn.small", { type: "button", disabled: to >= data.total, onclick: () => go(data.page + 1) }, "Next")));
}

function select(id, label, options, value, onchange) {
  return [el("label.sr-only", { for: id }, label),
    el("select", { id, onchange: (e) => onchange(e.target.value) },
      Object.entries(options).map(([v, t]) => el("option", { value: v, selected: v === value }, t)))];
}

let searchTimer = null;
function usersSection() {
  const search = el("input", { id: "admin-q", type: "text", placeholder: "Search handle", value: state.uq.q, autocomplete: "off", spellcheck: "false",
    oninput: (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(async () => {
        state.uq = { ...state.uq, q: e.target.value.trim(), page: 1 };
        try { await loadUsers(); renderUsers(); } catch (ex) { toast(ex.message, true); }
      }, 250);
    } });
  const tools = el("div.admin-tools", el("label.sr-only", { for: "admin-q" }, "Search by handle"), search,
    select("admin-sort", "Sort users", SORTS, state.uq.sort, async (v) => {
      state.uq = { ...state.uq, sort: v, page: 1 };
      try { await loadUsers(); renderUsers(); } catch (e) { toast(e.message, true); }
    }));
  return el("section.stack",
    el("div.spread", el("h2.section-title", { id: "admin-users-title" }, `Users · ${num(state.users.total)}`), tools),
    el("div.stack", { id: "admin-users" }, usersBody()));
}

function usersBody() {
  const d = state.users;
  if (!d.users.length) {
    const empty = emptyState({ iconName: "users", title: state.uq.q ? "No handle matches" : "No users yet",
      text: state.uq.q ? `Nobody's handle contains “${state.uq.q}”.` : "People appear here once they log in with their Codeforces handle." });
    empty.classList.add("boxed");
    return [empty];
  }
  return [el("div.table-wrap", el("table.admin-table",
      el("thead", el("tr", el("th", "Handle"), el("th.num", "CF"), el("th.num", "Duel"), el("th", "Joined"), el("th", "Last seen"),
        el("th.num", "Matches"), el("th.num", { title: "First-place finishes" }, "Wins"), el("th", "Status"), el("th", { "aria-label": "Actions" }))),
      el("tbody", d.users.map((u) => el("tr", { class: u.banned_at ? "banned" : null },
        el("td", el("span", { class: "online-dot" + (u.online ? " on" : ""), title: u.online ? "Online" : "Offline" }),
          u.handle ? handleEl(u.handle, u.cf_rating) : el("span.muted", "no handle"),
          u.handle ? el("a.cf-link", { href: `https://codeforces.com/profile/${encodeURIComponent(u.handle)}`, target: "_blank", rel: "noopener", title: "Codeforces profile" }, "CF ↗") : null),
        el("td.num", u.cf_rating ? String(u.cf_rating) : el("span.muted", "—")),
        el("td.num", String(u.duel_rating)),
        el("td", ago(u.created_at)),
        el("td", ago(u.last_seen_at)),
        el("td.num", String(u.played)),
        el("td.num", String(u.wins)),
        el("td", u.banned_at ? el("span.chip.bad", { title: `Banned ${fmtLocal(u.banned_at, { year: true })}` }, "banned")
          : u.is_admin ? el("span.chip.on", "admin") : u.needs_reverify ? el("span.chip", "re-verify") : el("span.muted.small", "—")),
        el("td.act", u.banned_at
          ? el("button.btn.small.ghost", { type: "button", onclick: () => act(`Unban ${u.handle || "this user"}? They can log in again.`, `/api/admin/users/${u.id}/unban`, `${u.handle || "User"} unbanned`) }, "Unban")
          : u.is_admin ? null
          : el("button.btn.small.ghost.danger", { type: "button", onclick: () => act(`Ban ${u.handle || "this user"}? They are signed out, removed from lobbies and the queue, and can't log in until unbanned.`, `/api/admin/users/${u.id}/ban`, `${u.handle || "User"} banned`) }, "Ban"))))))),
    d.total > d.page_size ? pager(d, state.uq, async () => { await loadUsers(); renderUsers(); }) : null];
}

function duration(m) {
  if (!m.start_time) return el("span.muted", "—");
  const end = m.ended_at || (m.status === "running" ? now() : null);
  if (!end) return el("span.muted", "—");
  return el("span.when", fmtMinutes(Math.max(0, Math.round((end - m.start_time) / 60))));
}

function stateChip(m) {
  const cls = m.status === "running" ? "chip.on" : m.status === "cancelled" ? "chip.bad" : "chip";
  const label = m.status === "ended" && m.result_status !== "final" ? "Confirming" : STATES[m.status] || m.status;
  return el(`span.${cls}`, label);
}

function matchesSection() {
  const reload = async () => { state.mq.page = 1; try { await loadMatches(); renderMatches(); } catch (e) { toast(e.message, true); } };
  const tools = el("div.admin-tools",
    select("admin-state", "Filter by state", { "": "Any state", ...STATES }, state.mq.state, (v) => { state.mq.state = v; reload(); }),
    select("admin-mode", "Filter by mode", { "": "Any mode", ...MODES }, state.mq.mode, (v) => { state.mq.mode = v; reload(); }));
  return el("section.stack",
    el("div.spread", el("h2.section-title", { id: "admin-matches-title" }, `Matches · ${num(state.matches.total)}`), tools),
    el("div.stack", { id: "admin-matches" }, matchesBody()));
}

function matchesBody() {
  const d = state.matches;
  const rules = (r) => (r === "lockout" ? "Lockout" : "ICPC");
  const filtered = state.mq.state || state.mq.mode;
  if (!d.matches.length) {
    const empty = emptyState({ iconName: "swords", title: filtered ? "No matches like that" : "No matches yet",
      text: filtered ? "Try another state or mode." : "Matches show up here as soon as someone opens a lobby." });
    empty.classList.add("boxed");
    return [empty];
  }
  return [el("div.table-wrap", el("table.admin-table",
      el("thead", el("tr", el("th", "Code"), el("th", "Mode"), el("th", "State"), el("th.num", "Players"), el("th", "Host"),
        el("th", "Created"), el("th", "Started"), el("th", "Duration"), el("th", { "aria-label": "Actions" }))),
      el("tbody", d.matches.map((m) => el("tr",
        el("td", el("a.mono", { href: `/m/${m.code}` }, m.code)),
        el("td", m.title ? el("span", m.title, el("span.muted", ` · ${rules(m.rules)}`)) : `${MODES[m.mode] || m.mode} · ${rules(m.rules)}`),
        el("td", stateChip(m)),
        el("td.num", String(m.players)),
        el("td", m.creator ? handleEl(m.creator, m.creator_rating) : el("span.muted", "—")),
        el("td", ago(m.created_at)),
        el("td", m.start_time ? ago(m.start_time) : m.scheduled_at && m.status === "lobby" ? ago(m.scheduled_at) : el("span.when", "—")),
        el("td", duration(m)),
        el("td.act", m.status === "lobby" || m.status === "running"
          ? el("button.btn.small.ghost.danger", { type: "button", onclick: () => act(`Cancel ${m.code} for everyone? ${m.status === "running" ? "It stops now and no result is recorded." : "The lobby closes."}`, `/api/admin/matches/${m.code}/cancel`, `${m.code} cancelled`) }, "Cancel")
          : null)))))),
    d.total > d.page_size ? pager(d, state.mq, async () => { await loadMatches(); renderMatches(); }) : null];
}

function activitySection() {
  const items = state.activity.items;
  const what = (i) => {
    const name = i.title || MODES[i.mode] || i.mode;
    const code = el("a.mono", { href: `/m/${i.code}` }, i.code);
    if (i.kind === "signup") return [handleEl(i.handle, i.cf_rating), " joined"];
    if (i.kind === "created") return [i.handle ? handleEl(i.handle, i.cf_rating) : "Someone", ` opened ${name} `, code];
    if (i.kind === "cancelled") return [`${name} `, code, " was cancelled"];
    return [`${name} `, code, " finished"];
  };
  return el("section.panel",
    el("div.panel-head", el("h2", "Recent activity"), el("span.small.muted", "Sign-ups and matches")),
    items.length
      ? el("ul.panel-list.activity-list", items.map((i) => el("li", ago(i.at), el("span.what", what(i)))))
      : emptyState({ iconName: "history", title: "Nothing yet", text: "Sign-ups and matches will show up here." }));
}

// ---------- page ----------

function render() {
  app.replaceChildren(el("div.page",
    el("div.page-head",
      el("div", el("h1", "Admin"), el("p.lede", "Who's here and what's happening. Only admins can see this page.")),
      el("button.btn", { type: "button", onclick: refresh }, "Refresh")),
    overview(),
    usersSection(),
    matchesSection(),
    activitySection()));
}

function renderUsers() {
  $("#admin-users").replaceChildren(...usersBody().filter(Boolean));
  $("#admin-users-title").textContent = `Users · ${num(state.users.total)}`;
}
function renderMatches() {
  $("#admin-matches").replaceChildren(...matchesBody().filter(Boolean));
  $("#admin-matches-title").textContent = `Matches · ${num(state.matches.total)}`;
}

await refresh();
