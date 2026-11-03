import { $, el, mountTopbar } from "./api.js";
import { verifyWidget } from "./verify-widget.js";

mountTopbar("/login");
const next = new URLSearchParams(location.search).get("next") || "/";
const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";

$("#app").append(el("div.stack",
  el("div.stack.login-head",
    el("h1", "Log in with Codeforces"),
    el("p.lede", "No password. You prove the handle is yours with one throwaway submission that fails to compile.")),
  el("div.card", verifyWidget({
    startPath: "/api/auth/start",
    buttonLabel: "Continue",
    onDone: () => { location.href = safeNext; },
  })),
  el("p.small.muted", "New here? The same steps create your account. We only read public data from the Codeforces API.")));
