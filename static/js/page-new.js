// Create a friend duel, team duel or group room, or edit a lobby's settings (?edit=CODE).
import { $, api, el, getMe, mountTopbar } from "./api.js";

mountTopbar("/");
const app = $("#app");
const params = new URLSearchParams(location.search);
const editCode = params.get("edit");
const challengeHandle = editCode ? null : (params.get("challenge") || "").trim() || null;
const me = await getMe();
if (!me.user) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;

// "Problems from": older rounds skew easy for their rating, so newer-only is a common ask.
const YEARS = [];
for (let y = new Date().getFullYear(); y >= 2014; y -= 1) YEARS.push(y);

const RATINGS = [];
for (let r = 800; r <= 3500; r += 100) RATINGS.push(r);

const state = {
  mode: ["group", "team"].includes(params.get("mode")) ? params.get("mode") : "friend",
  team_size: 2, team_count: 2, fill: "free", uneven_ok: false,
  rules: "icpc",
  count: 3, rating_min: 1200, rating_max: 1600,
  tags_include: new Set(), tags_avoid: new Set(),
  avoid_interactive: true,
  since_year: null,
  use_custom: false, custom_problems: "",
  duration_min: 60, freeze_min: 0,
  handicap: null,
  sprint_limit_min: 10,
};

if (editCode) {
  const m = await api(`/api/matches/${editCode}`);
  const s = m.settings;
  Object.assign(state, {
    mode: m.mode, rules: m.rules, count: s.count, rating_min: s.rating_min, rating_max: s.rating_max,
    tags_include: new Set(s.tags_include), tags_avoid: new Set(s.tags_avoid),
    avoid_interactive: s.avoid_interactive, use_custom: !!s.custom_problems,
    custom_problems: s.custom_problems || "", duration_min: s.duration_min, freeze_min: s.freeze_min || 0,
    handicap: s.handicap, team_size: s.team_size || 2, team_count: s.team_count || 2, fill: s.fill || "free",
    uneven_ok: !!s.uneven_ok, since_year: s.since_year || null,
    sprint_limit_min: s.sprint ? s.sprint_limit_min || 0 : 10,
  });
  if (s.sprint) state.rules = "sprint";  // shown as its own rules choice; stored as Lockout + sprint
  if (s.freestyle) state.rules = "freestyle";  // stored as ICPC + freestyle
  // The "Avoid interactive problems" checkbox stands for the interactive tag's avoided state.
  if (state.tags_avoid.delete("interactive")) state.avoid_interactive = true;
}

const TITLES = { friend: "New friend duel", team: "New team duel", group: "New group room" };
const CREATE = { friend: "Create duel", team: "Create team duel", group: "Create room" };

let tags = [];
try { tags = (await api("/api/tags")).tags; } catch { /* cache still loading */ }

function segmented(options, value, onPick, label) {
  return el("div.segmented", { role: "group", "aria-label": label },
    options.map(([v, text]) => el("button", {
      type: "button", "aria-pressed": String(v === value),
      onclick: () => { onPick(v); render(); },
    }, text)));
}

function ratingSelect(id, value, onChange) {
  return el("select", { id, onchange: (e) => { onChange(Number(e.target.value)); refreshPreview(); } },
    RATINGS.map((r) => el("option", { value: r, selected: r === value }, String(r))));
}

