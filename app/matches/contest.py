"""Hosted contests: one host sets problems and a start time; players (solo or in teams) join.

The host never plays: they are not in match_players, never polled, and never
on the scoreboard. Problems stay in the private "contest_problems" setting and
are only copied into match_problems when the contest starts, so nothing the
players or spectators can reach shows them early.

Start: the lifecycle ticker calls tick() every second. A few minutes before the
scheduled time it re-syncs any missing histories and refreshes handles in one
batched user.info call; at the scheduled time it starts the contest with
whoever is seated (or the host presses Start now / Start anyway).
"""

import json
import logging

from app import clock, config, db
from app.auth import handles, users
from app.cf import cache
from app.cf.client import CFError, Lane
from app.core import custom_list, selection
from app.core import team as team_core
from app.matches import events, locks, store
from app.matches import settings as settings_mod
from app.matches.service import MatchError, _begin, _spawn, _team_conf, new_code, start_blocker
from app.poller import history, ingest
from app.poller.poller import poller
from app.ws.hub import hub

log = logging.getLogger("contest")

_retry_at: dict[int, int] = {}     # match_id -> next auto-start attempt
_prefetched: set[int] = set()      # contests whose pre-start sync already ran
_starting: set[int] = set()


# ---------- settings ----------

def _int(raw: dict, key: str, default, what: str) -> int:
    try:
        return int(raw.get(key, default) if raw.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        raise MatchError(f"{what} must be a number.")


def resolve_problems(text: str, points=None) -> list[dict]:
    """Problem IDs or URLs -> [{"key", "label", "points"}], in the given order (A, B, C, ...)."""
    by_key = {p.key: p for p in cache.problems()}
    if not by_key:
        raise MatchError("The problem list is still loading. Try again in a minute.")
    res = custom_list.check(text or "", by_key, cache.running_contest_ids(), {}, config.CONTEST_PROBLEMS_MAX)
    if res.errors:
        raise MatchError(res.errors[0])
    n = len(res.problems)
    default = selection.lockout_points(n)
    pts = list(points or [])
    out = []
    for i, (label, p) in enumerate(zip(selection.labels(n), res.problems)):
        value = default[i]
        if i < len(pts) and pts[i] not in (None, ""):
            try:
                value = int(pts[i])
            except (TypeError, ValueError):
                raise MatchError("Points must be whole numbers.")
            if not 1 <= value <= 10000:
                raise MatchError("Points must be 1–10000.")
        out.append({"key": p.key, "label": label, "points": value})
    return out


def clean(raw: dict, now: int) -> tuple[dict, str, int | None]:
    """Validate a contest form. Returns (settings, visibility, scheduled_at)."""
    rules = raw.get("rules", "icpc")
    if rules not in ("icpc", "lockout"):
        raise MatchError("Rules must be ICPC or Lockout.")
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        raise MatchError("Give the contest a title.")
    if len(title) > config.CONTEST_TITLE_MAX:
        raise MatchError(f"The title is at most {config.CONTEST_TITLE_MAX} characters.")
    description = str(raw.get("description") or "").strip()[:280]
    fmt = raw.get("format", "solo")
    if fmt not in ("solo", "teams"):
        raise MatchError("Format must be solo or teams.")
    duration = _int(raw, "duration_min", 120, "Duration")
    if not config.DURATION_MIN <= duration <= config.DURATION_MAX:
        raise MatchError(f"Duration must be {config.DURATION_MIN}–{config.DURATION_MAX} minutes.")
    freeze = _int(raw, "freeze_min", 0, "Freeze") if rules == "icpc" else 0
    if not 0 <= freeze < duration:
        raise MatchError("The freeze must be shorter than the contest.")
    visibility = raw.get("visibility", "public")
    if visibility not in ("public", "unlisted"):
        raise MatchError("Visibility is public or unlisted.")
    scheduled_at = raw.get("scheduled_at")
    if scheduled_at in (None, ""):
        scheduled_at = None
    else:
        scheduled_at = _int(raw, "scheduled_at", None, "Start time")
        if scheduled_at < now - 60:
            raise MatchError("The start time is in the past.")
        if scheduled_at > now + config.CONTEST_SCHEDULE_MAX_DAYS * 86400:
            raise MatchError(f"Schedule the start at most {config.CONTEST_SCHEDULE_MAX_DAYS} days ahead.")
    problems = resolve_problems(raw.get("problems", ""), raw.get("points"))
    s = {
        "title": title, "description": description, "format": fmt, "duration_min": duration, "freeze_min": freeze,
        "count": len(problems), "contest_problems": problems,
        # the usual keys, so shared code (rematch, filters, views) finds what it expects
        "rating_min": config.RATING_MIN, "rating_max": config.RATING_MAX, "tags_include": [], "tags_avoid": [],
        "avoid_interactive": False, "custom_problems": None, "handicap": None, "exclude": [],
    }
    if fmt == "teams":
        try:
            s.update(settings_mod.clean_teams(raw))
        except settings_mod.SettingsError as e:
            raise MatchError(str(e))
        s["draft"] = None
    else:
        s["max_players"] = _int(raw, "max_players", config.CONTEST_MAX_PLAYERS, "Max players")
        if not 2 <= s["max_players"] <= config.CONTEST_MAX_PLAYERS:
            raise MatchError(f"A solo contest has 2–{config.CONTEST_MAX_PLAYERS} players.")
    return s, visibility, scheduled_at


def problem_rows(s: dict) -> list[tuple]:
    return [(None, p["label"], p["key"], p["points"]) for p in s.get("contest_problems", [])]


# ---------- create / edit ----------

def create(user, raw: dict) -> str:
    if user["handle"] is None or user["needs_reverify"]:
        raise MatchError("Your Codeforces handle changed. Re-verify it in Settings first.")
    now = clock.now()
    s, visibility, scheduled_at = clean(raw, now)
    names = raw.get("team_names") or []
    code = new_code()
    with db.transaction():
        cur = db.execute(
            "INSERT INTO matches(code, mode, rules, status, settings, creator_id, created_at, scheduled_at, visibility)"
            " VALUES (?, 'contest', ?, 'lobby', ?, ?, ?, ?, ?)",
            (code, raw.get("rules", "icpc"), json.dumps(s), user["id"], now, scheduled_at, visibility))
        if s["format"] == "teams":
            _store_team_names(cur.lastrowid, s["team_count"], names)
    return code


def _store_team_names(match_id: int, count: int, names: list) -> None:
    try:
        clean_names = [team_core.clean_name(names[i] if i < len(names) else "", i) for i in range(count)]
        team_core.check_unique(clean_names)
    except team_core.NameError_ as e:
        raise MatchError(str(e))
    store.ensure_teams(match_id, count)
    for i, name in enumerate(clean_names):
        db.execute("UPDATE match_teams SET name = ? WHERE match_id = ? AND idx = ?", (name, match_id, i))


def _host_lobby(user, code: str, statuses=("lobby",)):
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if match["mode"] != "contest":
        raise MatchError("That isn't a hosted contest.")
    if user is None or match["creator_id"] != user["id"]:
        raise MatchError("Only the host can do that.")
    if match["status"] not in statuses:
        raise MatchError({"lobby": "The contest has already started.",
                          "running": "The contest isn't running."}.get(statuses[0], "Not possible right now."))
    return match


def update(user, code: str, raw: dict) -> None:
    """The host edits a contest before it starts. Players stay; teams are kept where they still fit."""
    match = _host_lobby(user, code)
    old = store.settings(match)
    s, visibility, scheduled_at = clean(raw, clock.now())
    players = store.players(match["id"])
    if s["format"] != old.get("format"):
        if players:
            raise MatchError("Players have joined, so the format can't change.")
    if s["format"] == "teams":
        if len(players) > s["team_count"] * s["team_size"]:
            raise MatchError(f"{len(players)} players have joined; {s['team_count']} teams of {s['team_size']} "
                             "don't fit them all.")
        counts = [0] * s["team_count"]
        for p in players:
            if p["team"] is not None and p["team"] < s["team_count"]:
                counts[p["team"]] += 1
        if any(c > s["team_size"] for c in counts):
            raise MatchError(f"A team already has more than {s['team_size']} players. Move someone first.")
        s["draft"] = old.get("draft") if old.get("fill") == s["fill"] == "draft" else None
    elif len(players) > s["max_players"]:
        raise MatchError(f"{len(players)} players have joined already.")
    with db.transaction():
        db.execute("UPDATE matches SET settings = ?, rules = ?, scheduled_at = ?, visibility = ? WHERE id = ?",
                   (json.dumps(s), raw.get("rules", "icpc"), scheduled_at, visibility, match["id"]))
        if s["format"] == "teams":
            names = raw.get("team_names")
            if names is None:
                names = store.team_names(match)[:s["team_count"]]
            _store_team_names(match["id"], s["team_count"], names)
            db.execute("UPDATE match_players SET team = NULL WHERE match_id = ? AND team >= ?",
                       (match["id"], s["team_count"]))
    _retry_at.pop(match["id"], None)
    _prefetched.discard(match["id"])
    events.state(match["id"])


def editable(user, code: str) -> dict:
    """Everything the edit form needs (host only, before the start)."""
    match = _host_lobby(user, code)
    s = store.settings(match)
    return {
        "title": s["title"], "description": s.get("description", ""), "format": s["format"], "rules": match["rules"],
        "duration_min": s["duration_min"], "freeze_min": s.get("freeze_min", 0),
        "max_players": s.get("max_players"), "team_count": s.get("team_count", 2), "team_size": s.get("team_size", 2),
        "fill": s.get("fill", "free"), "uneven_ok": s.get("uneven_ok", False),
        "visibility": match["visibility"], "scheduled_at": match["scheduled_at"],
        "problems": " ".join(p["key"] for p in s["contest_problems"]),
        "points": [p["points"] for p in s["contest_problems"]],
        "team_names": store.team_names(match) if s["format"] == "teams" else [],
    }


# ---------- what the host sees ----------

def host_view(match) -> dict:
    """Problems and warnings for the host only. Never part of a player's or spectator's view."""
    s = store.settings(match)
    players = store.players(match["id"])
    touched = {p["user_id"]: ingest.touched_groups(p["user_id"]) for p in players}
    probs = []
    for cp in s.get("contest_problems", []):
        p = cache.get(cp["key"])
        group = p.group if p else cp["key"]
        tried = [pl["current_handle"] for pl in players if group in touched[pl["user_id"]]]
        probs.append({"label": cp["label"], "key": cp["key"], "points": cp["points"],
                      "name": p.name if p else cp["key"], "rating": p.rating if p else None,
                      "url": f"https://codeforces.com/contest/{p.contest_id}/problem/{p.index}" if p else None,
                      "tried_by": tried})
    warnings = [f"{len(x['tried_by'])} {'player has' if len(x['tried_by']) == 1 else 'players have'} already tried "
                f"{x['key']} ({', '.join(x['tried_by'][:5])}{'…' if len(x['tried_by']) > 5 else ''})"
                for x in probs if x["tried_by"]]
    unsynced = [p["current_handle"] for p in players
                if not db.scalar("SELECT history_synced FROM users WHERE id = ?", (p["user_id"],))]
    kicked = [{"user_id": r["user_id"], "handle": r["handle"]} for r in db.all(
        "SELECT k.user_id, u.handle FROM match_kicks k JOIN users u ON u.id = k.user_id WHERE k.match_id = ?"
        " ORDER BY k.created_at", (match["id"],))]
    return {"problems": probs, "warnings": warnings, "unsynced": unsynced, "kicked": kicked}


def public_info(match) -> dict:
    s = store.settings(match)
    host = users.by_id(match["creator_id"]) if match["creator_id"] else None
    return {
        "title": s.get("title"), "description": s.get("description", ""), "format": s.get("format", "solo"),
        "scheduled_at": match["scheduled_at"], "visibility": match["visibility"],
        "problem_count": s.get("count", 0), "duration_min": s.get("duration_min"),
        "max_players": service_max_players(match),
        "host": {"user_id": host["id"], "handle": host["handle"], "cf_rating": host["cf_rating"]} if host else None,
        "start_note": s.get("start_note"),
    }


def service_max_players(match) -> int:
    from app.matches.service import max_players
    return max_players(match)


# ---------- announcements ----------

def announcements(match_id: int, limit: int = 50) -> list[dict]:
    rows = db.all("SELECT a.id, a.body, a.created_at, u.handle FROM announcements a LEFT JOIN users u ON u.id = a.author_id"
                  " WHERE a.match_id = ? ORDER BY a.id DESC LIMIT ?", (match_id, limit))
    return [dict(r) for r in rows]


def _post(match, author_id: int | None, body: str) -> dict:
    now = clock.now()
    cur = db.execute("INSERT INTO announcements(match_id, author_id, body, created_at) VALUES (?,?,?,?)",
                     (match["id"], author_id, body, now))
    handle = db.scalar("SELECT handle FROM users WHERE id = ?", (author_id,)) if author_id else None
    item = {"id": cur.lastrowid, "body": body, "created_at": now, "handle": handle}
    hub.send_match(match["code"], "match.announcement", item)
    return item


def announce(user, code: str, body: str) -> dict:
    match = _host_lobby(user, code, ("lobby", "starting", "running", "ended"))
    text = "".join(ch for ch in str(body or "") if ch == "\n" or ch.isprintable()).strip()
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not text:
        raise MatchError("Write something first.")
    if len(text) > config.ANNOUNCEMENT_MAX:
        raise MatchError(f"Announcements are at most {config.ANNOUNCEMENT_MAX} characters.")
    return _post(match, user["id"], text)


# ---------- host controls ----------

def kick(user, code: str, player_id: int) -> None:
    match = _host_lobby(user, code)
    if not db.one("SELECT 1 FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], player_id)):
        raise MatchError("That player is not in this contest.")
    with db.transaction():
        db.execute("DELETE FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], player_id))
        db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ? AND captain_id = ?",
                   (match["id"], player_id))
        db.execute("INSERT OR IGNORE INTO match_kicks(match_id, user_id, created_at) VALUES (?,?,?)",
                   (match["id"], player_id, clock.now()))
        locks.release(player_id, match["id"])
    hub.send_user(player_id, "match.kicked", {"code": match["code"], "title": store.settings(match).get("title")})
    events.state(match["id"])


def allow(user, code: str, player_id: int) -> None:
    match = _host_lobby(user, code)
    db.execute("DELETE FROM match_kicks WHERE match_id = ? AND user_id = ?", (match["id"], player_id))
    events.state(match["id"])


def extend(user, code: str, minutes: int) -> int:
    """More time for everyone still playing. Returns the new end time."""
    from app.matches import lifecycle
    match = _host_lobby(user, code, ("running",))
    if not 1 <= minutes <= config.EXTEND_MAX_MIN:
        raise MatchError(f"Extend by 1–{config.EXTEND_MAX_MIN} minutes.")
    delta = minutes * 60
    now = clock.now()
    s = store.settings(match)
    with db.transaction():
        db.execute("UPDATE matches SET end_time = end_time + ? WHERE id = ?", (delta, match["id"]))
        db.execute("UPDATE match_players SET end_time = end_time + ? WHERE match_id = ? AND forfeited_at IS NULL"
                   " AND end_time > ?", (delta, match["id"], now))
        fs = s.get("freeze_at")
        if fs is not None and fs > now:
            s["freeze_at"] = fs + delta  # a freeze that already began stays on
            db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))
    match = store.match_by_id(match["id"])
    for p in store.players(match["id"]):
        if p["forfeited_at"] is None and p["end_time"] > now:
            poller.watch(p["user_id"], p["handle"], match["start_time"], p["end_time"])
            lifecycle._sent.discard(("warn", match["id"], p["user_id"]))
    _post(match, user["id"], f"Time extended by {minutes} {'minute' if minutes == 1 else 'minutes'}.")
    events.state(match["id"])
    return match["end_time"]


