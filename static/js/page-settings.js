import { $, api, el, getMe, handleEl, mountTopbar, toast, toggleTheme } from "./api.js";
import { verifyWidget } from "./verify-widget.js";
import * as notifications from "./notify.js";

mountTopbar("/settings");
const app = $("#app");
const me = await getMe();
if (!me.user) {
  location.href = "/login?next=/settings";
} else {
  render();
}

function setTheme(theme) {
  const root = document.documentElement;
  if ((root.dataset.theme === "light") === (theme === "light")) return;
  toggleTheme();
  render();
}

function render() {
  const blocked = me.active
    ? el("p.note", "You can't change your handle while you are in a match or the queue.")
    : verifyWidget({ startPath: "/api/me/handle/start", buttonLabel: "Verify new handle", onDone: () => location.reload() });
  const light = document.documentElement.dataset.theme === "light";

  const panel = (title, text, ...body) => el("section.panel",
    el("div.panel-head", el("div", el("h2", title), text ? el("p.small.muted", text) : null)),
    el("div.panel-body.stack", body));

  app.replaceChildren(el("div.page",
    el("div.page-head", el("div", el("h1", "Settings"), el("p.lede", "Your linked handle, how the site looks, and where you're logged in."))),
    el("div.stack",
      panel("Codeforces handle", "Renamed your account on Codeforces? Verify the new handle here. Your history stays.",
        el("div.spread",
          el("span", handleEl(me.user.handle, me.user.cf_rating)),
          el("span.small.muted.mono", me.user.cf_rating ? `CF ${me.user.cf_rating}` : "unrated")),
        me.user.needs_reverify ? el("p.error-box", "Codeforces no longer knows this handle. Verify your new one below.") : null,
        blocked),
      panel("Appearance", "Saved in this browser.",
        el("div.segmented", { role: "group", "aria-label": "Theme" },
          el("button", { type: "button", "aria-pressed": String(!light), onclick: () => setTheme("dark") }, "Dark"),
          el("button", { type: "button", "aria-pressed": String(light), onclick: () => setTheme("light") }, "Light"))),
      panel("Browser notifications", "Duel invites, an opponent solving a problem, 5 minutes left, and match end. Shown while this site is open in a background tab.",
        el("label.check", el("input", { type: "checkbox", checked: notifications.enabled(), onchange: async (e) => {
          if (e.target.checked) {
            const ok = await notifications.enable();
            if (!ok) { e.target.checked = false; toast("Notifications are blocked in your browser settings.", true); }
          } else notifications.disable();
        } }), "Enable notifications")),
      panel("Session", "“Log out everywhere” also ends sessions on your other devices.",
        el("div.actions",
          el("button.btn", { type: "button", onclick: () => logout(false) }, "Log out"),
          el("button.btn.ghost.danger", { type: "button", onclick: () => logout(true) }, "Log out everywhere"))))));
}

async function logout(everywhere) {
  await api(`/api/auth/logout${everywhere ? "?everywhere=1" : ""}`, { method: "POST" });
  location.href = "/";
}
