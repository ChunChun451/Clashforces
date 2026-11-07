// Match chat: a drawer on the match page. In team matches each team has its own; friend duels
// and group rooms have one shared by the players (ROOM). Spectators never see it.
// Lives outside #app so the page's re-renders never touch it (or what you're typing).
import { el, ratingClass } from "./api.js";
import { on, send } from "./ws.js";
import { teamBadge, teamClass } from "./standings.js";
import { icon } from "./icons.js";

const OPEN = ["lobby", "starting", "running", "ended"];  // open after the match too, for the post-match talk
const ROOM = -1;                       // the shared channel of a friend duel or group room
const ROOM_MODES = ["friend", "group"];
const MAX = 500;

const PHASE = { lobby: "Lobby", starting: "Starting", running: "Live", ended: "After the match", cancelled: "Cancelled" };

export function teamChat(code, myId) {
  const storeKey = `chat-open:${code}`;
  let expanded = false;
  try { expanded = sessionStorage.getItem(storeKey) === "1"; } catch { /* storage blocked */ }
  let team = null;          // my team index, from match.state
  let name = "";
  let writable = false;
  let messages = [];
  let unread = 0;
  let error = "";
  let status = "lobby";
  let ratings = {};        // handle -> CF rating, for colouring names

  const dock = el("aside.chat-dock", { "aria-label": "Chat" });
  const list = el("ol.chat-list", { "aria-live": "polite" });
  const input = el("input", { type: "text", id: "chat-input", maxlength: MAX, autocomplete: "off",
    placeholder: "Message your team", "aria-label": "Message your team" });
  const count = el("span.chat-count.mono");
  const err = el("p.chat-error", { role: "alert" });
  const sendBody = (body) => {
    send("chat.send", { code, body });
    error = "";
    err.hidden = true;
  };
  const form = el("form.chat-form", { onsubmit: (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    sendBody(body);
    input.value = "";
    drawCount();
  } }, input, el("button.chat-send", { type: "submit", "aria-label": "Send", title: "Send" }, "\u2191"));
  input.addEventListener("input", drawCount);

  function setExpanded(v) {
    expanded = v;
    if (v) unread = 0;
    try { sessionStorage.setItem(storeKey, v ? "1" : "0"); } catch { /* ignore */ }
    draw();
    if (v) { scrollDown(true); if (writable && matchMedia("(min-width: 721px)").matches) input.focus(); }
  }

  function scrollDown(force = false) {
    const near = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    if (force || near) list.scrollTop = list.scrollHeight;
  }

  function drawCount() {
    const n = input.value.length;
    count.textContent = n > MAX - 100 ? `${MAX - n}` : "";
  }

  function drawList() {
    const near = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    if (!messages.length) {
      const who = team === ROOM ? "Only players in this match can read it." : "Only your team can read it.";
      const hint = status === "ended" ? "Talk the match over." : status === "running" ? "Keep it short, the clock is running."
        : "Say hi before the start.";
      list.replaceChildren(el("li.chat-empty", el("div.chat-empty-icon", icon("message", 20)), el("strong", hint), el("span", who)));
      return;
    }
    let prev = null;
    list.replaceChildren(...messages.map((m, i) => {
      const mine = m.user_id === myId;
      const same = prev && prev.user_id === m.user_id && m.created_at - prev.created_at < 120;
      const next = messages[i + 1];
      const last = !next || next.user_id !== m.user_id || next.created_at - m.created_at >= 120;
      prev = m;
      const time = new Date(m.created_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      const handle = m.handle || "someone";
      const color = ratingClass(ratings[handle]);
      return el("li", { class: `chat-msg${mine ? " mine" : ""}${same ? " cont" : ""}${last ? " last" : ""}` },
        mine ? null : el("span", { class: `chat-avatar ${color}${last ? "" : " ghost"}`, "aria-hidden": "true" },
          last ? handle.slice(0, 1).toUpperCase() : ""),
        el("div.chat-stack",
          same || mine ? null : el("span", { class: `chat-who ${color}` }, handle),
          el("p.chat-bubble", { title: time }, m.body),
          last ? el("span.chat-time", time) : null));
    }));
    if (near) list.scrollTop = list.scrollHeight;
  }

  let built = "";
  function draw() {
    if (team === null) { dock.remove(); built = ""; return; }
    if (!dock.isConnected) document.body.append(dock);
    // rebuild only when something visible changed, so the input keeps its focus
    const sig = `${team}|${name}|${writable}|${expanded}|${status}|${expanded ? "" : unread}`;
    if (sig === built) { if (expanded) drawList(); return; }
    built = sig;
    const room = team === ROOM;
    const label = room ? "Chat" : "Team chat";
    dock.className = `chat-dock ${room ? "room" : teamClass(team)}${expanded ? " open" : ""}`;
    if (!expanded) {
      dock.replaceChildren(el("button.btn.chat-toggle", { type: "button", onclick: () => setExpanded(true),
        "aria-label": `Open ${label.toLowerCase()}${unread ? ` (${unread} new)` : ""}` },
      icon("message", 16), label, unread ? el("span.chat-unread", { "aria-hidden": "true" }, unread > 9 ? "9+" : String(unread)) : null));
      return;
    }
    input.placeholder = room ? "Message the other players" : "Message your team";
    input.setAttribute("aria-label", input.placeholder);
    err.textContent = error;
    err.hidden = !error;
    dock.replaceChildren(el("section.panel.chat-panel", { "aria-label": room ? "Chat" : `${name} chat` },
      el("div.panel-head.chat-head",
        el("div.chat-head-main",
          room ? el("h2", el("span.chat-title", "Chat"))
            : el("h2.team-name", teamBadge(team, name), el("span.chat-title", name)),
          el("span", { class: `chat-phase ${status}` }, el("span.chat-phase-dot", { "aria-hidden": "true" }), PHASE[status] || "",
            el("span.muted", room ? " \u00b7 players only" : " \u00b7 your team only"))),
        el("button.btn.ghost.small.icon-sm", { type: "button", "aria-label": `Close ${label.toLowerCase()}`, title: "Close", onclick: () => setExpanded(false) }, "\u00d7")),
      list,
      writable
        ? el("div.chat-compose", form, el("div.chat-hint", err, count))
        : el("p.panel-foot", "This match was cancelled, so the chat is read-only.")));
    drawList();
    drawCount();
  }

  on("chat.history", (d) => {
    if (d.code !== code) return;
    team = d.team;
    messages = d.messages || [];
    writable = !!d.open;
    draw();
    scrollDown(true);
  });
  on("chat.message", (d) => {
    if (d.code !== code || d.team !== team) return;
    if (messages.some((m) => m.id === d.id)) return;
    messages.push(d);
    if (messages.length > 200) messages = messages.slice(-200);
    if (!expanded && d.user_id !== myId) { unread += 1; draw(); return; }
    if (expanded) drawList();
  });
  on("chat.error", (d) => {
    if (d.code !== code) return;
    error = d.message;
    if (expanded) { err.textContent = error; err.hidden = false; }
  });

  // Called with every match.state: follows team moves and the end of the match.
  function update(M) {
    let mine = M && M.teams && M.me ? M.my_team : null;
    if (M && M.me && !M.teams && ROOM_MODES.includes(M.mode)) mine = ROOM;
    if (mine === null || mine === undefined) { team = null; draw(); return; }
    if (team === null && mine === ROOM && !messages.length) send("chat.history", { code });  // just joined the lobby
    if (team !== null && mine !== team) {
      messages = [];          // moved to another team: only that team's chat from now on
      send("chat.history", { code });
    }
    team = mine;
    name = mine === ROOM ? "Chat" : M.teams[mine] ? M.teams[mine].name : `Team ${"ABCDEF"[mine]}`;
    writable = OPEN.includes(M.status);
    status = M.status;
    ratings = Object.fromEntries((M.players || []).map((p) => [p.handle, p.cf_rating]));
    draw();
  }

  return { update };
}