def end_early(user, code: str) -> None:
    from app.matches import lifecycle
    match = _host_lobby(user, code, ("running",))
    lifecycle.end_match(match["id"], "host", at=clock.now())


def cancel(user, code: str) -> None:
    match = _host_lobby(user, code)
    db.execute("UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ?",
               (clock.now(), match["id"]))
    locks.release_match(match["id"])
    events.state(match["id"])


def start_now(user, code: str, force: bool = False) -> None:
    match = _host_lobby(user, code)
    _spawn(start(match["id"], force=force))


# ---------- starting ----------

def _note(match, text: str | None) -> None:
    s = store.settings(match)
    if s.get("start_note") == text:
        return
    s["start_note"] = text
    db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))


def _back(match, message: str, retry: bool) -> bool:
    db.execute("UPDATE matches SET status = 'lobby' WHERE id = ?", (match["id"],))
    _note(match, message)
    if retry:
        _retry_at[match["id"]] = clock.now() + config.CONTEST_RETRY_START_S
    events.blocked(match, message)
    events.state(match["id"])
    return False


def _drop(match, user_id: int, why: str) -> None:
    db.execute("DELETE FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], user_id))
    db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ? AND captain_id = ?", (match["id"], user_id))
    hub.send_user(user_id, "match.dropped", {"code": match["code"], "message": why})


def _seat_unplaced(match) -> None:
    """Teams: anyone not on a team yet joins the smallest team with room; the rest can't play."""
    count, size, s = _team_conf(match)
    if (s.get("draft") or {}).get("active"):
        s["draft"] = {**s["draft"], "active": False, "done": True, "deadline": None}
        db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))
    players = store.players(match["id"])
    counts = [sum(1 for p in players if p["team"] == t) for t in range(count)]
    for p in players:
        if p["team"] is not None and 0 <= p["team"] < count:
            continue
        t = team_core.smallest_team(counts, size)
        if t is None:
            _drop(match, p["user_id"], "Every team was full when the contest started.")
            continue
        counts[t] += 1
        db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?", (t, match["id"], p["user_id"]))


