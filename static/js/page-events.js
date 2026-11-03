// The Events tab: hosted contests that are live, coming up, or finished, and the ones you're part of.
import { $, api, el, emptyState, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { icon } from "./icons.js";
import { fmtCountdown, fmtLocal, fmtMinutes } from "./time.js";

mountTopbar("/events");
const app = $("#app");
const me = await getMe();
let data = null;
let offset = 0; // server time - local time (s)
const now = () => Date.now() / 1000 + offset;

async function load() {
  try {
    data = await api("/api/events");
    offset = data.server_time - Date.now() / 1000;
    render();
  } catch (e) {
    app.replaceChildren(el("div.error-box", e.message));
  }
}

function formatLine(e) {
  let shape = "Solo";
  if (e.format === "teams") {
    shape = e.team_count <= 4 ? Array(e.team_count).fill(e.team_size).join("v") : `${e.team_count} teams × ${e.team_size}`;
  }
  const parts = [shape, e.rules === "icpc" ? "ICPC" : "Lockout", `${e.problem_count} ${e.problem_count === 1 ? "problem" : "problems"}`];
  if (e.duration_min) parts.push(fmtMinutes(e.duration_min));
  return parts.join(" · ");
}

// Columns sized for the card count, so the last row never holds one lonely card.
function cols(n) {
  if (n <= 3) return Math.max(1, n);
  return [3, 2, 4].find((c) => n % c !== 1) || 3;
}

async function join(e) {
  try {
    await api(`/api/matches/${e.code}/join`, { method: "POST" });
    location.href = `/m/${e.code}`;
  } catch (ex) { toast(ex.message, true); }
}

function cta(e) {
  const open = (label, primary = false) => el(`a.btn.small${primary ? ".primary" : ""}`, { href: `/m/${e.code}` }, label);
  if (e.status === "running") return e.role ? open("Open", true) : open("Watch");
  if (e.role) return open(e.role === "host" ? "Manage" : "Open", true);
  if (!me.user) return el("a.btn.small.primary", { href: `/login?next=${encodeURIComponent(`/m/${e.code}`)}` }, "Log in to join");
  if (e.slots && e.players >= e.slots) return open("Full · watch");
  return el("button.btn.small.primary", { type: "button", onclick: () => join(e) }, "Join");
}

function eventCard(e) {
  const live = e.status === "running";
  const when = live
    ? el("span.event-when", el("span.pulse"), "Live")
    : el("span.event-when", icon("calendar", 14), e.scheduled_at ? fmtLocal(e.scheduled_at) : "Start set by host");
  const clock = live
    ? el("span.mono.event-clock", { dataset: { until: String(e.end_time || 0) } }, e.end_time ? `ends in ${fmtCountdown(e.end_time - now())}` : "")
    : e.scheduled_at ? el("span.mono.event-clock", { dataset: { until: String(e.scheduled_at), prefix: "in " } }, `in ${fmtCountdown(e.scheduled_at - now())}`)
      : el("span.mono.event-clock", "");
  return el("article.card.event-card",
    el("div.event-top", when, e.role ? el("span.chip.on", e.role === "host" ? "Hosting" : "Joined") : el("span.mode-tag", e.visibility === "unlisted" ? "unlisted" : "")),
    el("h2", el("a", { href: `/m/${e.code}` }, e.title)),
    el("p.event-format.mono", formatLine(e)),
    el("dl.event-facts",
      el("div", el("dt", "Host"), el("dd", e.host && e.host.handle ? handleEl(e.host.handle, e.host.cf_rating) : "—")),
      el("div", el("dt", "Players"), el("dd.mono", `${e.players}/${e.slots ?? "—"}`))),
    el("div.go", clock, cta(e)));
}

function cardGrid(list) {
  const n = list.length;
  return el("div.event-grid", { class: `event-grid${n % 2 ? " odd" : ""}`, style: `--cols: ${cols(n)}` }, list.map(eventCard));
}

function winnerText(e) {
  const w = e.winner;
  if (!w) return e.result_status === "final" ? el("span.muted", "—") : el("span.muted", "Result pending");
  if (w.kind === "team") return el("span", w.names.join(", "));
  return el("span", w.players.map((p, i) => [i ? ", " : "", handleEl(p.handle, p.cf_rating)]));
}

function pastPanel(list) {
  return el("section.panel",
    el("div.panel-head", el("h2", "Past")),
    list.length
      ? el("div.panel-fill", el("ul.panel-list.event-list", list.map((e) => el("li",
        el("div.event-row-main",
          el("a.event-row-title", { href: `/m/${e.code}` }, e.title),
          el("span.meta", e.ended_at ? fmtLocal(e.ended_at, { weekday: false }) : "", " · ", formatLine(e))),
        el("div.event-row-side", el("span.small.event-winner", el("span.muted", "Winner "), winnerText(e)),
          el("a.event-link", { href: `/m/${e.code}` }, "Results"))))))
      : el("div.panel-fill", emptyState({ iconName: "trophy", title: "No finished contests yet", text: "Results of public contests show up here, with the winner." })));
}

function minePanel(list) {
  let body;
  if (!me.user) {
    body = emptyState({ iconName: "users", title: "Your contests", text: "Log in to see the contests you host or joined.",
      action: el("a.btn.small", { href: "/login?next=/events" }, "Log in") });
  } else if (!list.length) {
    body = emptyState({ iconName: "calendar", title: "Nothing here yet", text: "Contests you host or join show up here, including unlisted ones.",
      action: el("a.btn.small", { href: "/events/new" }, "Host a contest") });
  }
  const status = (e) => e.status === "running" ? el("span.chip.on", "Live")
    : e.status === "ended" ? el("span.chip", "Ended")
      : el("span.chip", e.scheduled_at ? fmtLocal(e.scheduled_at, { weekday: false }) : "Not scheduled");
  return el("section.panel",
    el("div.panel-head", el("h2", "Mine")),
    body ? el("div.panel-fill", body)
      : el("div.panel-fill", el("ul.panel-list.event-list", list.map((e) => el("li",
        el("div.event-row-main",
          el("a.event-row-title", { href: `/m/${e.code}` }, e.title),
          el("span.meta", e.role === "host" ? "You host" : "You play", " · ", formatLine(e), e.visibility === "unlisted" ? " · unlisted" : "")),
        el("div.event-row-side", status(e), el("a.event-link", { href: `/m/${e.code}` }, "Open")))))));
}

function render() {
  const host = me.user ? el("a.btn.primary", { href: "/events/new" }, "Host a contest")
    : el("a.btn.primary", { href: "/login?next=/events/new" }, "Host a contest");
  app.replaceChildren(el("div.page",
    el("div.page-head",
      el("div", el("h1", "Events"),
        el("p.lede", "Contests run by a host for a club or a group of friends. Solo or in teams, on real Codeforces problems.")),
      host),
    el("section.stack", el("h2.section-title", "Live now"),
      data.live.length ? cardGrid(data.live)
        : el("div.empty-state.boxed.compact", el("div.empty-icon", icon("radio")), el("strong", "Nothing live right now"),
          el("p", "Running public contests appear here. You can watch any of them."))),
    el("section.stack", el("h2.section-title", "Upcoming"),
      data.upcoming.length ? cardGrid(data.upcoming)
        : emptyState({ iconName: "calendar", title: "No contests scheduled", text: "Host one for your club or friends. Public contests are listed here for anyone to join.",
          action: me.user ? el("a.btn.small.primary", { href: "/events/new" }, "Host a contest") : null })),
    el("div.grid-2.equal.events-pair", pastPanel(data.past), minePanel(data.mine))));
  const upcomingEmpty = app.querySelector("section:nth-of-type(2) > .empty-state");
  if (upcomingEmpty) upcomingEmpty.classList.add("boxed");
}

function tick() {
  for (const node of app.querySelectorAll(".event-clock[data-until]")) {
    const until = Number(node.dataset.until);
    if (!until) continue;
    const left = until - now();
    if (left <= 0) { load(); return; }
    node.textContent = `${node.dataset.prefix || "ends in "}${fmtCountdown(left)}`;
  }
}

await load();
setInterval(tick, 1000);
setInterval(load, 30000);
