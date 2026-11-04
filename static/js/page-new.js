// Create a friend duel, team duel or group room, or edit a lobby's settings (?edit=CODE).
import { $, api, el, getMe, mountTopbar } from "./api.js";

mountTopbar("/");
const app = $("#app");
const params = new URLSearchParams(location.search);
const editCode = params.get("edit");
const challengeHandle = editCode ? null : (params.get("challenge") || "").trim() || null;
const me = await getMe();
if (!me.user) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;

const RATINGS = [];
for (let r = 800; r <= 3500; r += 100) RATINGS.push(r);

const state = {
  mode: ["group", "team"].includes(params.get("mode")) ? params.get("mode") : "friend",
  team_size: 2, team_count: 2, fill: "free", uneven_ok: false,
  rules: "icpc",
  count: 3, rating_min: 1200, rating_max: 1600,
  tags_include: new Set(), tags_avoid: new Set(),
  avoid_interactive: true,
  use_custom: false, custom_problems: "",
  duration_min: 60, freeze_min: 0,
  handicap: null,
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
    uneven_ok: !!s.uneven_ok,
  });
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

function payload() {
  return {
    mode: state.mode,
    rules: state.rules,
    settings: {
      rules: state.rules,
      count: state.count, rating_min: state.rating_min, rating_max: state.rating_max,
      tags_include: [...state.tags_include], tags_avoid: [...state.tags_avoid],
      avoid_interactive: state.avoid_interactive,
      custom_problems: state.use_custom ? state.custom_problems : null,
      duration_min: state.duration_min,
      freeze_min: state.rules === "icpc" ? state.freeze_min : 0,
      handicap: state.mode === "team" ? null : state.handicap,
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
      el("div.field", el("label", { for: "count" }, "Problems"), numberInput("count", state.count, 1, 10, (v) => state.count = v)),
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
      editCode ? null : segmented([["friend", "Friend duel"], ["team", "Team duel"], ["group", "Group room"]], state.mode, (v) => state.mode = v, "Mode")),
    el("div.stack",
      (() => {
        const rules = panel("Rules", null,
          segmented([["icpc", "ICPC"], ["lockout", "Lockout"]], state.rules, (v) => {
            state.rules = v;
            if (v === "lockout" && state.handicap && state.handicap.type !== "extra_time") state.handicap = null;
          }, "Rules"),
          el("p.small.muted", state.rules === "icpc"
            ? "Most solved wins. Ties go to lower penalty: minutes to each accept + 20 per wrong try."
            : "First to solve a problem takes its points. Harder problems are worth more. Ends early when the leader can't be caught."));
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
      panel("Problems", segmented([[false, "Random"], [true, "My own list"]], state.use_custom, (v) => state.use_custom = v, "Problem source"),
        state.use_custom ? customFields : randomFields),
      panel("Time", null,
        el("div.grid-2",
          el("div.field", el("label", { for: "dur" }, "Duration (minutes)"), numberInput("dur", state.duration_min, 15, 300, (v) => state.duration_min = v)),
          state.rules === "icpc"
            ? el("div.field", el("label", { for: "frz" }, "Freeze last N minutes (0 = off)"), numberInput("frz", state.freeze_min, 0, 299, (v) => state.freeze_min = v))
            : null)),
      el("p.small.muted", state.mode === "team" ? "Team duels have no handicaps."
        : state.mode === "group" ? "Handicaps are set in the lobby once players have joined."
          : "Handicaps are set in the lobby once your friend has joined."),
      err,
      el("div.actions",
        el("button.btn.primary.big", { type: "submit" }, editCode ? "Save settings" : CREATE[state.mode]),
        editCode ? el("a.btn.ghost.big", { href: `/m/${editCode}` }, "Cancel") : null))));
  refreshPreview();
}

render();
