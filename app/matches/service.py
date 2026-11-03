"""Match actions: create, join, leave, settings, ready, start, forfeit, rematch."""

import asyncio
import json
import logging
import random
import secrets

from app import clock, config, db
from app.auth import handles, users
from app.cf import cache
from app.cf.client import CFError
from app.core import custom_list, selection
from app.core import team as team_core
from app.core.selection import SelectionError
from app.matches import events, locks, store
from app.matches import settings as settings_mod
from app.poller import history, ingest
from app.poller.poller import poller

log = logging.getLogger("matches")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_PLAYERS = {"friend": 2, "group": config.ROOM_MAX_PLAYERS, "ranked": 2}
MIN_PLAYERS = {"friend": 2, "group": config.ROOM_MIN_PLAYERS, "ranked": 2}


def max_players(match) -> int:
    if match["mode"] == "team":
        return 2 * store.settings(match).get("team_size", 2)
    return MAX_PLAYERS[match["mode"]]


def min_players(match) -> int:
    return max_players(match) if match["mode"] == "team" else MIN_PLAYERS[match["mode"]]


class MatchError(Exception):
    pass


def new_code() -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(config.CODE_LENGTH))
        if store.match_by_code(code) is None:
            return code


def _log_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("background task %s failed", task.get_name(), exc_info=exc)


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    task.add_done_callback(_log_task_error)


async def _sync_quietly(user_id: int) -> None:
    try:
        await history.sync(user_id)
    except Exception as e:
        log.warning("history sync failed for %s: %s", user_id, e)


def _check_user(user) -> None:
    if user["handle"] is None or user["needs_reverify"]:
        raise MatchError("Your Codeforces handle changed. Re-verify it in Settings first.")


# ---------- lobby ----------

def create(user, mode: str, rules: str, raw_settings: dict, rematch_of: int | None = None) -> str:
    if mode not in ("friend", "group", "team"):
        raise MatchError("Unknown mode.")
    _check_user(user)
    try:
        s = settings_mod.clean(raw_settings, rules, mode)
    except settings_mod.SettingsError as e:
        raise MatchError(str(e))
    try:
        return _insert_match(user["id"], mode, rules, s, rematch_of)
    except locks.LockError as e:
        raise MatchError(str(e))


def _insert_match(creator_id: int, mode: str, rules: str, s: dict, rematch_of: int | None = None,
                  extra_players: tuple[int, ...] = ()) -> str:
    code = new_code()
    now = clock.now()
    with db.transaction():
        cur = db.execute(
            "INSERT INTO matches(code, mode, rules, status, settings, creator_id, rematch_of, created_at)"
            " VALUES (?,?,?,'lobby',?,?,?,?)",
            (code, mode, rules, json.dumps(s), creator_id, rematch_of, now),
        )
        match_id = cur.lastrowid
        for uid in (creator_id, *extra_players):
            locks.acquire(uid, "match", match_id)
            team = _previous_team(rematch_of, uid, 0) if mode == "team" else None
            db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at, team) VALUES (?,?,?,?,?)",
                       (match_id, uid, users.by_id(uid)["handle"], now, team))
    for uid in (creator_id, *extra_players):
        _spawn(_sync_quietly(uid))
    return code


def join(user, code: str) -> None:
    _check_user(user)
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if any(p["user_id"] == user["id"] for p in store.players(match["id"])):
        return
    if match["status"] != "lobby":
        raise MatchError("This match has already started. You can still watch it.")
    if match["mode"] == "ranked":
        raise MatchError("Ranked matches can't be joined by link.")
    players = store.players(match["id"])
    if len(players) >= max_players(match):
        raise MatchError("This match is full. You can still watch it.")
    team = _join_side(match, players, user["id"]) if match["mode"] == "team" else None
    with db.transaction():
        try:
            locks.acquire(user["id"], "match", match["id"])
        except locks.LockError as e:
            raise MatchError(str(e))
        db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at, team) VALUES (?,?,?,?,?)",
                   (match["id"], user["id"], user["handle"], clock.now(), team))
    _spawn(_sync_quietly(user["id"]))
    events.state(match["id"])


def leave(user, code: str) -> None:
    match = _lobby_of(user, code)
    with db.transaction():
        db.execute("DELETE FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], user["id"]))
        locks.release(user["id"], match["id"])
        rest = store.players(match["id"])
        if not rest:
            db.execute("UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ?",
                       (clock.now(), match["id"]))
        else:
            if match["creator_id"] == user["id"]:
                db.execute("UPDATE matches SET creator_id = ? WHERE id = ?", (rest[0]["user_id"], match["id"]))
            s = store.settings(match)
            h = s.get("handicap")
            if h and h.get("user_id") == user["id"]:
                s["handicap"] = None
                db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))
            db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
    events.state(match["id"])


