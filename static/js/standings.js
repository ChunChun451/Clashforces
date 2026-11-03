// Renders the contest-style standings grid and animates newly accepted cells.
import { el, handleEl } from "./api.js";

function cellNode(c, rules) {
  const attrs = { class: "cell", dataset: { label: c.label } };
  switch (c.state) {
    case "ok": {
      const late = rules === "lockout" && !c.first;
      attrs.class = `cell ${late ? "ok-late" : "ok"}`;
      const top = rules === "lockout"
        ? (c.first ? `+${fmtPts(c.points)}` : "✓")
        : (c.tries ? `+${c.tries}` : "+");
      return el("div", { ...attrs, title: late ? "Solved, but someone was first" : "Accepted" },
        el("span", top), el("span.t", `${c.minute}'`));
    }
    case "wrong":
      attrs.class = "cell wrong";
      return el("div", { ...attrs, title: `${c.tries} rejected` }, `−${c.tries}`);
    case "testing":
      attrs.class = "cell testing";
      return el("div", { ...attrs, title: "Being judged" }, el("span.spinner"));
    case "frozen":
      attrs.class = "cell frozen";
      return el("div", { ...attrs, title: "Frozen" },
        el("span", c.tries ? `−${c.tries}` : ""), el("span.t", `? ${c.frozen_tries}`));
    default:
      return el("div", attrs);
  }
}

export function fmtPts(p) {
  return Number.isInteger(p) ? String(p) : p.toFixed(1);
}

// rows: standings rows from the server. labels: [{label, points}]
export function standingsTable({ rows, labels, rules, myId }) {
  const head = el("tr",
    el("th.col-place", "#"),
    el("th.col-who", "Player"),
    rules === "lockout" ? el("th.col-num", "Points") : [el("th.col-num", "Solved"), el("th.col-num", "Penalty")],
    labels.map((l) => el("th.col-cell", { scope: "col" }, l.label,
      rules === "lockout" && l.points ? el("div.pts-head", fmtPts(l.points)) : null)));

  const body = rows.map((r) => el("tr", {
    class: [r.user_id === myId ? "me" : "", r.forfeited ? "out" : ""].join(" ").trim() || null,
    dataset: { user: String(r.user_id) },
  },
  el("td.col-place", String(r.place)),
  el("td.col-who", handleEl(r.handle, r.cf_rating), r.forfeited ? el("span.muted.small", " · forfeit") : null),
  rules === "lockout"
    ? el("td.col-num", fmtPts(r.points))
    : [el("td.col-num", String(r.solved)), el("td.col-num", String(r.penalty))],
  labels.map((l) => {
    const c = r.cells.find((x) => x.label === l.label) || { label: l.label, state: "none" };
    return el("td.col-cell", cellNode(c, rules));
  })));

  return el("div.table-wrap.standings", { tabindex: "0", "aria-label": "Standings" },
    el("table", el("thead", head), el("tbody", body)));
}

// ---------- team duels ----------

export const TEAM_LETTER = ["A", "B"];

export function teamBadge(team, name) {
  return el("span", { class: `team-badge team-${TEAM_LETTER[team].toLowerCase()}`, title: name || `Team ${TEAM_LETTER[team]}` },
    TEAM_LETTER[team]);
}

function teamCell(c, rules) {
  const node = cellNode(c, rules);
  if (c.state === "ok" && c.by_handle) {
    node.classList.add("has-by");
    node.title = `${node.title || "Accepted"} by ${c.by_handle}`;
    node.append(el("span.by", c.by_handle));
  }
  return node;
}

function memberCell(c) {
  // A member's own result: small and quiet, so the team row stays the headline.
  const attrs = { class: `mcell ${c.state}`, dataset: { label: c.label } };
  switch (c.state) {
    case "ok": return el("span", { ...attrs, title: `Solved at ${c.minute}'` }, `✓ ${c.minute}'`);
    case "wrong": return el("span", { ...attrs, title: `${c.tries} rejected` }, `−${c.tries}`);
    case "testing": return el("span", { ...attrs, title: "Being judged" }, "…");
    case "frozen": return el("span", { ...attrs, title: "Frozen" }, `?${c.frozen_tries || ""}`);
    default: return el("span", attrs, "");
  }
}

// rows: team rows from the server, each with .members. Members are listed under their team.
export function teamStandingsTable({ rows, labels, rules, myTeam, myId }) {
  const head = el("tr",
    el("th.col-place", "#"),
    el("th.col-who", "Team"),
    rules === "lockout" ? el("th.col-num", "Points") : [el("th.col-num", "Solved"), el("th.col-num", "Penalty")],
    labels.map((l) => el("th.col-cell", { scope: "col" }, l.label,
      rules === "lockout" && l.points ? el("div.pts-head", fmtPts(l.points)) : null)));
  const numCols = rules === "lockout" ? 1 : 2;

  const body = rows.flatMap((r) => [
    el("tr", {
      class: ["team-row", `team-${TEAM_LETTER[r.team].toLowerCase()}`, r.team === myTeam ? "me" : "", r.forfeited ? "out" : ""].join(" ").trim(),
      dataset: { user: r.user_id },
    },
    el("td.col-place", String(r.place)),
    el("td.col-who", el("span.team-name", teamBadge(r.team, r.name), el("span", r.name)),
      r.team === myTeam ? el("span.chip.you", "you") : null,
      r.forfeited ? el("span.muted.small", " · forfeit") : null),
    rules === "lockout"
      ? el("td.col-num", fmtPts(r.points))
      : [el("td.col-num", String(r.solved)), el("td.col-num", String(r.penalty))],
    labels.map((l) => el("td.col-cell", teamCell(r.cells.find((x) => x.label === l.label) || { label: l.label, state: "none" }, rules)))),
    ...r.members.map((m) => el("tr", {
      class: ["member-row", `team-${TEAM_LETTER[r.team].toLowerCase()}`, m.user_id === myId ? "self" : "", m.forfeited ? "out" : ""].join(" ").trim(),
      dataset: { member: String(m.user_id) },
    },
    el("td.col-place"),
    el("td.col-who", handleEl(m.handle, m.cf_rating), m.forfeited ? el("span.muted.small", " · forfeit") : null),
    el("td.col-num.member-solved", { colspan: numCols }, m.solved ? `${m.solved} solved` : ""),
    labels.map((l) => el("td.col-cell", memberCell(m.cells.find((x) => x.label === l.label) || { label: l.label, state: "none" }))))),
  ]);

  return el("div.table-wrap.standings.team-standings", { tabindex: "0", "aria-label": "Team standings" },
    el("table", el("thead", head), el("tbody", body)));
}

// Remember which cells were accepted so the next render can flip the new ones.
export function okSet(rows) {
  const s = new Set();
  for (const r of rows || []) for (const c of r.cells) if (c.state === "ok") s.add(`${r.user_id}:${c.label}`);
  return s;
}

export function flipNew(container, before, rows) {
  if (!before) return;
  for (const r of rows) {
    for (const c of r.cells) {
      const key = `${r.user_id}:${c.label}`;
      if (c.state === "ok" && !before.has(key)) {
        const node = container.querySelector(`tr[data-user="${r.user_id}"] .cell[data-label="${c.label}"]`);
        if (node) {
          node.classList.add("flip");
          node.closest("tr").classList.add("row-flash");
        }
      }
    }
  }
}