def _take_locks(match) -> None:
    """Players get the one-active-match lock now. A lobby or queue elsewhere is left automatically;
    someone still playing another match can't take part."""
    from app.matches import service
    for p in store.players(match["id"]):
        uid = p["user_id"]
        try:
            locks.acquire(uid, "match", match["id"])
            continue
        except locks.LockError:
            pass
        held = locks.current(uid)
        if held and held["kind"] == "queue":
            db.execute("DELETE FROM queue WHERE user_id = ?", (uid,))
            locks.release(uid)
        elif held and held["kind"] == "match":
            other = store.match_by_id(held["match_id"])
            if other is not None and other["status"] == "lobby":
                try:
                    service.leave(users.by_id(uid), other["code"])
                except MatchError:
                    pass
            if other is None or other["status"] in ("lobby", "ended", "cancelled"):
                locks.release(uid, held["match_id"])  # a stale lock: nothing is running there
        try:
            locks.acquire(uid, "match", match["id"])
        except locks.LockError:
            _drop(match, uid, "You were still playing another match when the contest started.")


async def start(match_id: int, force: bool = False) -> bool:
    if match_id in _starting:
        return False
    cur = db.execute("UPDATE matches SET status = 'starting' WHERE id = ? AND status = 'lobby' AND mode = 'contest'",
                     (match_id,))
    if cur.rowcount != 1:
        return False
    _starting.add(match_id)
    match = store.match_by_id(match_id)
    try:
        return await _start(match, force)
    except Exception:
        log.exception("starting contest %s failed", match_id)
        try:
            _back(match, "Something went wrong starting the contest. Retrying shortly.", retry=True)
        except Exception:
            log.exception("could not put contest %s back in the lobby", match_id)
        return False
    finally:
        _starting.discard(match_id)


