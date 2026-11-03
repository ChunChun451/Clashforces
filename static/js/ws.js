// One reconnecting WebSocket per tab. Handlers are registered per message type.

const handlers = new Map();
const subscriptions = new Set();
let socket = null;
let backoff = 500;
let serverOffset = 0; // server_time - local time, in seconds
let socketQuery = "";  // "?view=screen" on the big screen: always the spectator view

export function screenView() { socketQuery = "?view=screen"; }

export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, []);
  handlers.get(type).push(fn);
}

export function send(type, data = {}) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type, data }));
}

export function subscribe(code) {
  subscriptions.add(code);
  send("subscribe", { code });
}

export function serverNow() {
  return Date.now() / 1000 + serverOffset;
}

export function syncClock(serverTime) {
  if (typeof serverTime === "number") serverOffset = serverTime - Date.now() / 1000;
}

function dispatch(msg) {
  for (const fn of handlers.get(msg.type) || []) {
    try { fn(msg.data || {}); } catch (e) { console.error(e); }
  }
  for (const fn of handlers.get("*") || []) {
    try { fn(msg); } catch (e) { console.error(e); }
  }
}

export function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws${socketQuery}`);
  socket.addEventListener("open", () => {
    backoff = 500;
    for (const code of subscriptions) send("subscribe", { code });
    dispatch({ type: "socket.open", data: {} });
  });
  socket.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "hello" || msg.type === "pong") syncClock(msg.data.server_time);
    dispatch(msg);
  });
  socket.addEventListener("close", () => {
    dispatch({ type: "socket.closed", data: {} });
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 15000);
  });
}

setInterval(() => send("ping"), 25000);
