import { $, api, el, emptyState, fmtDuration, getMe, handleEl, mountTopbar, ratingClass } from "./api.js";
import { icon } from "./icons.js";

mountTopbar("/");
const app = $("#app");
const me = await getMe();

function joinForm({ big = false, id = "join-code" } = {}) {
  const input = el(`input.mono.code-input${big ? ".lg" : ""}`, { type: "text", id, placeholder: "ABC123", maxlength: 6,
    autocomplete: "off", spellcheck: "false", "aria-label": "Match code" });
  return el("form.inline-form.code", { onsubmit: (e) => {
    e.preventDefault();
    const c = input.value.trim().toUpperCase();
    if (c) location.href = `/m/${c}`;
  } }, input, el(`button.btn${big ? ".big" : ""}`, { type: "submit" }, "Join or watch"));
}

const MODES = [
  { key: "friend", icon: "swords", title: "Friend duel", text: "1 vs 1 with a link. ICPC or Lockout, any rating range, optional handicap. Unrated.", href: "/new", go: "Create a duel" },
  { key: "group", icon: "users", title: "Group room", text: "3–10 players and a contest-style standings table. Unrated.", href: "/new?mode=group", go: "Open a room" },
  { key: "ranked", icon: "trophy", title: "Ranked", text: "3 problems, 60 minutes, ICPC rules. Matched by duel rating, which moves with Elo.", href: "/queue", go: "Find a match" },
];

function modeCard(m, loggedIn) {
  const href = loggedIn ? m.href : `/login?next=${encodeURIComponent(m.href)}`;
  return el("a.card.mode-card", { href },
    el("div.mode-icon", icon(m.icon)),
    el("h2", m.title),
    el("p", m.text),
    el("div.go", m.go));
}

// ---------- logged out ----------

function duelMock() {
  // A static picture of a live duel. Nothing here talks to the API.
  const cell = (cls, top, t) => el("div", { class: `cell ${cls}` }, top !== null ? el("span", top) : null, t ? el("span.t", t) : null);
  const testing = el("div.cell.testing", el("span.spinner"));
  const timer = el("div.dm-timer", { role: "presentation" }, "41:27");
  const score = el("div.dm-score", el("b", "2"), " : ", el("b", "1"), " solved");
  const fig = el("figure.duel-mock",
    el("figcaption.sr-only", "Example: alice and Bob_77 in a live friend duel. alice has solved A and B, Bob_77 has solved A and has one wrong try on B."),
    el("div", { "aria-hidden": "true" },
      el("div.dm-head",
        el("span.dm-live", el("span.pulse"), "Live"),
        el("span.chip", "Friend duel"),
        el("span.chip", "ICPC"),
        el("span.dm-code", "K7QX2M")),
      el("div.dm-versus",
        el("div.dm-player", el("span", { class: `handle ${ratingClass(1712)}` }, "alice"), el("span.r", "1712")),
        el("div.dm-clock", timer, score),
        el("div.dm-player.right", el("span", { class: `handle ${ratingClass(1320)}` }, "Bob_77"), el("span.r", "1320"))),
      el("div.dm-board",
        el("span"), el("span.h.left", "Problem"), el("span.h", "alice"), el("span.h", "Bob_77"),
        el("span.dm-letter", "A"), el("span.dm-prob", el("span.n", "Two Arrays and Swaps"), el("span.m", "1400")),
        cell("ok", "+", "12'"), cell("ok", "+1", "19'"),
        el("span.dm-letter", "B"), el("span.dm-prob", el("span.n", "Grid Walk"), el("span.m", "1600")),
        cell("ok", "+", "31'"), cell("wrong", "−2", null),
        el("span.dm-letter", "C"), el("span.dm-prob", el("span.n", "Segment Magic"), el("span.m", "1900")),
        testing, cell("empty-cell", null, null)),
      el("div.dm-foot", el("span", "Submissions go to Codeforces"), el("span.mono", "verdicts in ~15s"))));

  // The clock runs, and C gets judged once. Only motion on the page that isn't a hover.
  let left = 41 * 60 + 27;
  setInterval(() => { left = Math.max(0, left - 1); timer.textContent = fmtDuration(left); }, 1000);
  setTimeout(() => {
    const done = cell("ok flip", "+", "44'");
    testing.replaceWith(done);
    score.replaceChildren(el("b", "3"), " : ", el("b", "1"), " solved");
  }, 3500);
  return fig;
}