async def _start(match, force: bool) -> bool:
    events.state(match["id"])
    s = store.settings(match)
    if not s.get("contest_problems"):
        return _back(match, "Add problems before starting.", retry=False)
    if store.is_team(match):
        _seat_unplaced(match)
    blocked = start_blocker(match, store.players(match["id"]), force)
    if blocked:
        return _back(match, blocked + (" The host can start anyway." if store.is_team(match) else ""), retry=True)
    ids = [p["user_id"] for p in store.players(match["id"])]
    try:
        missing = await handles.check_players(ids)  # one batched user.info call
    except CFError:
        return _back(match, "Codeforces is not answering, so the contest can't start yet. Retrying every 30 seconds.",
                     retry=True)
    if missing:
        # one renamed handle must not hold up everyone else: those players sit this one out
        for p in store.players(match["id"]):
            u = users.by_id(p["user_id"])
            if u["handle"] is None or u["needs_reverify"]:
                _drop(match, p["user_id"], f"{p['handle']} no longer exists on Codeforces. "
                                            "Re-verify your handle in Settings to play next time.")
    _take_locks(match)
    players = store.players(match["id"])
    blocked = start_blocker(match, players, force)
    if blocked:
        locks.release_match(match["id"])
        return _back(match, blocked, retry=True)
    _note(match, None)
    match = store.match_by_id(match["id"])
    s = store.settings(match)
    if s.get("freeze_min"):
        s["freeze_at"] = clock.now() + config.COUNTDOWN_S + (s["duration_min"] - s["freeze_min"]) * 60
        db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))
        match = store.match_by_id(match["id"])
    _begin(match, players, problem_rows(s))
    _retry_at.pop(match["id"], None)
    return True


