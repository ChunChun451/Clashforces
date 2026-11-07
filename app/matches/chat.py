"""Match chat: a private channel per team in team matches and team contests, and one
shared channel (ROOM) for the players of a friend duel or group room.

Only the members of a channel read and write it; the host, spectators, big screens
and other teams never get it. A player moved to another team sees that team's chat from
then on. Ranked matches and solo contests have no chat. Messages are plain text, at most CHAT_MAX characters, and rate limited per user.
The chat is open in the lobby, during the match and after it (to talk it over);
only a cancelled match's chat is read-only.
"""

from collections import defaultdict, deque

from app import clock, config, db
from app.matches import store
from app.matches.service import MatchError
from app.ws.hub import hub

OPEN_STATUSES = ("lobby", "starting", "running", "ended")  # stays open for the post-match talk
ROOM = -1                          # the shared channel of a friend duel or group room (team_idx in team_messages)
ROOM_MODES = ("friend", "group")

_sent: dict[int, deque] = defaultdict(deque)  # user_id -> times of their recent messages


def my_team(match, user_id: int | None) -> int | None:
    """The viewer's team in a team match, or None (not a team match, not a player, or not on a team yet)."""
    if user_id is None or not store.is_team(match):
        return None
    t = db.scalar("SELECT team FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], user_id))
    return t if t is not None and t >= 0 else None


def my_channel(match, user_id: int | None) -> int | None:
    """The chat the viewer belongs to: their team, ROOM for a friend duel or group room, or None."""
    if user_id is None:
        return None
    if store.is_team(match):
        return my_team(match, user_id)
    if match["mode"] in ROOM_MODES and db.one("SELECT 1 FROM match_players WHERE match_id = ? AND user_id = ?",
                                               (match["id"], user_id)):
        return ROOM
    return None


def is_open(match) -> bool:
    return match["status"] in OPEN_STATUSES


def _message(row) -> dict:
    return {"id": row["id"], "team": row["team_idx"], "user_id": row["user_id"], "handle": row["handle"],
            "body": row["body"], "created_at": row["created_at"]}


def history(match, user_id: int | None) -> dict:
    """The last CHAT_HISTORY messages of the viewer's own channel, oldest first."""
    team = my_channel(match, user_id)
    if team is None:
        raise MatchError("The chat is only for players in this match.")
    rows = db.all(
        "SELECT t.*, u.handle FROM (SELECT * FROM team_messages WHERE match_id = ? AND team_idx = ?"
        " ORDER BY id DESC LIMIT ?) t LEFT JOIN users u ON u.id = t.user_id ORDER BY t.id",
        (match["id"], team, config.CHAT_HISTORY))
    return {"code": match["code"], "team": team, "open": is_open(match), "messages": [_message(r) for r in rows]}


def clean(body) -> str:
    text = "".join(ch for ch in str(body or "") if ch == "\n" or ch.isprintable()).strip()
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _rate_limit(user_id: int, now: int) -> None:
    sent = _sent[user_id]
    while sent and sent[0] <= now - 60:
        sent.popleft()
    if sent and now - sent[-1] < config.CHAT_MIN_GAP_S:
        raise MatchError("Slow down: one message per second.")
    if len(sent) >= config.CHAT_PER_MIN:
        raise MatchError(f"Slow down: at most {config.CHAT_PER_MIN} messages a minute.")
    sent.append(now)


def post(match, user, body) -> dict:
    team = my_channel(match, user["id"] if user else None)
    if team is None:
        raise MatchError("The chat is only for players in this match.")
    if not is_open(match):
        raise MatchError("This match was cancelled, so the chat is closed.")
    text = clean(body)
    if not text:
        raise MatchError("Write something first.")
    if len(text) > config.CHAT_MAX:
        raise MatchError(f"Messages are at most {config.CHAT_MAX} characters.")
    now = clock.now()
    _rate_limit(user["id"], now)
    cur = db.execute("INSERT INTO team_messages(match_id, team_idx, user_id, body, created_at) VALUES (?,?,?,?,?)",
                     (match["id"], team, user["id"], text, now))
    msg = {"id": cur.lastrowid, "team": team, "user_id": user["id"], "handle": user["handle"], "body": text,
           "created_at": now}
    members = {p["user_id"] for p in store.players(match["id"]) if team == ROOM or p["team"] == team}
    # per viewer: only this team's members get it (big screens count as spectators)
    hub.send_match(match["code"], "chat.message", lambda uid: {"code": match["code"], **msg} if uid in members else None)
    return msg
