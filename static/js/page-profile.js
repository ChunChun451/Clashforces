import { $, api, el, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { lineChart } from "./chart.js";

mountTopbar("");
const app = $("#app");
const handle = decodeURIComponent(location.pathname.split("/").pop());
const me = await getMe();

let user;
try { user = await api(`/api/users/${encodeURIComponent(handle)}`); }
catch (e) { app.replaceChildren(el("div.empty-state.boxed", el("strong", "Player not found"), el("p", e.message), el("a.btn.small", { href: "/" }, "Back to Play"))); throw e; }
document.title = `${user.handle} · Clashforces`;
const [stats, hist, recent] = await Promise.all([
  api(`/api/users/${encodeURIComponent(handle)}/stats`),
  api(`/api/users/${encodeURIComponent(handle)}/rating-history`),
  api(`/api/users/${encodeURIComponent(handle)}/matches`),
]);
const h2h = me.user && !user.is_me ? await api(`/api/h2h/${encodeURIComponent(handle)}`).catch(() => null) : null;

const pct = (x) => `${Math.round(x * 100)}%`;
const wl = (r) => `${r.win} W – ${r.loss} L${r.draw ? ` – ${r.draw} D` : ""}`;
const modeName = { friend: "Friend duels", group: "Group rooms", ranked: "Ranked", team: "Team duels", contest: "Contests" };
const modeOne = { friend: "Friend duel", group: "Group room", ranked: "Ranked", team: "Team duel", contest: "Contest" };
const people = (list) => list.map((o, i) => [i ? ", " : "", handleEl(o.handle, o.cf_rating)]);
function resultCell(m) {
  if (m.result) {
    const text = { win: "Won", loss: "Lost", draw: "Draw" }[m.result];
    return el("td.num", { class: `num ${m.result === "win" ? "accent" : m.result === "loss" ? "danger" : ""}` }, text);
  }
  return el("td.num", { class: `num ${m.place === 1 ? "accent" : ""}` }, `${m.place}/${m.players}`);
}

function kpi(label, value) {
  return el("div", el("div.kpi", value), el("div.kpi-label", label));
}

async function toggleFriend() {
  try {
    if (user.is_friend) await api(`/api/friends/${encodeURIComponent(user.handle)}`, { method: "DELETE" });
    else await api("/api/friends", { method: "POST", body: { handle: user.handle } });
    user.is_friend = !user.is_friend;
    render();
  } catch (e) { toast(e.message, true); }
}

function render() {
  const points = [];
  if (hist.points.length) points.push({ x: hist.points[0].created_at - 1, y: hist.points[0].before, label: "start" });
  for (const p of hist.points) points.push({ x: p.created_at, y: p.after, label: p.code });
  const r = stats.record.overall;
  const byMode = Object.entries(stats.record.by_mode);
  const tableOrEmpty = (rows, table, empty) => rows.length ? el("div.table-wrap", table) : el("div.empty-state.boxed", el("p", empty));
  app.replaceChildren(el("div.page",
    el("div.stack",
      el("div.page-head",
        el("div",
          el("h1.profile-name", el("span", { class: "online-dot" + (user.online ? " on" : ""), title: user.online ? "Online" : "Offline" }),
            handleEl(user.handle, user.cf_rating, { link: false })),
          el("p.lede.small", el("a", { href: `https://codeforces.com/profile/${encodeURIComponent(user.handle)}`, target: "_blank", rel: "noopener" }, "Codeforces profile"),
            `  ·  CF rating ${user.cf_rating || "unrated"}`, user.online ? "  ·  online now" : "")),
        me.user && !user.is_me ? el("button.btn" + (user.is_friend ? "" : ".primary"), { type: "button", onclick: toggleFriend }, user.is_friend ? "Remove friend" : "Add friend") : null),
      el("div.card.kpis",
        kpi("Duel rating", String(user.duel_rating)),
        kpi("Record", wl(r)),
        kpi("Matches", String(stats.matches_played)),
        byMode.map(([m, rec]) => kpi(modeName[m] || m, wl(rec)))),
      h2h ? el("p.note", `You vs ${user.handle}: ${wl(h2h)}`) : null),
    el("section.stack", el("h2.section-title", "Duel rating"),
      points.length > 1 ? el("div.card", lineChart(points, { ariaLabel: `Duel rating of ${user.handle} over time` }))
        : el("div.empty-state.boxed", el("p", "Ranked matches move the duel rating. The graph appears after the first one."),
          user.is_me ? el("a.btn.small", { href: "/queue" }, "Find a ranked match") : null)),
    el("div.grid-2.equal-cols",
      el("section.stack", el("h2.section-title", "Average solve time"),
        tableOrEmpty(stats.solve_times, el("table",
          el("thead", el("tr", el("th.num", "Rating"), el("th", "Tag"), el("th.num", "Avg"), el("th.num", "Solves"))),
          el("tbody", stats.solve_times.map((s) => el("tr", el("td.num", String(s.rating)), el("td", s.tag),
            el("td.num", `${s.avg_minutes} min`), el("td.num", String(s.count)))))), "Solve a problem in a match to see solve times by rating and tag.")),
      el("section.stack", el("h2.section-title", "Win rate by tag"),
        tableOrEmpty(stats.tag_win_rates, el("table",
          el("thead", el("tr", el("th", "Tag"), el("th.num", "Matches"), el("th.num", "Win rate"))),
          el("tbody", stats.tag_win_rates.map((t) => el("tr", el("td", t.tag), el("td.num", String(t.matches)), el("td.num", pct(t.win_rate)))))),
        "Win rates by problem tag appear after a few finished matches."))),
    el("section.stack", el("h2.section-title", "Recent matches"),
      recent.matches.length
        ? el("div.table-wrap", el("table",
          el("thead", el("tr", el("th", "Match"), el("th", "Mode"), el("th", "Opponents"), el("th.num", "Result"))),
          el("tbody", recent.matches.map((m) => el("tr",
            el("td", el("a.mono", { href: `/m/${m.code}` }, m.code)),
            el("td", m.title ? el("span", m.title, el("span.muted", ` · ${m.rules === "icpc" ? "ICPC" : "Lockout"}`))
              : `${modeOne[m.mode] || m.mode} · ${m.rules === "icpc" ? "ICPC" : "Lockout"}`),
            el("td", m.team_name && m.players > 2 ? el("span.muted", `${m.team_name} · `) : null, people(m.opponents.slice(0, 6)),
              m.opponents.length > 6 ? el("span.muted", ` +${m.opponents.length - 6}`) : null,
              m.teammates && m.teammates.length ? el("span.muted", "  with ", people(m.teammates)) : null),
            resultCell(m))))))
        : el("div.empty-state.boxed", el("p", user.is_me ? "No finished matches yet." : `${user.handle} hasn't finished a match yet.`),
          user.is_me ? el("a.btn.small.primary", { href: "/new" }, "Start a friend duel") : null))));
}

render();