# ---------- teams ----------

def _previous_team(rematch_of: int | None, user_id: int, default: int) -> int:
    """A rematch keeps the same teams."""
    if rematch_of is None:
        return default
    t = db.scalar("SELECT team FROM match_players WHERE match_id = ? AND user_id = ?", (rematch_of, user_id))
    return t if t in (0, 1) else default


def _join_side(match, players, user_id: int) -> int:
    size = store.settings(match).get("team_size", 2)
    counts = [sum(1 for p in players if p["team"] == t) for t in (0, 1)]
    want = _previous_team(match["rematch_of"], user_id, -1)
    if want in (0, 1) and counts[want] < size:
        return want
    return 0 if counts[0] <= counts[1] else 1


def _set_teams(match, team_of: dict) -> None:
    with db.transaction():
        for uid, t in team_of.items():
            db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?", (t, match["id"], uid))
        db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
    events.state(match["id"])


def switch_team(user, code: str, team: int) -> None:
    """A player picks a side. Everyone's Ready resets, since the teams changed."""
    match = _lobby_of(user, code)
    if match["mode"] != "team":
        raise MatchError("Only team duels have sides.")
    if team not in (0, 1):
        raise MatchError("Pick Team A or Team B.")
    players = store.players(match["id"])
    me = next(p for p in players if p["user_id"] == user["id"])
    if me["team"] == team:
        return
    size = store.settings(match).get("team_size", 2)
    if sum(1 for p in players if p["team"] == team) >= size:
        raise MatchError(f"{team_core.TEAM_NAMES[team]} is full.")
    _set_teams(match, {user["id"]: team})


def arrange_teams(user, code: str, how: str, rng: random.Random | None = None) -> dict:
    """The creator shuffles the sides at random or balances them by Codeforces rating."""
    match = _lobby_of(user, code)
    if match["mode"] != "team":
        raise MatchError("Only team duels have sides.")
    if match["creator_id"] != user["id"]:
        raise MatchError("Only the creator can arrange the teams.")
    players = store.players(match["id"])
    size = store.settings(match).get("team_size", 2)
    if how == "shuffle":
        teams = team_core.shuffle([p["user_id"] for p in players], size, rng or random.Random())
    elif how == "balance":
        teams = team_core.balance({p["user_id"]: _team_rating(p) for p in players}, size)
    else:
        raise MatchError("Unknown arrangement.")
    _set_teams(match, teams)
    return teams


def _team_rating(p) -> int:
    return p["current_rating"] or config.UNRATED_START


def _lobby_of(user, code: str):
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if not any(p["user_id"] == user["id"] for p in store.players(match["id"])):
        raise MatchError("You are not in this match.")
    if match["status"] != "lobby":
        raise MatchError("The match has already started.")
    return match


def update_settings(user, code: str, raw: dict) -> None:
    match = _lobby_of(user, code)
    if match["creator_id"] != user["id"]:
        raise MatchError("Only the creator can change settings.")
    if match["mode"] == "ranked":
        raise MatchError("Ranked settings are fixed.")
    rules = raw.get("rules", match["rules"])
    old = store.settings(match)
    raw = {**raw, "exclude": old.get("exclude", [])}
    try:
        s = settings_mod.clean(raw, rules, match["mode"])
    except settings_mod.SettingsError as e:
        raise MatchError(str(e))
    h = s.get("handicap")
    players = store.players(match["id"])
    ids = {p["user_id"] for p in players}
    if h and h.get("user_id") is not None and h["user_id"] not in ids:
        raise MatchError("The handicap player is not in this lobby.")
    if match["mode"] == "team":
        for t in (0, 1):
            n = sum(1 for p in players if p["team"] == t)
            if n > s["team_size"]:
                raise MatchError(f"{team_core.TEAM_NAMES[t]} already has {n} players. "
                                 f"Someone has to leave before teams can be {s['team_size']}.")
    with db.transaction():
        db.execute("UPDATE matches SET settings = ?, rules = ? WHERE id = ?", (json.dumps(s), rules, match["id"]))
        db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
    events.state(match["id"])


def set_ready(user, code: str, ready: bool) -> None:
    match = _lobby_of(user, code)
    db.execute("UPDATE match_players SET ready = ? WHERE match_id = ? AND user_id = ?",
               (1 if ready else 0, match["id"], user["id"]))
    events.state(match["id"])
    players = store.players(match["id"])
    if len(players) >= min_players(match) and all(p["ready"] for p in players) and not start_blocker(match, players):
        _spawn(start(match["id"]))


