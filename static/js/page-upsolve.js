import { $, api, el, getMe, mountTopbar, toast } from "./api.js";
import { icon } from "./icons.js";

mountTopbar("/upsolve");
const app = $("#app");
const me = await getMe();
if (!me.user) location.href = "/login?next=/upsolve";

let data = await api("/api/me/upsolve");
let showDone = false;

function render() {
  const open = data.items.filter((i) => !i.done_at);
  const done = data.items.filter((i) => i.done_at);
  const list = showDone ? done : open;
  app.replaceChildren(el("div.page",
    el("div.page-head",
      el("div", el("h1", "Upsolve"),
        el("p.lede", "Problems you didn't solve during a match. They tick off automatically once you get Accepted on Codeforces.")),
      el("button.btn", { type: "button", disabled: !data.can_refresh, onclick: refresh,
        title: "Checks Codeforces for new accepted submissions" }, "Check Codeforces")),
    el("div.stack", el("div.segmented", { role: "group", "aria-label": "Filter" },
      el("button", { type: "button", "aria-pressed": String(!showDone), onclick: () => { showDone = false; render(); } }, `To do (${open.length})`),
      el("button", { type: "button", "aria-pressed": String(showDone), onclick: () => { showDone = true; render(); } }, `Done (${done.length})`)),
    list.length
      ? el("div.table-wrap", el("table",
        el("thead", el("tr", el("th", "Problem"), el("th.num", "Rating"), el("th", "Tags"), el("th", "From"))),
        el("tbody", list.map((i) => el("tr",
          el("td", i.done_at ? el("span.accent", "✓ ") : null,
            el("a", { href: i.url, target: "_blank", rel: "noopener" }, i.name), el("span.muted.small.mono", `  ${i.key}`)),
          el("td.num", i.rating ? String(i.rating) : "—"),
          el("td", el("div.chips", i.tags.slice(0, 3).map((t) => el("span.chip", t)))),
          el("td", el("a.mono.small", { href: `/m/${i.match_code}` }, i.match_code)))))))
      : el("div.empty-state.boxed",
        el("div.empty-icon", icon(showDone ? "check" : "retry")),
        el("strong", showDone ? "Nothing upsolved yet" : data.items.length ? "All caught up" : "Nothing to upsolve"),
        el("p", showDone ? "Problems you finish after a match land here." : data.items.length
          ? "Every problem from your matches is solved. Play another to refill the list."
          : "After a match, the problems you didn't solve show up here."),
        el("a.btn.small.primary", { href: "/new" }, "Start a friend duel")))));
}

async function refresh() {
  try { data = await api("/api/me/upsolve/refresh", { method: "POST" }); toast("Checked Codeforces"); render(); }
  catch (e) { toast(e.message, true); }
}

render();
