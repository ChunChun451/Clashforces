// Browser notifications. Opt-in from Settings; only shown while this tab is hidden.

const KEY = "notify";

export function enabled() {
  try { return localStorage.getItem(KEY) === "1" && "Notification" in window && Notification.permission === "granted"; }
  catch { return false; }
}

export async function enable() {
  if (!("Notification" in window)) return false;
  const p = await Notification.requestPermission();
  const ok = p === "granted";
  try { localStorage.setItem(KEY, ok ? "1" : "0"); } catch { /* ignore */ }
  return ok;
}

export function disable() {
  try { localStorage.setItem(KEY, "0"); } catch { /* ignore */ }
}

const last = new Map();
export function notify(text, tag = "match", { always = false } = {}) {
  if (!enabled()) return;
  if (!always && !document.hidden) return;
  const now = Date.now();
  if (now - (last.get(text) || 0) < 3000) return; // same message twice in a row
  last.set(text, now);
  try { new Notification("Clashforces", { body: text, tag }); } catch { /* some browsers need a service worker */ }
}