function tagPicker() {
  // Click cycles: neutral -> include -> avoid -> neutral.
  // "interactive" is avoided through the checkbox below, so the chip and checkbox always agree.
  return el("div.chips", { role: "group", "aria-label": "Tags" }, tags.filter((t) => t !== "*special").map((t) => {
    const interactive = t === "interactive";
    const inc = state.tags_include.has(t);
    const avo = interactive ? state.avoid_interactive : state.tags_avoid.has(t);
    return el("button", {
      type: "button",
      class: `chip${inc ? " on" : ""}${avo ? " avoid" : ""}`,
      "aria-label": `${t}: ${inc ? "required" : avo ? "avoided" : "any"}`,
      onclick: () => {
        if (interactive) {
          if (inc) { state.tags_include.delete(t); state.avoid_interactive = true; }
          else if (avo) state.avoid_interactive = false;
          else state.tags_include.add(t);
        } else if (inc) { state.tags_include.delete(t); state.tags_avoid.add(t); }
        else if (avo) state.tags_avoid.delete(t);
        else state.tags_include.add(t);
        render();
        refreshPreview();
      },
    }, t);
  }));
}

const preview = el("p.small.muted", { role: "status", "aria-live": "polite" });
let previewTimer = null;
function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    if (state.use_custom) { preview.textContent = ""; return; }
    try {
      const r = await api("/api/problems/preview", { method: "POST", body: payload().settings });
      preview.className = r.ok ? "small muted" : "small danger";
      preview.textContent = r.ok ? `${r.available} unsolved problems match.` : r.message;
    } catch { preview.textContent = ""; }
  }, 250);
}

const customResult = el("div.small", { role: "status", "aria-live": "polite" });
async function validateCustom() {
  try {
    const r = await api("/api/problems/validate", { method: "POST", body: { text: state.custom_problems, match_code: editCode } });
    customResult.replaceChildren(
      ...r.problems.map((p) => el("div.mono", `${p.key} · ${p.name} · ${p.rating ?? "unrated"}`)),
      ...r.errors.map((e) => el("div.danger", e)),
      ...r.warnings.map((w) => el("div.warn", `Warning: ${w}`)));
  } catch (e) { customResult.replaceChildren(el("div.danger", e.message)); }
}

// Sprint is Lockout with one problem open at a time. Freestyle is ICPC with problems set by the players.
const realRules = () => (state.rules === "sprint" ? "lockout" : state.rules === "freestyle" ? "icpc" : state.rules);
const isFreestyle = () => state.rules === "freestyle" && state.mode !== "team";
const SPRINT_LIMITS = [[0, "No limit"], [5, "5 min"], [10, "10 min"], [15, "15 min"], [20, "20 min"], [30, "30 min"]];
const RULE_TEXT = {
  icpc: "Most solved wins. Ties go to lower penalty: minutes to each accept + 20 per wrong try.",
  lockout: "First to solve a problem takes its points. Harder problems are worth more. Ends early when the leader can't be caught.",
  sprint: "Lockout, one problem at a time. Only the current problem is shown; the first to solve it takes its points and the next one opens for everyone.",
  freestyle: "Everyone gets the random problem, plus one problem set by each opponent. In the lobby, each player picks a problem for the others; it stays secret until the start. Scored like ICPC.",
};

function payload() {
  return {
    mode: state.mode,
    rules: realRules(),
    settings: {
      rules: realRules(),
      ...(state.rules === "sprint" ? { sprint: true, sprint_limit_min: state.sprint_limit_min } : {}),
      ...(isFreestyle() ? { freestyle: true } : {}),
      count: state.count, rating_min: state.rating_min, rating_max: state.rating_max,
      tags_include: [...state.tags_include], tags_avoid: [...state.tags_avoid],
      avoid_interactive: state.avoid_interactive,
      since_year: state.since_year,
      custom_problems: state.use_custom && !isFreestyle() ? state.custom_problems : null,
      duration_min: state.duration_min,
      freeze_min: realRules() === "icpc" ? state.freeze_min : 0,
      handicap: state.mode === "team" || state.rules === "sprint" || isFreestyle() ? null : state.handicap,
      ...(state.mode === "team" ? { team_size: state.team_size, team_count: state.team_count, fill: state.fill, uneven_ok: state.uneven_ok } : {}),
    },
  };
}

const err = el("div.error-box", { hidden: true, role: "alert" });