async def _prefetch(match_id: int) -> None:
    """A few minutes before the start: sync missing histories and refresh handles, so the start is quick."""
    match = store.match_by_id(match_id)
    if match is None:
        return
    ids = [p["user_id"] for p in store.players(match_id)]
    for uid in ids:
        if not db.scalar("SELECT history_synced FROM users WHERE id = ?", (uid,)):
            try:
                await history.sync(uid, Lane.INTERACTIVE)
            except Exception as e:
                log.warning("pre-start history sync failed for %s: %s", uid, e)
    try:
        await handles.check_players(ids)
    except CFError as e:
        log.warning("pre-start handle check failed: %s", e)
    events.state(match_id)


def tick(now: int) -> None:
    """Every second: prefetch before the start, auto-start on time, cancel lobbies long past their start."""
    for m in db.all("SELECT * FROM matches WHERE mode = 'contest' AND status = 'lobby'"):
        at = m["scheduled_at"]
        if at is None:
            if m["created_at"] < now - config.CONTEST_UNSCHEDULED_IDLE_S:
                _cancel_stale(m, now)
            continue
        if at - config.CONTEST_PREFETCH_S <= now < at and m["id"] not in _prefetched:
            _prefetched.add(m["id"])
            _spawn(_prefetch(m["id"]))
        if now >= at + config.CONTEST_STALE_S:
            _cancel_stale(m, now)
            continue
        if now >= at and now >= _retry_at.get(m["id"], 0) and m["id"] not in _starting:
            _retry_at[m["id"]] = now + config.CONTEST_RETRY_START_S
            _spawn(start(m["id"]))


