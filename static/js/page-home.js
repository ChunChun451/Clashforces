import { $, el, getMe, mountTopbar } from "./api.js";

mountTopbar("/");
const me = await getMe();
$("#app").append(el("div.stack",
  el("h1", "Clashforces"),
  el("p.muted", "Duel your friends on real Codeforces problems."),
  me.user ? el("p", "Logged in.") : el("a.btn.primary", { href: "/login" }, "Log in")));
