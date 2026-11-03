"""Team chat: a private channel per team in team matches and team contests.

Only the members of a team read and write its chat; the host, spectators, big screens
and other teams never get it. A player moved to another team sees that team's chat from
then on. Messages are plain text, at most CHAT_MAX characters, and rate limited per user.
The chat is open in the lobby and during the match, and read-only once it is over.
"""

from collections import defaultdict, deque

from app import clock, config, db
from app.matches import store
from app.matches.service import MatchError
from app.ws.hub import hub

OPEN_STATUSES = ("lobby", "starting", "running")

_sent: dict[int, deque] = defaultdict(deque)  # user_id -> times of their recent messages


def my_team(match, user_id: int | None) -> int | None:
    """The viewer's team in a team match, or None (not a team match, not a player, or not on a team yet)."""
    if user_id is None or not store.is_team(match):
        return None
    t = db.scalar("SELECT team FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], user_id))
    return t if t is not None and t >= 0 else None


def is_open(match) -> bool:
    return match["status"] in OPEN_STATUSES


def _message(row) -> dict:
    return {"id": row["id"], "team": row["team_idx"], "user_id": row["user_id"], "handle": row["handle"],
            "body": row["body"], "created_at": row["created_at"]}


def history(match, user_id: int | None) -> dict:
    """The last CHAT_HISTORY messages of the viewer's own team, oldest first."""
    team = my_team(match, user_id)
    if team is None:
        raise MatchError("Team chat is only for players on a team.")
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
    team = my_team(match, user["id"] if user else None)
    if team is None:
        raise MatchError("Team chat is only for players on a team.")
    if not is_open(match):
        raise MatchError("The match is over, so the chat is closed.")
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
    members = {p["user_id"] for p in store.players(match["id"]) if p["team"] == team}
    # per viewer: only this team's members get it (big screens count as spectators)
    hub.send_match(match["code"], "chat.message", lambda uid: {"code": match["code"], **msg} if uid in members else None)
    return msg