function flash(msg) {
  try { sessionStorage.setItem("flash", msg); } catch { /* shown nowhere then; the lobby still opens */ }
}

async function submit(e) {
  e.preventDefault();
  err.hidden = true;
  try {
    if (editCode) {
      const p = payload();
      await api(`/api/matches/${editCode}/settings`, { method: "PATCH", body: { rules: p.rules, settings: p.settings } });
      location.href = `/m/${editCode}`;
    } else {
      const r = await api("/api/matches", { method: "POST", body: payload() });
      if (challengeHandle) {
        try {
          const c = await api(`/api/matches/${r.code}/invite`, { method: "POST", body: { handle: challengeHandle } });
          flash(c.online ? `Challenge sent to ${c.handle}.` : `Challenge saved. ${c.handle} sees it next time they're on.`);
        } catch (ex) { flash(`The lobby is open, but the challenge didn't go through: ${ex.message}`); }
      }
      location.href = `/m/${r.code}`;
    }
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
}

function numberInput(id, value, min, max, onChange) {
  return el("input", { type: "number", id, value, min, max, inputmode: "numeric",
    oninput: (e) => { onChange(Number(e.target.value)); refreshPreview(); } });
}

function render() {
  const randomFields = el("div.stack",
    el("div.grid-2",
      el("div.row",
        el("div.field.grow", el("label", { for: "count" }, "Problems"), numberInput("count", state.count, 1, 10, (v) => state.count = v)),
        el("div.field.grow", el("label", { for: "since" }, "From year"),
          el("select", { id: "since", title: "Only problems from contests held this year or later",
            onchange: (e) => { state.since_year = e.target.value ? Number(e.target.value) : null; refreshPreview(); } },
          el("option", { value: "", selected: !state.since_year }, "Any"),
          YEARS.map((y) => el("option", { value: y, selected: state.since_year === y }, `${y}+`))))),
      el("div.row",
        el("div.field.grow", el("label", { for: "rmin" }, "Rating from"), ratingSelect("rmin", state.rating_min, (v) => state.rating_min = v)),
        el("div.field.grow", el("label", { for: "rmax" }, "to"), ratingSelect("rmax", state.rating_max, (v) => state.rating_max = v)))),
    el("div",
      el("label", "Tags — click once to require, twice to avoid"),
      tags.length ? tagPicker() : el("p.small.muted", "Tags appear once the problem list has loaded.")),
    el("label.check", el("input", { type: "checkbox", checked: state.avoid_interactive,
      onchange: (e) => {
        state.avoid_interactive = e.target.checked;
        if (e.target.checked) state.tags_include.delete("interactive");
        render();
        refreshPreview();
      } }), "Avoid interactive problems"),
    preview);

  const customFields = el("div.stack",
    el("div.field", el("label", { for: "custom" }, "Problem IDs"),
      el("textarea", { id: "custom", placeholder: "2043C, 2051E, 2060D", oninput: (e) => { state.custom_problems = e.target.value; } },
        state.custom_problems)),
    el("button.btn.small", { type: "button", onclick: validateCustom }, "Check list"),
    customResult);

  const panel = (title, aside, ...body) => el("section.panel",
    el("div.panel-head", el("h2", title), aside || null),
    el("div.panel-body.stack", body));
  app.replaceChildren(el("form.page", { onsubmit: submit, novalidate: true },
    el("div.page-head",
      el("div",
        el("h1", editCode ? "Match settings" : TITLES[state.mode]),
        el("p.lede", editCode ? "Changes reset everyone's Ready."
          : challengeHandle ? ["Challenging ", el("strong.mono", challengeHandle), ". Set the rules; they get the invite when you create it."]
            : "Set the rules, then share the link. Problems stay hidden until everyone is ready.")),
      editCode ? null : segmented([["friend", "Friend duel"], ["team", "Team duel"], ["group", "Group room"]], state.mode, (v) => {
        state.mode = v;
        if (v === "team" && state.rules === "freestyle") state.rules = "icpc";
      }, "Mode")),
    el("div.stack",
      (() => {
        const rules = panel("Rules", null,
          segmented([["icpc", "ICPC"], ["lockout", "Lockout"], ["sprint", "Sprint"],
            ...(state.mode === "team" ? [] : [["freestyle", "Freestyle"]])], state.rules, (v) => {
            state.rules = v;
            if (v === "lockout" && state.handicap && state.handicap.type !== "extra_time") state.handicap = null;
            if (v === "sprint" || v === "freestyle") state.handicap = null;
            if (v === "freestyle") { state.use_custom = false; state.count = 1; }
          }, "Rules"),
          el("p.small.muted", RULE_TEXT[state.rules]),
          state.rules === "sprint"
            ? el("div.field", el("label", { for: "sprint-limit" }, "Time per problem"),
              el("select", { id: "sprint-limit", onchange: (e) => { state.sprint_limit_min = Number(e.target.value); render(); } },
                SPRINT_LIMITS.map(([v, t]) => el("option", { value: v, selected: v === state.sprint_limit_min }, t))),
              el("p.small.muted", state.sprint_limit_min
                ? `Nobody solves it in ${state.sprint_limit_min} minutes? It's skipped (no points) and the next one opens.`
                : "A problem stays open until someone solves it."))
            : null);
        if (state.mode !== "team") return rules;
        const sizes = [2, 3, 4, 5, 6].map((v) => [v, String(v)]);
        const teams = panel("Teams", null,
          el("div.row",
            el("div.field", el("label", "Teams"), segmented(sizes, state.team_count, (v) => state.team_count = v, "Number of teams")),
            el("div.field", el("label", "Players per team"), segmented(sizes, state.team_size, (v) => state.team_size = v, "Players per team"))),
          el("label.check", el("input", { type: "checkbox", checked: state.fill === "draft", onchange: (e) => { state.fill = e.target.checked ? "draft" : "free"; } }),
            "Captains' draft: you pick captains, they pick teams"),
          el("p.small.muted", `${state.team_size * state.team_count} players. A problem counts for a team when any member solves it. `
            + (state.fill === "draft" ? "Captains take turns picking in the lobby. Unrated."
              : "Pick sides in the lobby, or shuffle / balance by rating there. Unrated.")));
        return el("div.grid-2.equal", teams, rules);
      })(),
      isFreestyle()
        ? panel("Random problem", null,
          el("p.small.muted", "The same for everyone. Each player also gets one problem from each opponent, set in the lobby."),
          randomFields)
        : panel("Problems", segmented([[false, "Random"], [true, "My own list"]], state.use_custom, (v) => state.use_custom = v, "Problem source"),
          state.use_custom ? customFields : randomFields),
      panel("Time", null,
        el("div.grid-2",
          el("div.field", el("label", { for: "dur" }, "Duration (minutes)"), numberInput("dur", state.duration_min, 15, 300, (v) => state.duration_min = v)),
          realRules() === "icpc"
            ? el("div.field", el("label", { for: "frz" }, "Freeze last N minutes (0 = off)"), numberInput("frz", state.freeze_min, 0, 299, (v) => state.freeze_min = v))
            : null)),
      el("p.small.muted", state.mode === "team" ? "Team duels have no handicaps."
        : state.rules === "sprint" ? "Sprint has no handicaps."
        : isFreestyle() ? "Freestyle has no handicaps."
        : state.mode === "group" ? "Handicaps are set in the lobby once players have joined."
          : "Handicaps are set in the lobby once your friend has joined."),
      err,
      el("div.actions",
        el("button.btn.primary.big", { type: "submit" }, editCode ? "Save settings" : CREATE[state.mode]),
        editCode ? el("a.btn.ghost.big", { href: `/m/${editCode}` }, "Cancel") : null))));
  refreshPreview();
}

render();
