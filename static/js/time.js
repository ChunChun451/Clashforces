// Dates and countdowns for scheduled contests. Times are shown in the viewer's own time zone.

// "Fri 13 Nov, 19:00"
export function fmtLocal(ts, { weekday = true, year = false } = {}) {
  const d = new Date(ts * 1000);
  const date = d.toLocaleDateString(undefined, { weekday: weekday ? "short" : undefined, day: "numeric", month: "short", year: year ? "numeric" : undefined });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${time}`;
}

// "UTC+5:30"
export function tzLabel(ts = Date.now() / 1000) {
  const off = -new Date(ts * 1000).getTimezoneOffset();
  const sign = off >= 0 ? "+" : "−";
  const h = Math.floor(Math.abs(off) / 60), m = Math.abs(off) % 60;
  return `UTC${sign}${h}${m ? `:${String(m).padStart(2, "0")}` : ""}`;
}

// 2d 4h · 3h 12m · 12:05
export function fmtCountdown(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

// 180 -> "3h", 90 -> "1h 30m", 45 -> "45m"
export function fmtMinutes(min) {
  const h = Math.floor(min / 60), m = min % 60;
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}

// "<input type=datetime-local>" value for a unix time, in local time
export function toLocalInput(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value) {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}
