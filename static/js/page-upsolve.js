import { $, api, el, getMe, mountTopbar, toast } from "./api.js";

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
  app.replaceChildren(el("div.stack",
    el("div.spread",
      el("h1", "Upsolve"),
      el("button.btn.small", { type: "button", disabled: !data.can_refresh, onclick: refresh,
        title: "Checks Codeforces for new accepted submissions" }, "Check Codeforces")),
    el("p.muted", "Problems you didn't solve during a match. They tick off automatically once you get Accepted on Codeforces."),
    el("div.segmented", { role: "group", "aria-label": "Filter" },
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
      : el("p.empty", showDone ? "Nothing upsolved yet." : "Nothing to upsolve. Nice.")));
}

async function refresh() {
  try { data = await api("/api/me/upsolve/refresh", { method: "POST" }); toast("Checked Codeforces"); render(); }
  catch (e) { toast(e.message, true); }
}

render();
