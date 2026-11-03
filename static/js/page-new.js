// Create a friend duel or group room, or edit a lobby's settings (?edit=CODE).
import { $, api, el, getMe, mountTopbar } from "./api.js";

mountTopbar("/");
const app = $("#app");
const params = new URLSearchParams(location.search);
const editCode = params.get("edit");
const me = await getMe();
if (!me.user) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;

const RATINGS = [];
for (let r = 800; r <= 3500; r += 100) RATINGS.push(r);

const state = {
  mode: params.get("mode") === "group" ? "group" : "friend",
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
    handicap: s.handicap,
  });
}

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
  return el("div.chips", { role: "group", "aria-label": "Tags" }, tags.filter((t) => t !== "*special").map((t) => {
    const inc = state.tags_include.has(t), avo = state.tags_avoid.has(t);
    return el("button", {
      type: "button",
      class: `chip${inc ? " on" : ""}${avo ? " avoid" : ""}`,
      "aria-label": `${t}: ${inc ? "required" : avo ? "avoided" : "any"}`,
      onclick: () => {
        if (inc) { state.tags_include.delete(t); state.tags_avoid.add(t); }
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
      ...r.warnings.map((w) => el("div", { style: "color: var(--warn)" }, `Warning: ${w}`)));
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
      handicap: state.handicap,
    },
  };
}

const err = el("div.error-box", { hidden: true, role: "alert" });

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
        el("div.field", { style: "flex:1" }, el("label", { for: "rmin" }, "Rating from"), ratingSelect("rmin", state.rating_min, (v) => state.rating_min = v)),
        el("div.field", { style: "flex:1; margin-top:0" }, el("label", { for: "rmax" }, "to"), ratingSelect("rmax", state.rating_max, (v) => state.rating_max = v)))),
    el("div",
      el("label", "Tags — click once to require, twice to avoid"),
      tags.length ? tagPicker() : el("p.small.muted", "Tags appear once the problem list has loaded.")),
    el("label.check", el("input", { type: "checkbox", checked: state.avoid_interactive,
      onchange: (e) => { state.avoid_interactive = e.target.checked; refreshPreview(); } }), "Avoid interactive problems"),
    preview);

  const customFields = el("div.stack",
    el("div.field", el("label", { for: "custom" }, "Problem IDs"),
      el("textarea", { id: "custom", placeholder: "2043C, 2051E, 2060D", oninput: (e) => { state.custom_problems = e.target.value; } },
        state.custom_problems)),
    el("button.btn.small", { type: "button", onclick: validateCustom }, "Check list"),
    customResult);

  app.replaceChildren(el("form.stack", { onsubmit: submit, novalidate: true },
    el("h1", editCode ? "Match settings" : state.mode === "group" ? "New group room" : "New friend duel"),
    editCode ? null : segmented([["friend", "Friend duel"], ["group", "Group room (3–10)"]], state.mode, (v) => state.mode = v, "Mode"),
    el("section.stack",
      el("div.section-title", "Rules"),
      segmented([["icpc", "ICPC"], ["lockout", "Lockout"]], state.rules, (v) => {
        state.rules = v;
        if (v === "lockout" && state.handicap && state.handicap.type !== "extra_time") state.handicap = null;
      }, "Rules"),
      el("p.small.muted", state.rules === "icpc"
        ? "Most solved wins. Ties go to lower penalty: minutes to each accept + 20 per wrong try."
        : "First to solve a problem takes its points. Harder problems are worth more. Ends early when the leader can't be caught.")),
    el("section.stack",
      el("div.spread", el("div.section-title", "Problems"),
        segmented([[false, "Random"], [true, "My own list"]], state.use_custom, (v) => state.use_custom = v, "Problem source")),
      state.use_custom ? customFields : randomFields),
    el("section.stack",
      el("div.section-title", "Time"),
      el("div.grid-2",
        el("div.field", el("label", { for: "dur" }, "Duration (minutes)"), numberInput("dur", state.duration_min, 15, 300, (v) => state.duration_min = v)),
        state.rules === "icpc"
          ? el("div.field", { style: "margin-top:0" }, el("label", { for: "frz" }, "Freeze last N minutes (0 = off)"), numberInput("frz", state.freeze_min, 0, 299, (v) => state.freeze_min = v))
          : null)),
    el("p.small.muted", "Handicaps are set in the lobby once your friend has joined."),
    err,
    el("div.actions",
      el("button.btn.primary.big", { type: "submit" }, editCode ? "Save" : "Create"),
      editCode ? el("a.btn.ghost", { href: `/m/${editCode}` }, "Cancel") : null)));
  refreshPreview();
}

render();
