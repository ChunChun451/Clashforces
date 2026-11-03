import { $, api, el, getMe, handleEl, mountTopbar, toast } from "./api.js";
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
  app.replaceChildren(el("div.stack",
    el("h1", "Friends"),
    el("form.row", { onsubmit: add },
      el("label", { for: "friend-handle", style: "position:absolute;left:-9999px" }, "Handle"),
      el("input.mono", { id: "friend-handle", type: "text", placeholder: "Add by handle", style: "max-width: 260px", autocomplete: "off" }),
      el("button.btn", { type: "submit" }, "Add")),
    el("div.grid-2",
      el("section.stack", el("div.section-title", "Your friends"),
        friends.length
          ? el("div.lobby-players", friends.map((f) => el("div.lobby-player",
            el("span", el("span", { class: "online-dot" + (f.online ? " on" : ""), title: f.online ? "Online" : "Offline", dataset: { handle: f.handle } }),
              handleEl(f.handle, f.cf_rating)),
            el("span.row", { style: "gap: var(--s3)" },
              el("span.small.muted.mono", { title: "Your head-to-head record" }, `You ${record(f.h2h)}`),
              el("button.btn.small", { type: "button", onclick: () => invite(f.handle) }, "Duel")))))
          : el("p.muted.small", "Add friends by their Codeforces handle once they've joined.")),
      el("section.stack", el("div.section-title", "Leaderboard · duel rating"),
        el("div.table-wrap", el("table",
          el("tbody", board.map((u, i) => el("tr", { class: u.is_me ? "me" : null },
            el("td.num", { style: "width:40px" }, String(i + 1)),
            el("td", handleEl(u.handle, u.cf_rating), u.is_me ? el("span.muted.small", "  you") : null),
            el("td.num", String(u.duel_rating))))))))),
  ));
}

render();
connect();
on("presence", (d) => {
  const f = friends.find((x) => x.handle === d.handle);
  if (f) { f.online = d.online; render(); }
});