function loggedOut() {
  const steps = [
    { title: "Verify your handle", text: "No password. Submit code that fails to compile on a problem we pick, and we know the handle is yours.",
      term: [el("span", "verdict  "), el("span.ce", "Compilation error"), el("span", "  → verified")] },
    { title: "Pick the rules", text: "ICPC or Lockout, a rating range, tags, duration. We only choose problems none of you has tried.",
      term: [el("span", "ICPC · 3 problems · 1400–1800 · 60 min")] },
    { title: "Submit on Codeforces", text: "Solve in the Codeforces tab like always. We read the official API and update the scoreboard live.",
      term: [el("span", "B  "), el("span.ok", "Accepted"), el("span", "  31'")] },
  ];
  app.replaceChildren(el("div.page",
    el("section.hero",
      el("div",
        el("h1.wordmark", "clash", el("span", "forces"), el("i.caret", { "aria-hidden": "true" })),
        el("p.tagline", "Duel your friends on real Codeforces problems."),
        el("p.lede", "Race the clock on problems nobody has seen, submit on Codeforces as usual, and watch every verdict land on a live scoreboard."),
        el("div.hero-actions",
          el("a.btn.primary.big", { href: "/login" }, "Log in with Codeforces"),
          el("div.hero-join",
            el("label.small", { for: "join-code" }, "Got a match code? Join or watch without an account."),
            joinForm({ big: true })))),
      duelMock()),
    el("section", { "aria-labelledby": "how" },
      el("div.section-head", el("h2", { id: "how" }, "How it works")),
      el("ol.steps", steps.map((s, i) => el("li.card.step",
        el("span.step-num", { "aria-hidden": "true" }, String(i + 1)),
        el("h3", s.title), el("p", s.text), el("div.term", s.term))))),
    el("section", { "aria-labelledby": "modes" },
      el("div.section-head", el("h2", { id: "modes" }, "Ways to play")),
      el("div.card-grid", MODES.map((m) => modeCard(m, false))))));
  mountFooter();
}

// ---------- logged in ----------

const MODE_NAME = { friend: "Friend duel", group: "Group room", ranked: "Ranked" };
const ordinal = (n) => `${n}${["th", "st", "nd", "rd"][(n % 100 > 10 && n % 100 < 14) ? 0 : n % 10 < 4 ? n % 10 : 0]}`;

function recentPanel(matches) {
  const body = matches.length
    ? el("ul.panel-list", matches.slice(0, 6).map((m) => el("li",
      el("div.match-row",
        el("span", { class: `place${m.place === 1 ? " first" : ""}`, title: `Place ${m.place} of ${m.players}` }, `${ordinal(m.place)}/${m.players}`),
        el("div.opp",
          m.opponents.length ? ["vs ", m.opponents.slice(0, 3).map((o, i) => [i ? ", " : "", handleEl(o.handle, o.cf_rating)]),
            m.opponents.length > 3 ? ` +${m.opponents.length - 3}` : ""] : "Solo",
          el("div.meta", `${MODE_NAME[m.mode] || m.mode}, ${m.rules === "icpc" ? "ICPC" : "Lockout"}`))),
      el("a.match-code", { href: `/m/${m.code}` }, m.code))))
    : emptyState({ iconName: "history", title: "No matches yet",
      text: "Your finished duels and rooms show up here, with places and opponents.",
      action: el("a.btn.primary.small", { href: "/new" }, "Start a friend duel") });
  return el("section.panel",
    el("div.panel-head", el("h2", "Recent matches"),
      matches.length ? el("a", { href: `/u/${encodeURIComponent(me.user.handle)}` }, "All matches") : null),
    body);
}

