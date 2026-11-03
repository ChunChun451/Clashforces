import { $, el, mountTopbar } from "./api.js";
import { verifyWidget } from "./verify-widget.js";

mountTopbar("/login");
const next = new URLSearchParams(location.search).get("next") || "/";
const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/";

$("#app").append(el("div.stack",
  el("h1", "Log in with Codeforces"),
  el("p.muted", "No password. You prove the handle is yours with one throwaway submission."),
  verifyWidget({
    startPath: "/api/auth/start",
    buttonLabel: "Continue",
    onDone: () => { location.href = safeNext; },
  })));