def _cancel_stale(m, now: int) -> None:
    db.execute("UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ?",
               (now, m["id"]))
    locks.release_match(m["id"])
    events.state(m["id"])


# ---------- the Events tab ----------

def _format_line(m, s: dict, players: int) -> dict:
    team = s.get("format") == "teams"
    return {
        "code": m["code"], "title": s.get("title") or "Contest", "status": m["status"],
        "rules": m["rules"], "format": s.get("format", "solo"),
        "team_count": s.get("team_count") if team else None, "team_size": s.get("team_size") if team else None,
        "problem_count": s.get("count", 0), "duration_min": s.get("duration_min"),
        "scheduled_at": m["scheduled_at"], "start_time": m["start_time"], "end_time": m["end_time"],
        "ended_at": m["ended_at"], "visibility": m["visibility"],
        "players": players, "slots": (s.get("team_count", 2) * s.get("team_size", 2)) if team else s.get("max_players"),
    }


def _winner(m) -> dict | None:
    if m["result_status"] != "final" or not m["result"]:
        return None
    summary = json.loads(m["result"])
    top = [r for r in summary if r.get("place") == 1]
    if not top:
        return None
    if "team" in top[0]:
        return {"kind": "team", "names": [r.get("name") or team_core.default_name(r["team"]) for r in top]}
    rows = [db.one("SELECT handle, cf_rating FROM match_players WHERE match_id = ? AND user_id = ?",
                   (m["id"], r["user_id"])) for r in top]
    return {"kind": "player", "players": [{"handle": r["handle"], "cf_rating": r["cf_rating"]} for r in rows if r]}