def start_blocker(match, players) -> str | None:
    """Why a lobby can't start even when everyone is ready (team duels: uneven teams)."""
    if match["mode"] != "team":
        return None
    size = store.settings(match).get("team_size", 2)
    return team_core.can_start({p["user_id"]: (p["team"] if p["team"] in (0, 1) else -1) for p in players}, size)


# ---------- start ----------

def _back_to_lobby(match, message: str, handle: str | None = None) -> None:
    db.execute("UPDATE matches SET status = 'lobby' WHERE id = ?", (match["id"],))
    db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
    events.blocked(match, message, handle)
    events.state(match["id"])


async def start(match_id: int, rng: random.Random | None = None) -> bool:
    """Check handles, pick problems, start the countdown. Returns True if started."""
    cur = db.execute("UPDATE matches SET status = 'starting' WHERE id = ? AND status = 'lobby'", (match_id,))
    if cur.rowcount != 1:
        return False
    match = store.match_by_id(match_id)
    try:
        return await _start(match, rng)
    except Exception:
        # Anything unexpected must not leave the match stuck in 'starting'.
        log.exception("starting match %s failed", match_id)
        try:
            _back_to_lobby(match, "Something went wrong starting the match. Try again.")
        except Exception:
            log.exception("could not put match %s back in the lobby", match_id)
        return False


async def _start(match, rng: random.Random | None) -> bool:
    match_id = match["id"]
    events.state(match_id)
    players = store.players(match_id)
    blocked = start_blocker(match, players)
    if blocked:
        _back_to_lobby(match, blocked)
        return False
    ids = [p["user_id"] for p in players]
    try:
        missing = await handles.check_players(ids)
    except CFError:
        _back_to_lobby(match, "Codeforces is not answering, so we can't start yet. Try again in a minute.")
        return False
    if missing:
        _back_to_lobby(match, f"{missing[0]} no longer exists on Codeforces. That player must re-verify "
                              "their new handle in Settings.", missing[0])
        return False
    for uid in ids:
        if not db.scalar("SELECT history_synced FROM users WHERE id = ?", (uid,)):
            try:
                await history.sync(uid)
            except CFError:
                _back_to_lobby(match, "Couldn't load a player's Codeforces history. Try again in a minute.")
                return False
    players = store.players(match_id)  # ratings refreshed
    try:
        problem_rows = choose_problems(match, players, rng or random.Random())
    except (SelectionError, MatchError) as e:
        _back_to_lobby(match, str(e))
        return False
    _begin(match, players, problem_rows)
    return True


def choose_problems(match, players, rng) -> list[tuple]:
    """Returns [(owner_user_id | None, label, problem_key, points)]."""
    s = store.settings(match)
    problems = cache.problems()
    if not problems:
        raise MatchError("The problem list is still loading. Try again in a minute.")
    running = cache.running_contest_ids()
    touched = {p["user_id"]: ingest.touched_groups(p["user_id"]) for p in players}
    exclude = set()
    for key in s.get("exclude", []):
        p = cache.get(key)
        exclude.add(p.group if p else key)
    h = s.get("handicap") or {}

    if s.get("custom_problems"):
        by_key = {p.key: p for p in problems}
        res = custom_list.check(s["custom_problems"], by_key, running, {})
        if res.errors:
            raise MatchError(res.errors[0])
        chosen = res.problems
        pts = selection.lockout_points(len(chosen))
        return [(None, lab, p.key, pt) for lab, p, pt in zip(selection.labels(len(chosen)), chosen, pts)]

    f = settings_mod.filters(s)
    if h.get("type") == "per_player_range":
        ranges = h.get("ranges", {})
        entries = []
        for p in players:
            lo, hi = ranges.get(str(p["user_id"])) or settings_mod.default_range(p["current_rating"])
            entries.append((p["user_id"], p["current_handle"], lo, hi, touched[p["user_id"]]))
        per = selection.select_per_player(problems, running, entries, f, rng, exclude)
        out = []
        for uid, chosen in per.items():
            for lab, p in zip(selection.labels(len(chosen)), chosen):
                out.append((uid, lab, p.key, 0))
        return out

    all_touched = set().union(*touched.values()) if touched else set()
    chosen = selection.select(problems, running, all_touched, f, rng, exclude)
    pts = selection.lockout_points(len(chosen))
    return [(None, lab, p.key, pt) for lab, p, pt in zip(selection.labels(len(chosen)), chosen, pts)]


