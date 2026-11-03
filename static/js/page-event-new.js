// Host a contest (or edit one before it starts: /events/new?edit=CODE).
import { $, api, el, getMe, mountTopbar } from "./api.js";
import { teamBadge } from "./standings.js";
import { fromLocalInput, toLocalInput, tzLabel } from "./time.js";

mountTopbar("/events");
const app = $("#app");
const params = new URLSearchParams(location.search);
const editCode = params.get("edit");
const me = await getMe();
if (!me.user) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;

function defaultStart() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(19, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

const state = {
  title: "", description: "",
  format: "teams", team_count: 2, team_size: 3, fill: "free", uneven_ok: false, team_names: [],
  max_players: 30,
  rules: "icpc", duration_min: 180, freeze_min: 60,
  scheduled: true, scheduled_at: toLocalInput(defaultStart()), visibility: "public",
  problems: "", checked: [], points: {},
};

if (editCode) {
  try {
    const c = await api(`/api/contests/${editCode}/edit`);
    Object.assign(state, {
      title: c.title, description: c.description || "", format: c.format, rules: c.rules,
      team_count: c.team_count || 2, team_size: c.team_size || 3, fill: c.fill || "free", uneven_ok: !!c.uneven_ok,
      team_names: c.team_names || [], max_players: c.max_players || 30,
      duration_min: c.duration_min, freeze_min: c.freeze_min || 0,
      scheduled: !!c.scheduled_at, scheduled_at: toLocalInput(c.scheduled_at || defaultStart()), visibility: c.visibility,
      problems: c.problems,
    });
    c.problems.split(" ").forEach((k, i) => { state.points[k] = c.points[i]; });
  } catch (e) {
    app.replaceChildren(el("div.error-box", e.message));
    throw e;
  }
}

const err = el("div.error-box", { hidden: true, role: "alert" });
const checkResult = el("div.small.stack", { role: "status", "aria-live": "polite" });

function segmented(options, value, onPick, label) {
  return el("div.segmented", { role: "group", "aria-label": label },
    options.map(([v, text]) => el("button", {
      type: "button", "aria-pressed": String(v === value),
      onclick: () => { onPick(v); render(); },
    }, text)));
}

function numberInput(id, value, min, max, onChange) {
  return el("input", { type: "number", id, value, min, max, inputmode: "numeric", oninput: (e) => onChange(Number(e.target.value)) });
}

async function checkProblems() {
  try {
    const r = await api("/api/problems/validate", { method: "POST", body: { text: state.problems, match_code: editCode || null } });
    state.checked = r.problems;
    checkResult.replaceChildren(
      ...r.errors.map((e) => el("div.danger", e)),
      ...(editCode ? r.warnings.map((w) => el("div.warn", `Warning: ${w}`)) : []));
    if (r.problems.length > 15) checkResult.append(el("div.danger", "At most 15 problems."));
  } catch (e) {
    checkResult.replaceChildren(el("div.danger", e.message));
  }
  render();
}

function move(i, d) {
  const list = state.checked;
  const j = i + d;
  if (j < 0 || j >= list.length) return;
  [list[i], list[j]] = [list[j], list[i]];
  state.problems = list.map((p) => p.key).join(" ");
  render();
}

function remove(i) {
  state.checked.splice(i, 1);
  state.problems = state.checked.map((p) => p.key).join(" ");
  render();
}

function problemRows() {
  if (!state.checked.length) return null;
  const lockout = state.rules === "lockout";
  return el("div.problem-rows", state.checked.map((p, i) => {
    const def = (i + 1) * 100;
    return el("div.problem-row",
      el("span.letter", String.fromCharCode(65 + i)),
      el("span.what", el("span.n", p.name), el("span.m", `${p.key} · ${p.rating ?? "unrated"}`)),
      lockout ? el("span.pts-cell", el("input.pts.mono", { type: "number", min: 1, max: 10000, value: state.points[p.key] ?? def,
        "aria-label": `Points for ${p.key}`, oninput: (e) => { state.points[p.key] = e.target.value === "" ? null : Number(e.target.value); } })) : el("span"),
      el("span.moves",
        el("button.btn.ghost.small", { type: "button", "aria-label": `Move ${p.key} up`, title: "Move up", disabled: i === 0 || null, onclick: () => move(i, -1) }, "↑"),
        el("button.btn.ghost.small", { type: "button", "aria-label": `Move ${p.key} down`, title: "Move down", disabled: i === state.checked.length - 1 || null, onclick: () => move(i, 1) }, "↓"),
        el("button.btn.ghost.small.danger", { type: "button", "aria-label": `Remove ${p.key}`, title: "Remove", onclick: () => remove(i) }, "×")));
  }));
}

function payload() {
  const keys = state.checked.length ? state.checked.map((p) => p.key) : null;
  return {
    title: state.title, description: state.description, format: state.format, rules: state.rules,
    team_count: state.team_count, team_size: state.team_size, fill: state.fill, uneven_ok: state.uneven_ok,
    team_names: state.team_names.slice(0, state.team_count),
    max_players: state.max_players,
    duration_min: state.duration_min, freeze_min: state.rules === "icpc" ? state.freeze_min : 0,
    visibility: state.visibility,
    scheduled_at: state.scheduled ? fromLocalInput(state.scheduled_at) : null,
    problems: keys ? keys.join(" ") : state.problems,
    points: keys && state.rules === "lockout" ? keys.map((k, i) => state.points[k] ?? (i + 1) * 100) : null,
  };
}

async function submit(e) {
  e.preventDefault();
  err.hidden = true;
  try {
    if (editCode) {
      await api(`/api/contests/${editCode}`, { method: "PATCH", body: payload() });
      location.href = `/m/${editCode}`;
    } else {
      const r = await api("/api/contests", { method: "POST", body: payload() });
      location.href = `/m/${r.code}`;
    }
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    err.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

const panel = (title, aside, ...body) => el("section.panel",
  el("div.panel-head", el("h2", title), aside || null),
  el("div.panel-body.stack", body));

function teamFields() {
  const n = state.team_count;
  const nameCols = n === 4 ? 2 : Math.min(3, n);
  return [
    el("div.grid-2",
      el("div.field", el("label", "Teams"), segmented([2, 3, 4, 5, 6].map((v) => [v, String(v)]), state.team_count, (v) => state.team_count = v, "Number of teams")),
      el("div.field", el("label", "Players per team"), segmented([2, 3, 4, 5, 6].map((v) => [v, String(v)]), state.team_size, (v) => state.team_size = v, "Players per team"))),
    el("div.grid-2",
      el("div.field", el("label", "How teams fill"),
        segmented([["free", "Players pick"], ["draft", "Captains' draft"]], state.fill, (v) => state.fill = v, "How teams fill")),
      el("label.check", el("input", { type: "checkbox", checked: state.uneven_ok, onchange: (e) => { state.uneven_ok = e.target.checked; } }),
        "Allow uneven teams at the start")),
    el("p.small.muted", state.fill === "draft"
      ? "You name a captain per team in the lobby; captains take turns picking players (45 s per pick, then the strongest player left is picked for them)."
      : "Players join the smallest team, pick a side, or use a team's own invite link. You can move anyone, or balance by rating."),
    el("div", el("label", "Team names (optional)"),
      el("div.team-names", { style: `--cols: ${nameCols}` },
        Array.from({ length: n }, (_, i) => el("div.field",
          el("label.sr-only", { for: `tn-${i}` }, `Team ${String.fromCharCode(65 + i)} name`),
          el("div.row.tight", teamBadge(i), el("input", { type: "text", id: `tn-${i}`, maxlength: 24, placeholder: `Team ${String.fromCharCode(65 + i)}`,
            value: state.team_names[i] || "", oninput: (e) => { state.team_names[i] = e.target.value; } })))))),
  ];
}

function render() {
  const slots = state.format === "teams" ? state.team_count * state.team_size : state.max_players;
  const format = panel("Format", segmented([["teams", "Teams"], ["solo", "Solo"]], state.format, (v) => state.format = v, "Format"),
    el("div.grid-2",
      el("div.field", el("label", { for: "title" }, "Title"),
        el("input", { type: "text", id: "title", maxlength: 60, value: state.title, placeholder: "Friday Night Clash", oninput: (e) => { state.title = e.target.value; } })),
      el("div.field", el("label", { for: "desc" }, "Short description (optional)"),
        el("input", { type: "text", id: "desc", maxlength: 280, value: state.description, placeholder: "Room 204, pizza after", oninput: (e) => { state.description = e.target.value; } }))),
    state.format === "teams" ? teamFields()
      : el("div.grid-2",
        el("div.field", el("label", { for: "maxp" }, "Max players (2–40)"), numberInput("maxp", state.max_players, 2, 40, (v) => { state.max_players = v; })),
        el("p.small.muted.align-end", "Everyone plays for themselves on one scoreboard. You host, you don't play.")),
    el("p.small.muted", `${slots} ${slots === 1 ? "slot" : "slots"}. You host the contest and don't appear on the scoreboard.`));

  const problems = panel("Problems", el("button.btn.small", { type: "button", onclick: checkProblems }, "Check list"),
    el("div.field", el("label", { for: "probs" }, "Codeforces problem IDs or links, in order (up to 15)"),
      el("textarea", { id: "probs", placeholder: "1850C, 1850D\nhttps://codeforces.com/contest/2043/problem/E",
        oninput: (e) => { state.problems = e.target.value; state.checked = []; } }, state.problems)),
    problemRows(),
    checkResult,
    el("p.small.muted", state.rules === "lockout"
      ? "Labels follow the order (A, B, C…). Points default to 100, 200, 300…; change them per problem after checking the list."
      : "Labels follow the order (A, B, C…). Only you can see the problems until the start."));

  const schedule = panel("Schedule", null,
    segmented([[true, "At a set time"], [false, "When I start it"]], state.scheduled, (v) => state.scheduled = v, "Start"),
    state.scheduled
      ? el("div.field", el("label", { for: "when" }, `Start (your time, ${tzLabel()})`),
        el("input", { type: "datetime-local", id: "when", value: state.scheduled_at, oninput: (e) => { state.scheduled_at = e.target.value; } }))
      : el("p.small.muted", "Press Start now in the lobby when everyone is there."),
    el("div.field", el("label", "Visibility"),
      segmented([["public", "Public"], ["unlisted", "Unlisted"]], state.visibility, (v) => state.visibility = v, "Visibility")),
    el("p.small.muted", state.visibility === "public"
      ? "Listed under Events for anyone to find. Players see the start in their own time zone."
      : "Only people with the link can find it. It still shows under Mine for you and your players."));

  const rules = panel("Rules", null,
    segmented([["icpc", "ICPC"], ["lockout", "Lockout"]], state.rules, (v) => state.rules = v, "Rules"),
    el("p.small.muted", state.rules === "icpc"
      ? "Most solved wins. Ties go to lower penalty: minutes to each accept + 20 per wrong try."
      : "The first team (or player) to solve a problem takes its points. Ends early when the leader can't be caught."),
    el("div.grid-2",
      el("div.field", el("label", { for: "dur" }, "Duration (minutes)"), numberInput("dur", state.duration_min, 15, 300, (v) => { state.duration_min = v; })),
      state.rules === "icpc"
        ? el("div.field", el("label", { for: "frz" }, "Freeze last N minutes"), numberInput("frz", state.freeze_min, 0, 299, (v) => { state.freeze_min = v; }))
        : el("div")));

  app.replaceChildren(el("form.page", { onsubmit: submit, novalidate: true },
    el("div.page-head",
      el("div",
        el("h1", editCode ? "Edit contest" : "Host a contest"),
        el("p.lede", editCode ? "Players stay in the lobby. Problems stay hidden until the start."
          : "You run it, you don't play. Problems stay hidden until the start; the contest starts on its own at the set time."))),
    el("div.stack",
      format,
      problems,
      el("div.grid-2.equal.form-pair", schedule, rules),
      err,
      el("div.actions",
        el("button.btn.primary.big", { type: "submit" }, editCode ? "Save contest" : "Create contest"),
        el("a.btn.ghost.big", { href: editCode ? `/m/${editCode}` : "/events" }, "Cancel")))));
}

render();
if (editCode) checkProblems();