def listing(viewer_id: int | None) -> dict:
    rows = db.all(
        "SELECT m.*, u.handle AS host_handle, u.cf_rating AS host_rating,"
        " (SELECT COUNT(*) FROM match_players mp WHERE mp.match_id = m.id) AS n_players,"
        " (SELECT 1 FROM match_players mp WHERE mp.match_id = m.id AND mp.user_id = ?) AS joined"
        " FROM matches m LEFT JOIN users u ON u.id = m.creator_id WHERE m.mode = 'contest'"
        " AND m.status != 'cancelled' AND (m.visibility = 'public' OR m.creator_id = ? OR EXISTS"
        " (SELECT 1 FROM match_players mp WHERE mp.match_id = m.id AND mp.user_id = ?))"
        " ORDER BY COALESCE(m.scheduled_at, m.start_time, m.created_at)",
        (viewer_id or -1, viewer_id or -1, viewer_id or -1))
    out = {"upcoming": [], "live": [], "past": [], "mine": [], "server_time": clock.now()}
    for m in rows:
        s = store.settings(m)
        item = _format_line(m, s, m["n_players"])
        item["host"] = {"handle": m["host_handle"], "cf_rating": m["host_rating"]}
        item["role"] = "host" if viewer_id and m["creator_id"] == viewer_id else "player" if m["joined"] else None
        if m["status"] == "ended":
            item["winner"] = _winner(m)
            item["result_status"] = m["result_status"]
        public = m["visibility"] == "public"
        if public:
            if m["status"] in ("lobby", "starting"):
                out["upcoming"].append(item)
            elif m["status"] == "running":
                out["live"].append(item)
            elif m["status"] == "ended":
                out["past"].append(item)
        if item["role"]:
            out["mine"].append(item)
    out["past"] = sorted(out["past"], key=lambda x: -(x["ended_at"] or 0))[:20]
    rank = {"running": 0, "starting": 1, "lobby": 2, "ended": 3}
    out["mine"] = sorted(out["mine"], key=lambda x: (rank.get(x["status"], 4),
                                                     (x["scheduled_at"] or 0) if x["status"] != "ended"
                                                     else -(x["ended_at"] or 0)))[:20]
    return out

