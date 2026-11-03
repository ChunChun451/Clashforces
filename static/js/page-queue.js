import { $, api, el, fmtDuration, getMe, mountTopbar, toast } from "./api.js";
import { connect, on } from "./ws.js";
import { notify } from "./notify.js";

mountTopbar("/queue");
const app = $("#app");
const me = await getMe();
if (!me.user) location.href = "/login?next=/queue";

let st = await api("/api/queue");
let baseWait = st.waited_s || 0;
let since = Date.now();

function render() {
  const searching = st.in_queue;
  app.replaceChildren(el("div.stack.center",
    el("h1", "Ranked"),
    el("p.lede.queue-lede", "3 problems, 60 minutes, ICPC rules. Problems near your average Codeforces rating."),
    el("div", { class: `radar${searching ? "" : " idle"}`, "aria-hidden": "true" },
      el("div.radar-core", { id: "q-time" }, searching ? fmtDuration(baseWait) : String(me.user.duel_rating))),
    searching
      ? el("div.card.stack.queue-card",
        el("p", { role: "status" }, "Looking for an opponent within ", el("span.mono.accent", { id: "q-win" }, `±${st.window}`), " duel rating"),
        el("p.small.muted", `Your duel rating ${st.duel_rating}. The range widens every 30 seconds.`),
        el("div", el("button.btn", { type: "button", onclick: leave }, "Cancel")))
      : el("div.card.stack.queue-card",
        el("p.small.muted", `Your duel rating: ${me.user.duel_rating}. Wins against stronger players move it more.`),
        me.active && me.active.kind === "match" ? el("p.note", "Finish your current match first: ", el("a", { href: `/m/${me.active.code}` }, me.active.code))
          : el("div", el("button.btn.primary.big", { type: "button", onclick: join }, "Find a match")))));
}

async function join() {
  try { st = await api("/api/queue/join", { method: "POST" }); baseWait = 0; since = Date.now(); render(); }
  catch (e) { toast(e.message, true); }
}
async function leave() {
  st = await api("/api/queue/leave", { method: "POST" });
  render();
}

on("queue.status", (d) => { st = d; baseWait = d.waited_s; since = Date.now(); const w = $("#q-win"); if (w) w.textContent = `±${d.window}`; });
on("queue.matched", (d) => { notify("Opponent found!", "queue", { always: true }); location.href = `/m/${d.code}`; });
on("queue.requeued", () => toast("Your opponent didn't press ready. Back in the queue."));
connect();
render();
setInterval(() => {
  if (!st.in_queue) return;
  const t = $("#q-time");
  if (t) t.textContent = fmtDuration(baseWait + (Date.now() - since) / 1000);
}, 500);