def _begin(match, players, problem_rows) -> None:
    s = store.settings(match)
    h = s.get("handicap") or {}
    now = clock.now()
    start_time = now + config.COUNTDOWN_S
    end_time = start_time + s["duration_min"] * 60
    with db.transaction():
        db.execute("DELETE FROM match_problems WHERE match_id = ?", (match["id"],))
        db.executemany(
            "INSERT INTO match_problems(match_id, user_id, label, problem_key, points) VALUES (?,?,?,?,?)",
            [(match["id"], owner, label, key, pts) for owner, label, key, pts in problem_rows],
        )
        db.execute("UPDATE matches SET status = 'running', start_time = ?, end_time = ? WHERE id = ?",
                   (start_time, end_time, match["id"]))
        for p in players:
            uid = p["user_id"]
            p_end = end_time
            bonus = 0
            lo = hi = None
            if h.get("type") == "extra_time" and h.get("user_id") == uid:
                p_end += h["minutes"] * 60
            if h.get("type") == "penalty" and h.get("user_id") == uid:
                bonus = -h["minutes"]
            if h.get("type") == "per_player_range":
                own = [cache.get(k) for o, _, k, _ in problem_rows if o == uid]
                if own:
                    lo = min(x.rating for x in own if x)
                    hi = max(x.rating for x in own if x)
            db.execute(
                "UPDATE match_players SET handle = ?, cf_rating = ?, end_time = ?, penalty_bonus = ?,"
                " rating_min = ?, rating_max = ? WHERE match_id = ? AND user_id = ?",
                (p["current_handle"], p["current_rating"], p_end, bonus, lo, hi, match["id"], uid),
            )
    for p in store.players(match["id"]):
        poller.watch(p["user_id"], p["handle"], start_time, p["end_time"])
    events.state(match["id"])


# ---------- during / after ----------

def hint(user, code: str) -> bool:
    match = store.match_by_code(code)
    if match is None or match["status"] != "running" or not store.is_running(match["id"], user["id"]):
        raise MatchError("You are not playing this match.")
    return poller.hard_hint(user["id"])


def forfeit(user, code: str) -> None:
    from app.matches import lifecycle
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    me = next((p for p in store.players(match["id"]) if p["user_id"] == user["id"]), None)
    if me is None:
        raise MatchError("You are not in this match.")
    if match["status"] == "lobby":
        return leave(user, code)
    if match["status"] != "running":
        raise MatchError("The match is not running.")
    if me["forfeited_at"] is not None:
        return
    now = clock.now()
    db.execute("UPDATE match_players SET forfeited_at = ?, end_time = MIN(end_time, ?) WHERE match_id = ? AND user_id = ?",
               (now, max(now, match["start_time"]), match["id"], user["id"]))
    players = store.players(match["id"])
    if match["mode"] == "team":
        # Teammates keep playing. A team with nobody left loses and the match ends.
        over = any(all(p["forfeited_at"] is not None for p in players if p["team"] == t) for t in (0, 1))
    else:
        over = len([p for p in players if p["forfeited_at"] is None]) <= 1
    if over:
        lifecycle.end_match(match["id"], "forfeit", at=now)
    else:
        events.state(match["id"])


def rematch(user, code: str) -> str:
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if match["status"] != "ended":
        raise MatchError("You can rematch once the match has ended.")
    if match["mode"] == "ranked":
        raise MatchError("Queue again for another ranked match.")
    if not any(p["user_id"] == user["id"] for p in store.players(match["id"])):
        raise MatchError("Only players can start a rematch.")
    existing = db.one("SELECT code, status FROM matches WHERE rematch_of = ? AND status = 'lobby'", (match["id"],))
    if existing:
        join(user, existing["code"])
        return existing["code"]
    s = store.settings(match)
    used = [p["problem_key"] for p in store.problems(match["id"])]
    s["exclude"] = sorted(set(s.get("exclude", [])) | set(used))
    if s.get("custom_problems"):
        # A custom list can't give new problems; pick random ones in the same rating span instead.
        ratings = [p.rating for p in (cache.get(k) for k in used) if p and p.rating]
        s["custom_problems"] = None
        s["count"] = max(1, min(len(set(used)), config.PROBLEM_COUNT_MAX))
        if ratings:
            s["rating_min"], s["rating_max"] = min(ratings), max(ratings)
    _check_user(user)
    try:
        new = _insert_match(user["id"], match["mode"], match["rules"], s, rematch_of=match["id"])
    except locks.LockError as e:
        raise MatchError(str(e))
    from app.ws.hub import hub
    hub.send_match(match["code"], "rematch.offer", {"code": new, "from": user["handle"]})
    return new
