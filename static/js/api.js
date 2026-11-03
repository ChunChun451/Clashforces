// Shared helpers: fetch wrapper, DOM builder, rating colors, top bar, toasts.

export async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const msg = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ")
      : `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// el("div.card", {onclick}, child, "text", ...) — text is always set via textContent.
export function el(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);

export function ratingClass(r) {
  if (!r) return "r-gray";
  if (r < 1200) return "r-gray";
  if (r < 1400) return "r-green";
  if (r < 1600) return "r-cyan";
  if (r < 1900) return "r-blue";
  if (r < 2100) return "r-violet";
  if (r < 2400) return "r-orange";
  if (r < 3000) return "r-red";
  return "r-red r-legend";
}

export function handleEl(handle, rating, { link = true } = {}) {
  const cls = `handle ${ratingClass(rating)}`;
  if (!handle) return el("span.muted", "unknown");
  if (!link) return el("span", { class: cls }, handle);
  return el("a", { class: cls, href: `/u/${encodeURIComponent(handle)}` }, handle);
}

export function toast(msg, bad = false) {
  let box = $(".toast-box");
  if (!box) { box = el("div.toast-box", { role: "status", "aria-live": "polite" }); document.body.append(box); }
  const t = el("div.toast" + (bad ? ".bad" : ""), msg);
  box.append(t);
  setTimeout(() => t.remove(), 4000);
}

export function cfProblemUrl(key) {
  const m = /^(\d+)([A-Z]\d*)$/.exec(key);
  return m ? `https://codeforces.com/contest/${m[1]}/problem/${m[2]}` : "#";
}

let mePromise = null;
export function getMe() {
  if (!mePromise) mePromise = api("/api/me").catch(() => ({ user: null }));
  return mePromise;
}

// Top bar shared by every page.
export async function mountTopbar(current) {
  const bar = $("#topbar");
  if (!bar) return;
  const links = [["/", "Play"], ["/queue", "Ranked"], ["/friends", "Friends"], ["/upsolve", "Upsolve"]];
  const nav = el("nav.nav", { "aria-label": "Main" },
    links.map(([href, label]) => el("a", { href, "aria-current": current === href ? "page" : null }, label)));
  const right = el("div.topbar-right");
  const themeBtn = el("button.btn.ghost.small", { type: "button", "aria-label": "Toggle theme", onclick: toggleTheme },
    document.documentElement.dataset.theme === "light" ? "Dark" : "Light");
  right.append(themeBtn);
  bar.replaceChildren(el("a.brand", { href: "/" }, "clash", el("span", "forces")), nav, right);
  const me = await getMe();
  if (me.user) {
    right.prepend(el("a.btn.ghost.small", { href: `/u/${encodeURIComponent(me.user.handle || "")}` },
      handleEl(me.user.handle, me.user.cf_rating, { link: false })));
    right.append(el("a.btn.ghost.small", { href: "/settings", "aria-label": "Settings" }, "Settings"));
  } else {
    right.append(el("a.btn.small", { href: "/login" }, "Log in"));
  }
  if (me.user && me.user.needs_reverify) {
    toast("Your Codeforces handle changed. Re-verify it in Settings.", true);
  }
}

export function toggleTheme() {
  const root = document.documentElement;
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch { /* private mode */ }
  mountTopbar(location.pathname);
}

export function fmtDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}