function sparkline(points) {
  const ns = "http://www.w3.org/2000/svg";
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const W = 100, Hh = 40;
  const xy = points.map((p, i) => [(i / (points.length - 1)) * W, Hh - 3 - ((p.y - lo) / span) * (Hh - 6)]);
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Duel rating over the last ${points.length - 1} rated matches, from ${points[0].y} to ${points[points.length - 1].y}`);
  const area = document.createElementNS(ns, "path");
  area.setAttribute("class", "area");
  area.setAttribute("d", `M0 ${Hh} L${xy.map(([x, y]) => `${x} ${y}`).join(" L")} L${W} ${Hh} Z`);
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("class", "line");
  line.setAttribute("points", xy.map(([x, y]) => `${x},${y}`).join(" "));
  svg.append(area, line);
  return svg;
}

function ratingPanel(hist) {
  const u = me.user;
  const pts = [];
  if (hist.length) pts.push({ y: hist[0].before });
  for (const p of hist.slice(-20)) pts.push({ y: p.after });
  const last = hist[hist.length - 1];
  const delta = last ? last.after - last.before : null;
  return el("section.panel.rating-panel",
    el("div.panel-head", el("h2", "Rating"), el("a", { href: "/queue" }, "Ranked queue")),
    el("div.panel-body",
      el("div.kpis",
        el("div", el("div.kpi", String(u.duel_rating)), el("div.kpi-label", "Duel rating",
          delta !== null ? el("span", { class: delta >= 0 ? "accent" : "danger" }, `  ${delta >= 0 ? "+" : ""}${delta} last`) : null)),
        el("div", el("div", { class: `kpi ${ratingClass(u.cf_rating)}` }, u.cf_rating ? String(u.cf_rating) : "—"), el("div.kpi-label", "Codeforces"))),
      pts.length > 1 ? sparkline(pts)
        : el("p.small.muted.spark-note", "Ranked matches move your duel rating. Play one to start the graph.")));
}

function friendsPanel(friends) {
  const online = friends.filter((f) => f.online);
  let body;
  if (!friends.length) {
    body = emptyState({ iconName: "userPlus", title: "No friends added",
      text: "Add friends by their Codeforces handle to see when they're online and challenge them.",
      action: el("a.btn.small", { href: "/friends" }, "Add friends") });
  } else if (!online.length) {
    body = emptyState({ iconName: "users", title: "Nobody online right now",
      text: `${friends.length} ${friends.length === 1 ? "friend" : "friends"} added. Send a duel link and they can join when they're back.`,
      action: el("a.btn.small", { href: "/new" }, "Create a duel link") });
  } else {
    body = el("ul.panel-list", online.slice(0, 6).map((f) => el("li",
      el("span", el("span.online-dot.on", { "aria-hidden": "true" }), handleEl(f.handle, f.cf_rating)),
      el("span.meta.mono", { title: "Your head-to-head record" }, `${f.h2h.win}–${f.h2h.loss}`))));
  }
  return el("section.panel",
    el("div.panel-head", el("h2", "Friends online", online.length ? el("span.muted", `  ${online.length}`) : null),
      el("a", { href: "/friends" }, "Friends")),
    body);
}

async function loggedIn() {
  const active = me.active;
  const handle = encodeURIComponent(me.user.handle || "");
  const [recent, hist, friends] = await Promise.all([
    api(`/api/users/${handle}/matches`).then((r) => r.matches).catch(() => []),
    api(`/api/users/${handle}/rating-history`).then((r) => r.points).catch(() => []),
    api("/api/friends").then((r) => r.friends).catch(() => []),
  ]);
  const modes = [...MODES, { key: "upsolve", icon: "retry", title: "Upsolve", text: "Problems you didn't solve in your matches. They tick off when you get Accepted.", href: "/upsolve", go: "Open the list" }];
  app.replaceChildren(el("div.page",
    el("div.stack",
      el("div.page-head",
        el("div", el("h1", "Play"), el("p.lede", "Pick a mode, or join a match with its code.")),
        joinForm()),
      active ? el("div.banner.good", { role: "status" },
        el("span.pulse", { "aria-hidden": "true" }),
        el("span.grow", active.kind === "queue" ? "You're in the ranked queue." : ["You're in match ", el("span.mono", active.code), "."]),
        el("a.btn.primary.small", { href: active.kind === "queue" ? "/queue" : `/m/${active.code}` }, active.kind === "queue" ? "Open queue" : "Back to match")) : null),
    el("div.card-grid", modes.map((m) => modeCard(m, true))),
    el("div.dash",
      recentPanel(recent),
      el("div.dash-side", ratingPanel(hist), friendsPanel(friends)))));
}

function mountFooter() {
  if ($(".site-footer")) return;
  document.body.append(el("footer.site-footer", el("div.inner",
    el("span", "Clashforces. Not affiliated with Codeforces; verdicts come from the official API."),
    el("span", el("a", { href: "https://codeforces.com", target: "_blank", rel: "noopener" }, "codeforces.com")))));
}

if (me.user) { await loggedIn(); mountFooter(); } else loggedOut();
