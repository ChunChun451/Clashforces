import { $, api, el, getMe, handleEl, mountTopbar, toast } from "./api.js";
import { verifyWidget } from "./verify-widget.js";

mountTopbar("/settings");
const app = $("#app");
const me = await getMe();
if (!me.user) {
  location.href = "/login?next=/settings";
} else {
  render();
}

function render() {
  const hook = el("input", { type: "url", id: "hook", placeholder: "https://discord.com/api/webhooks/…", value: me.discord_webhook || "" });
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
      el("div.section-title", "Discord"),
      el("div.field", el("label", { for: "hook" }, "Webhook for your match results (optional)"), hook),
      el("button.btn", { type: "button", onclick: async () => {
        try {
          await api("/api/me/settings", { method: "PATCH", body: { discord_webhook: hook.value } });
          toast("Saved");
        } catch (e) { toast(e.message, true); }
      } }, "Save")),
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
