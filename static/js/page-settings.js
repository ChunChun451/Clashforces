import { $, api, el, getMe, handleEl, mountTopbar, toast } from "./api.js";
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

function render() {
  const blocked = me.active
    ? el("p.note", "You can't change your handle while you are in a match or the queue.")
    : verifyWidget({ startPath: "/api/me/handle/start", buttonLabel: "Verify new handle", onDone: () => location.reload() });

  app.replaceChildren(el("div.stack",
    el("h1", "Settings"),
    el("section.stack",
      el("div.section-title", "Codeforces handle"),
      el("p", "Linked: ", handleEl(me.user.handle, me.user.cf_rating)),
      me.user.needs_reverify ? el("p.error-box", "Codeforces no longer knows this handle. Verify your new one below.") : null,
      el("p.muted.small", "Renamed your account on Codeforces? Verify the new handle here. Your history stays."),
      blocked),
    el("hr"),
    el("section.stack",
      el("div.section-title", "Browser notifications"),
      el("p.muted.small", "Duel invites, an opponent solving a problem, 5 minutes left, and match end. Shown while this site is open in a background tab."),
      el("label.check", el("input", { type: "checkbox", checked: notifications.enabled(), onchange: async (e) => {
        if (e.target.checked) {
          const ok = await notifications.enable();
          if (!ok) { e.target.checked = false; toast("Notifications are blocked in your browser settings.", true); }
        } else notifications.disable();
      } }), "Enable notifications")),
    el("hr"),
    el("section.row",
      el("button.btn.ghost", { type: "button", onclick: () => logout(false) }, "Log out"),
      el("button.btn.ghost", { type: "button", onclick: () => logout(true) }, "Log out everywhere")),
  ));
}

async function logout(everywhere) {
  await api(`/api/auth/logout${everywhere ? "?everywhere=1" : ""}`, { method: "POST" });
  location.href = "/";
}
