// Team chat: a private drawer per team on the match page. Only your team sees it.
// Lives outside #app so the page's re-renders never touch it (or what you're typing).
import { el } from "./api.js";
import { on, send } from "./ws.js";
import { teamBadge, teamClass } from "./standings.js";
import { icon } from "./icons.js";

const OPEN = ["lobby", "starting", "running"];
const MAX = 500;

export function teamChat(code, myId) {
  const storeKey = `chat-open:${code}`;
  let expanded = false;
  try { expanded = sessionStorage.getItem(storeKey) === "1"; } catch { /* storage blocked */ }
  let team = null;          // my team index, from match.state
  let name = "";
  let writable = false;
  let messages = [];
  let unread = false;
  let error = "";

  const dock = el("aside.chat-dock", { "aria-label": "Team chat" });
  const list = el("ol.chat-list", { "aria-live": "polite" });
  const input = el("input", { type: "text", id: "chat-input", maxlength: MAX, autocomplete: "off",
    placeholder: "Message your team", "aria-label": "Message your team" });
  const count = el("span.chat-count.mono");
  const err = el("p.chat-error", { role: "alert" });
  const form = el("form.chat-form", { onsubmit: (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    send("chat.send", { code, body });
    input.value = "";
    error = "";
    drawCount();
  } }, input, el("button.btn.small.primary", { type: "submit" }, "Send"));
  input.addEventListener("input", drawCount);

  function setExpanded(v) {
    expanded = v;
    if (v) unread = false;
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
      list.replaceChildren(el("li.chat-empty", el("strong", "No messages yet"), el("span", "Only your team can read this chat.")));
      return;
    }
    let prev = null;
    list.replaceChildren(...messages.map((m) => {
      const same = prev && prev.user_id === m.user_id && m.created_at - prev.created_at < 120;
      prev = m;
      const time = new Date(m.created_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      return el("li", { class: `chat-msg${m.user_id === myId ? " mine" : ""}${same ? " cont" : ""}` },
        same ? null : el("div.chat-meta", el("span.chat-who", m.handle || "someone"), m.user_id === myId ? el("span.chip.you", "you") : null,
          el("span.chat-time", time)),
        el("p.chat-body", m.body));
    }));
    if (near) list.scrollTop = list.scrollHeight;
  }

  let built = "";
  function draw() {
    if (team === null) { dock.remove(); built = ""; return; }
    if (!dock.isConnected) document.body.append(dock);
    // rebuild only when something visible changed, so the input keeps its focus
    const sig = `${team}|${name}|${writable}|${expanded}|${expanded ? "" : unread}`;
    if (sig === built) { if (expanded) drawList(); return; }
    built = sig;
    dock.className = `chat-dock ${teamClass(team)}${expanded ? " open" : ""}`;
    if (!expanded) {
      dock.replaceChildren(el("button.btn.chat-toggle", { type: "button", onclick: () => setExpanded(true),
        "aria-label": `Open team chat${unread ? " (new messages)" : ""}` },
      icon("message", 16), "Team chat", unread ? el("span.chat-dot", { "aria-hidden": "true" }) : null));
      return;
    }
    err.textContent = error;
    err.hidden = !error;
    dock.replaceChildren(el("section.panel.chat-panel", { "aria-label": `${name} chat` },
      el("div.panel-head",
        el("h2.team-name", teamBadge(team, name), el("span.chat-title", name), el("span.muted.chat-sub", "team chat")),
        el("button.btn.ghost.small.icon-sm", { type: "button", "aria-label": "Close team chat", title: "Close", onclick: () => setExpanded(false) }, "×")),
      list,
      writable
        ? el("div.chat-compose", form, el("div.chat-hint", err, count))
        : el("p.panel-foot", "The match is over, so the chat is read-only.")));
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
    if (!expanded && d.user_id !== myId) { unread = true; draw(); return; }
    if (expanded) drawList();
  });
  on("chat.error", (d) => {
    if (d.code !== code) return;
    error = d.message;
    if (expanded) { err.textContent = error; err.hidden = false; }
  });

  // Called with every match.state: follows team moves and the end of the match.
  function update(M) {
    const mine = M && M.teams && M.me ? M.my_team : null;
    if (mine === null || mine === undefined) { team = null; draw(); return; }
    if (team !== null && mine !== team) {
      messages = [];          // moved to another team: only that team's chat from now on
      send("chat.history", { code });
    }
    team = mine;
    name = M.teams[mine] ? M.teams[mine].name : `Team ${"ABCDEF"[mine]}`;
    writable = OPEN.includes(M.status);
    draw();
  }

  return { update };
}
