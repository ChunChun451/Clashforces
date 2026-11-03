import { $, api, el, getMe, mountTopbar, toast } from "./api.js";
import { connect, on } from "./ws.js";
import { notify } from "./notify.js";

mountTopbar("/");
const app = $("#app");
const me = await getMe();

function joinForm() {
  const input = el("input.mono", { type: "text", id: "join-code", placeholder: "ABC123", maxlength: 6, autocomplete: "off",
    spellcheck: "false", style: "text-transform: uppercase; max-width: 160px" });
  return el("form.row", { onsubmit: (e) => { e.preventDefault(); const c = input.value.trim().toUpperCase(); if (c) location.href = `/m/${c}`; } },
    el("label", { for: "join-code", class: "sr-only", style: "position:absolute;left:-9999px" }, "Match code"),
    input, el("button.btn", { type: "submit" }, "Join or watch"));
}

function modeCard(title, text, href, primary = false) {
  return el("a.card", { href, style: "display:block; border:0" },
    el("h2", title), el("p.muted.small", { style: "margin:0" }, text),
    primary ? el("div", { style: "margin-top: var(--s4)" }, el("span.btn.primary.small", "Start")) : null);
}

function render() {
  if (!me.user) {
    app.replaceChildren(el("div.stack",
      el("h1", { style: "font-size: clamp(32px, 6vw, 52px); max-width: 16ch" }, "Duel on real Codeforces problems."),
      el("p.muted", { style: "max-width: 52ch" }, "Pick problems nobody has tried, race the clock, and submit on Codeforces as usual. We track every verdict through the official API."),
      el("div.actions", el("a.btn.primary.big", { href: "/login" }, "Log in with your handle"), joinForm())));
    return;
  }
  const active = me.active;
  app.replaceChildren(el("div.stack",
    el("h1", "Play"),
    active ? el("div.banner.good",
      active.kind === "queue" ? "You are in the ranked queue." : `You are in match ${active.code}.`,
      el("a.btn.small", { href: active.kind === "queue" ? "/queue" : `/m/${active.code}` }, "Open")) : null,
    el("div.grid-2",
      modeCard("Friend duel", "1 vs 1 with a link. ICPC or Lockout, any filters, optional handicap. Unrated.", "/new", true),
      modeCard("Group room", "3–10 players with a contest-style standings table. Unrated.", "/new?mode=group"),
      modeCard("Ranked", "3 problems, 60 minutes, ICPC. Matched by duel rating.", "/queue"),
      modeCard("Upsolve", "Problems you didn't solve in your matches.", "/upsolve")),
    el("section.stack", el("div.section-title", "Have a code?"), joinForm())));
}

render();
connect();
on("invite", (d) => {
  toast(`${d.from} invited you to a match — ${d.code}`);
  notify(`${d.from} invited you to a duel`, "invite", { always: true });
});
