import { $, api, el, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { lineChart } from "./chart.js";

mountTopbar("");
const app = $("#app");
const handle = decodeURIComponent(location.pathname.split("/").pop());
const me = await getMe();

let user;
try { user = await api(`/api/users/${encodeURIComponent(handle)}`); }
catch (e) { app.replaceChildren(el("p.empty", e.message)); throw e; }
document.title = `${user.handle} · Clashforces`;
const [stats, hist, recent] = await Promise.all([
  api(`/api/users/${encodeURIComponent(handle)}/stats`),
  api(`/api/users/${encodeURIComponent(handle)}/rating-history`),
  api(`/api/users/${encodeURIComponent(handle)}/matches`),
]);
const h2h = me.user && !user.is_me ? await api(`/api/h2h/${encodeURIComponent(handle)}`).catch(() => null) : null;

const pct = (x) => `${Math.round(x * 100)}%`;
const wl = (r) => `${r.win} W – ${r.loss} L${r.draw ? ` – ${r.draw} D` : ""}`;
const modeName = { friend: "Friend duels", group: "Group rooms", ranked: "Ranked" };

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
  app.replaceChildren(el("div.stack",
    el("div.spread",
      el("div",
        el("h1", el("span", { class: "online-dot" + (user.online ? " on" : ""), title: user.online ? "Online" : "Offline" }), handleEl(user.handle, user.cf_rating, { link: false })),
        el("p.muted.small", el("a", { href: `https://codeforces.com/profile/${encodeURIComponent(user.handle)}`, target: "_blank", rel: "noopener" }, "Codeforces profile"),
          `  ·  CF rating ${user.cf_rating || "unrated"}`)),
      me.user && !user.is_me ? el("button.btn.small", { type: "button", onclick: toggleFriend }, user.is_friend ? "Remove friend" : "Add friend") : null),
    el("div.row", { style: "gap: var(--s7)" },
      kpi("Duel rating", String(user.duel_rating)),
      kpi("Record", wl(r)),
      kpi("Matches", String(stats.matches_played))),
    h2h ? el("p.note", `You vs ${user.handle}: ${wl(h2h)}`) : null,
    el("section.stack", el("div.section-title", "Duel rating"),
      points.length > 1 ? lineChart(points, { ariaLabel: `Duel rating of ${user.handle} over time` })
        : el("p.muted.small", "Play ranked matches to see the rating graph.")),
    el("section.stack", el("div.section-title", "By mode"),
      el("div.row", { style: "gap: var(--s6)" }, Object.entries(stats.record.by_mode).map(([m, rec]) => kpi(modeName[m] || m, wl(rec)))),
      Object.keys(stats.record.by_mode).length ? null : el("p.muted.small", "No finished matches yet.")),
    el("div.grid-2",
      el("section.stack", el("div.section-title", "Average solve time"),
        stats.solve_times.length
          ? el("div.table-wrap", el("table",
            el("thead", el("tr", el("th.num", "Rating"), el("th", "Tag"), el("th.num", "Avg"), el("th.num", "Solves"))),
            el("tbody", stats.solve_times.map((s) => el("tr", el("td.num", String(s.rating)), el("td", s.tag),
              el("td.num", `${s.avg_minutes} min`), el("td.num", String(s.count)))))))
          : el("p.muted.small", "Solve something in a match first.")),
      el("section.stack", el("div.section-title", "Win rate by tag"),
        stats.tag_win_rates.length
          ? el("div.table-wrap", el("table",
            el("thead", el("tr", el("th", "Tag"), el("th.num", "Matches"), el("th.num", "Win rate"))),
            el("tbody", stats.tag_win_rates.map((t) => el("tr", el("td", t.tag), el("td.num", String(t.matches)), el("td.num", pct(t.win_rate)))))))
          : el("p.muted.small", "No finished matches yet."))),
    el("section.stack", el("div.section-title", "Recent matches"),
      recent.matches.length
        ? el("div.table-wrap", el("table",
          el("thead", el("tr", el("th", "Match"), el("th", "Mode"), el("th", "Opponents"), el("th.num", "Place"))),
          el("tbody", recent.matches.map((m) => el("tr",
            el("td", el("a.mono", { href: `/m/${m.code}` }, m.code)),
            el("td", `${modeName[m.mode] || m.mode} · ${m.rules === "icpc" ? "ICPC" : "Lockout"}`),
            el("td", m.opponents.map((o, i) => [i ? ", " : "", handleEl(o.handle, o.cf_rating)])),
            el("td.num", { class: `num ${m.place === 1 ? "accent" : ""}` }, `${m.place}/${m.players}`))))))
        : el("p.muted.small", "No matches yet."))));
}

render();
