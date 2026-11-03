import { $, api, el, emptyState, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { connect, on } from "./ws.js";

mountTopbar("/friends");
const app = $("#app");
const me = await getMe();
if (!me.user) location.href = "/login?next=/friends";

let friends = (await api("/api/friends")).friends;
let board = (await api("/api/friends/leaderboard")).rows;

async function reload() {
  friends = (await api("/api/friends")).friends;
  board = (await api("/api/friends/leaderboard")).rows;
  render();
}

async function add(e) {
  e.preventDefault();
  const input = $("#friend-handle");
  try { await api("/api/friends", { method: "POST", body: { handle: input.value.trim() } }); input.value = ""; await reload(); }
  catch (ex) { toast(ex.message, true); }
}

async function invite(handle) {
  const active = (await api("/api/me")).active;
  let code = active && active.kind === "match" ? active.code : null;
  try {
    if (!code) code = (await api("/api/matches", { method: "POST", body: { mode: "friend", rules: "icpc", settings: {} } })).code;
    const r = await api(`/api/matches/${code}/invite`, { method: "POST", body: { handle } });
    toast(r.online ? `Invite sent to ${handle}` : `${handle} is offline — share the link instead`);
    location.href = `/m/${code}`;
  } catch (e) { toast(e.message, true); }
}

function render() {
  const record = (h) => `${h.win} W – ${h.loss} L${h.draw ? ` – ${h.draw} D` : ""}`;
  const online = friends.filter((f) => f.online).length;
  app.replaceChildren(el("div.page",
    el("div.page-head",
      el("div", el("h1", "Friends"), el("p.lede", "See who's online, challenge them, and compare duel ratings.")),
      el("form.inline-form", { onsubmit: add },
        el("label.sr-only", { for: "friend-handle" }, "Codeforces handle"),
        el("input.mono", { id: "friend-handle", type: "text", placeholder: "Add by handle", autocomplete: "off", spellcheck: "false" }),
        el("button.btn", { type: "submit" }, "Add friend"))),
    el("div.grid-2",
      el("section.panel",
        el("div.panel-head", el("h2", "Your friends"), friends.length ? el("span.small.muted", `${online} of ${friends.length} online`) : null),
        friends.length
          ? el("ul.panel-list", friends.map((f) => el("li",
            el("span", el("span", { class: "online-dot" + (f.online ? " on" : ""), title: f.online ? "Online" : "Offline", dataset: { handle: f.handle } }),
              handleEl(f.handle, f.cf_rating)),
            el("span.row",
              el("span.meta.mono", { title: "Your head-to-head record" }, record(f.h2h)),
              el("button.btn.small", { type: "button", onclick: () => invite(f.handle) }, "Duel")))))
          : emptyState({ iconName: "userPlus", title: "No friends yet",
            text: "Add friends by their Codeforces handle once they've logged in here. Or send them a duel link to get them started.",
            action: el("a.btn.small", { href: "/new" }, "Create a duel link") })),
      el("section.panel",
        el("div.panel-head", el("h2", "Leaderboard"), el("span.small.muted", "Duel rating")),
        el("table",
          el("tbody", board.map((u, i) => el("tr", { class: u.is_me ? "me" : null },
            el("td.num.rank", String(i + 1)),
            el("td", handleEl(u.handle, u.cf_rating), u.is_me ? el("span.muted.small", "  you") : null),
            el("td.num", String(u.duel_rating)))))),
        board.length < 2 ? el("p.small.muted.panel-body", "Add friends to see how you stack up.") : null))));
}

render();
connect();
on("presence", (d) => {
  const f = friends.find((x) => x.handle === d.handle);
  if (f) { f.online = d.online; render(); }
});
