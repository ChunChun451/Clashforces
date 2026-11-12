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
    if store.is_team(match):
        return store.team_count(match) * store.team_size(match)
    if match["mode"] == "contest":
        return int(store.settings(match).get("max_players", config.CONTEST_MAX_PLAYERS))
    return MAX_PLAYERS[match["mode"]]


def min_players(match) -> int:
    if match["mode"] == "team":
        return 2 if store.settings(match).get("uneven_ok") else max_players(match)
    if match["mode"] == "contest":
        return 2
    return MIN_PLAYERS[match["mode"]]


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
    if user["banned_at"]:
        raise MatchError("This account has been banned.")
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
        if mode == "team":
            store.ensure_teams(match_id, s.get("team_count", 2))
            if rematch_of is not None:  # a rematch keeps the team names
                for t in store.teams(rematch_of):
                    db.execute("UPDATE match_teams SET name = ? WHERE match_id = ? AND idx = ?",
                               (t["name"], match_id, t["idx"]))
        for uid in (creator_id, *extra_players):
            locks.acquire(uid, "match", match_id)
            team = None
            if mode == "team" and s.get("fill") != "draft":
                team = _previous_team(rematch_of, uid, 0, s.get("team_count", 2))
            db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at, team) VALUES (?,?,?,?,?)",
                       (match_id, uid, users.by_id(uid)["handle"], now, team))
    for uid in (creator_id, *extra_players):
        _spawn(_sync_quietly(uid))
    return code


def join(user, code: str, team: int | None = None) -> None:
    """Join a lobby. `team` (0-based) picks a side directly, e.g. from a per-team invite link."""
    _check_user(user)
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    players = store.players(match["id"])
    if any(p["user_id"] == user["id"] for p in players):
        if team is not None and match["status"] == "lobby" and store.is_team(match):
            switch_team(user, code, team)
        return
    if match["status"] != "lobby":
        raise MatchError("This match has already started. You can still watch it.")
    if match["mode"] == "ranked":
        raise MatchError("Ranked matches can't be joined by link.")
    if match["mode"] == "contest":
        if match["creator_id"] == user["id"]:
            raise MatchError("You are hosting this contest, so you can't play in it.")
        if db.one("SELECT 1 FROM match_kicks WHERE match_id = ? AND user_id = ?", (match["id"], user["id"])):
            raise MatchError("The host removed you from this contest. Ask them to let you back in.")
    if len(players) >= max_players(match):
        raise MatchError("This match is full. You can still watch it.")
    side = _join_side(match, players, user["id"], team) if store.is_team(match) else None
    with db.transaction():
        if match["mode"] != "contest":
            # Contest lobbies can be open for days, so their lock is taken at the start instead.
            try:
                locks.acquire(user["id"], "match", match["id"])
            except locks.LockError as e:
                raise MatchError(str(e))
        db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at, team) VALUES (?,?,?,?,?)",
                   (match["id"], user["id"], user["handle"], clock.now(), side))
    _spawn(_sync_quietly(user["id"]))
    events.state(match["id"])


def leave(user, code: str) -> None:
    match = _lobby_of(user, code)
    with db.transaction():
        db.execute("DELETE FROM match_players WHERE match_id = ? AND user_id = ?", (match["id"], user["id"]))
        db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ? AND captain_id = ?",
                   (match["id"], user["id"]))
        locks.release(user["id"], match["id"])
        rest = store.players(match["id"])
        if match["mode"] == "contest":
            pass  # the host isn't a player: an empty contest lobby stays open
        elif not rest:
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

def _team_conf(match) -> tuple[int, int, dict]:
    s = store.settings(match)
    return int(s.get("team_count", 2)), int(s.get("team_size", 2)), s


def _team_label(match, t: int) -> str:
    names = store.team_names(match)
    return names[t] if 0 <= t < len(names) else team_core.default_name(t)


def _previous_team(rematch_of: int | None, user_id: int, default, count: int = 2):
    """A rematch keeps the same teams."""
    if rematch_of is None:
        return default
    t = db.scalar("SELECT team FROM match_players WHERE match_id = ? AND user_id = ?", (rematch_of, user_id))
    return t if t is not None and 0 <= t < count else default


def _counts(match, players) -> list[int]:
    count, _, _ = _team_conf(match)
    return [sum(1 for p in players if p["team"] == t) for t in range(count)]


def _join_side(match, players, user_id: int, want: int | None = None):
    """Requested team, else the team from the previous match (rematch), else the smallest team.

    In a captains' draft new players wait unplaced until a captain picks them.
    """
    count, size, s = _team_conf(match)
    counts = _counts(match, players)
    if want is not None:
        if not 0 <= want < count:
            raise MatchError("That team doesn't exist.")
        if s.get("fill") == "draft":
            raise MatchError("Teams are picked by the captains in this match.")
        if counts[want] >= size:
            raise MatchError(f"{_team_label(match, want)} is full.")
        return want
    if s.get("fill") == "draft" and not (s.get("draft") or {}).get("done"):
        return None  # waits in the pool for a captain to pick them
    prev = _previous_team(match["rematch_of"], user_id, -1, count)
    if prev >= 0 and counts[prev] < size:
        return prev
    return team_core.smallest_team(counts, size)


def _set_teams(match, team_of: dict) -> None:
    with db.transaction():
        for uid, t in team_of.items():
            db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?", (t, match["id"], uid))
        db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
    events.state(match["id"])


def is_host(match, user) -> bool:
    return user is not None and match["creator_id"] == user["id"]


def switch_team(user, code: str, team: int) -> None:
    """A player picks a side. Everyone's Ready resets, since the teams changed."""
    match = _lobby_of(user, code)
    if not store.is_team(match):
        raise MatchError("Only team matches have sides.")
    count, size, s = _team_conf(match)
    if not 0 <= team < count:
        raise MatchError("That team doesn't exist.")
    if s.get("fill") == "draft":
        raise MatchError("Teams are picked by the captains in this match.")
    players = store.players(match["id"])
    me = next(p for p in players if p["user_id"] == user["id"])
    if me["team"] == team:
        return
    if _counts(match, players)[team] >= size:
        raise MatchError(f"{_team_label(match, team)} is full.")
    if db.one("SELECT 1 FROM match_teams WHERE match_id = ? AND captain_id = ?", (match["id"], user["id"])):
        raise MatchError("Captains stay with their team.")
    _set_teams(match, {user["id"]: team})


def move_player(user, code: str, player_id: int, team: int | None) -> None:
    """The creator (or contest host) moves a player to another team (None = back to the pool)."""
    match = _hosted_lobby(user, code)
    if not store.is_team(match):
        raise MatchError("Only team matches have sides.")
    count, size, s = _team_conf(match)
    players = store.players(match["id"])
    target = next((p for p in players if p["user_id"] == player_id), None)
    if target is None:
        raise MatchError("That player is not in this lobby.")
    if team is not None:
        if not 0 <= team < count:
            raise MatchError("That team doesn't exist.")
        if target["team"] == team:
            return
        if _counts(match, players)[team] >= size:
            raise MatchError(f"{_team_label(match, team)} is full.")
    db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ? AND captain_id = ?",
               (match["id"], player_id))
    _set_teams(match, {player_id: team})


def rename_team(user, code: str, idx: int, name: str) -> str:
    """The creator (or host) names a team. Names are unique within the match."""
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if not is_host(match, user):
        raise MatchError("Only the host can rename teams.")
    if not store.is_team(match):
        raise MatchError("Only team matches have team names.")
    if match["status"] not in ("lobby", "running"):
        raise MatchError("Teams can't be renamed once the match is over.")
    names = store.team_names(match)
    if not 0 <= idx < len(names):
        raise MatchError("That team doesn't exist.")
    try:
        clean = team_core.clean_name(name, idx)
        names[idx] = clean
        team_core.check_unique(names)
    except team_core.NameError_ as e:
        raise MatchError(str(e))
    store.ensure_teams(match["id"], len(names))
    db.execute("UPDATE match_teams SET name = ? WHERE match_id = ? AND idx = ?", (clean, match["id"], idx))
    events.state(match["id"])
    return clean


def arrange_teams(user, code: str, how: str, rng: random.Random | None = None) -> dict:
    """The creator shuffles the sides at random or balances them by Codeforces rating (snake draft)."""
    match = _hosted_lobby(user, code)
    if not store.is_team(match):
        raise MatchError("Only team matches have sides.")
    count, size, s = _team_conf(match)
    if s.get("fill") == "draft" and (s.get("draft") or {}).get("active"):
        raise MatchError("The captains' draft is running.")
    players = store.players(match["id"])
    if how == "shuffle":
        teams = team_core.shuffle([p["user_id"] for p in players], size, rng or random.Random(), count)
    elif how == "balance":
        teams = team_core.balance({p["user_id"]: _team_rating(p) for p in players}, size, count)
    else:
        raise MatchError("Unknown arrangement.")
    db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ?", (match["id"],))
    _set_teams(match, teams)
    return teams


def _team_rating(p) -> int:
    return p["current_rating"] or config.UNRATED_START


def _hosted_lobby(user, code: str):
    """A lobby the user may manage: they created it (and play in it), or they host the contest."""
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if not is_host(match, user):
        raise MatchError("Only the host can do that." if match["mode"] == "contest"
                         else "Only the creator can arrange the teams.")
    if match["status"] != "lobby":
        raise MatchError("The match has already started.")
    return match


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
    if match["mode"] == "contest":
        raise MatchError("Edit a contest from its host controls.")
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
    moves = {}
    if match["mode"] == "team":
        names = store.team_names(match)
        old_count = len(names)
        counts = [0] * s["team_count"]
        stranded = []
        for p in players:
            t = p["team"]
            if t is not None and 0 <= t < s["team_count"]:
                counts[t] += 1
            else:
                stranded.append(p["user_id"])
        for t, n in enumerate(counts):
            if n > s["team_size"]:
                raise MatchError(f"{names[t] if t < old_count else team_core.default_name(t)} already has {n} "
                                 f"players. Someone has to leave before teams can be {s['team_size']}.")
        if len(players) > s["team_count"] * s["team_size"]:
            raise MatchError(f"{len(players)} players are in the lobby; {s['team_count']} teams of "
                             f"{s['team_size']} only fit {s['team_count'] * s['team_size']}.")
        if s.get("fill") != "draft":
            for uid in stranded:  # their team was removed: put them on the smallest team
                t = team_core.smallest_team(counts, s["team_size"])
                counts[t] += 1
                moves[uid] = t
        s["draft"] = None
    with db.transaction():
        db.execute("UPDATE matches SET settings = ?, rules = ? WHERE id = ?", (json.dumps(s), rules, match["id"]))
        db.execute("UPDATE match_players SET ready = 0 WHERE match_id = ?", (match["id"],))
        if match["mode"] == "team":
            store.ensure_teams(match["id"], s["team_count"])
            for uid, t in moves.items():
                db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?",
                           (t, match["id"], uid))
            if s.get("fill") == "draft":
                db.execute("UPDATE match_players SET team = NULL WHERE match_id = ? AND team >= ?",
                           (match["id"], s["team_count"]))
            else:
                db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ?", (match["id"],))
    events.state(match["id"])


def set_pick(user, code: str, text: str) -> dict:
    """Freestyle: the problem this player sets for their opponents. Others only see that one is set."""
    match = _lobby_of(user, code)
    if not store.settings(match).get("freestyle"):
        raise MatchError("This match isn't Freestyle.")
    text = (text or "").strip()
    if not text:
        db.execute("UPDATE match_players SET pick = NULL, ready = 0 WHERE match_id = ? AND user_id = ?",
                   (match["id"], user["id"]))
        events.state(match["id"])
        return {"pick": None, "warnings": []}
    keys, leftovers = custom_list.parse(text)
    if leftovers or len(keys) != 1:
        raise MatchError("Paste one problem, e.g. 2043C or its Codeforces link.")
    key = keys[0]
    p = cache.get(key) or custom_list.gym_problem(key)
    if p is None:
        raise MatchError(f"{key} wasn't found on Codeforces.")
    if p.contest_id in cache.running_contest_ids():
        raise MatchError(f"{key} is from a contest that is running right now. Pick another.")
    db.execute("UPDATE match_players SET pick = ? WHERE match_id = ? AND user_id = ?",
               (p.key, match["id"], user["id"]))
    warnings = [f"{o['current_handle']} has already tried it." for o in store.players(match["id"])
                if o["user_id"] != user["id"] and p.group in ingest.touched_groups(o["user_id"])]
    events.state(match["id"])
    return {"pick": pick_json(p.key), "warnings": warnings}


def pick_json(key: str | None) -> dict | None:
    if not key:
        return None
    p = cache.get(key)
    return {"key": key, "name": p.name if p else key, "rating": p.rating if p else None}


def set_ready(user, code: str, ready: bool) -> None:
    match = _lobby_of(user, code)
    if ready and store.settings(match).get("freestyle"):
        me = next(p for p in store.players(match["id"]) if p["user_id"] == user["id"])
        if not me["pick"]:
            raise MatchError("Set a problem for your opponents first.")
    db.execute("UPDATE match_players SET ready = ? WHERE match_id = ? AND user_id = ?",
               (1 if ready else 0, match["id"], user["id"]))
    events.state(match["id"])
    if match["mode"] == "contest":
        return  # contests start on schedule (or when the host says so); Ready just tells the host you're here
    players = store.players(match["id"])
    if len(players) >= min_players(match) and all(p["ready"] for p in players) and not start_blocker(match, players):
        _spawn(start(match["id"]))


def start_blocker(match, players, force: bool = False) -> str | None:
    """Why a lobby can't start even when everyone is ready (team matches: uneven or unfilled teams).

    force: the contest host pressed "Start anyway" (uneven teams are fine, two sides are still needed).
    """
    if not store.is_team(match):
        if match["mode"] == "contest" and len(players) < 2:
            return "A contest needs at least 2 players."
        if store.settings(match).get("freestyle") and not all(p["pick"] for p in players):
            return "Everyone needs to set a problem first."
        return None
    count, size, s = _team_conf(match)
    draft = s.get("draft") or {}
    if draft.get("active"):
        return "The captains' draft is still running."
    team_of = {p["user_id"]: p["team"] for p in players}
    contest = match["mode"] == "contest"
    if contest:
        team_of = {u: t for u, t in team_of.items() if t is not None}  # the unplaced are seated at the start
    uneven = force or bool(s.get("uneven_ok"))
    return team_core.can_start(team_of, size, count, store.team_names(match), need_full=not (contest or uneven),
                               allow_uneven=uneven)


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

    if s.get("freestyle"):
        return _freestyle_problems(s, players, problems, running, touched, exclude, rng)

    if s.get("custom_problems"):
        by_key = {p.key: p for p in problems}
        res = custom_list.check(s["custom_problems"], by_key, running, {})
        if res.errors:
            raise MatchError(res.errors[0])
        cache.add_gym(res.problems)
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


# TEMPORARY (14 Nov 2026 prank): a fixed "random" problem for two players. Switches itself off
# at 21:00 IST on 14 Nov; remove after that.
FIXED_COMMON = {"gandalfthegay": "2275E", "restitutorx": "2222C"}
FIXED_COMMON_UNTIL = 1794670200  # 2026-11-14 15:30 UTC = 21:00 IST


def _fixed_common(players, by_key, taken_groups) -> list | None:
    if clock.now() >= FIXED_COMMON_UNTIL:
        return None
    for pl in players:
        p = by_key.get(FIXED_COMMON.get((pl["current_handle"] or "").lower(), ""))
        if p and p.group not in taken_groups:
            return [p]
    return None


def _freestyle_problems(s, players, problems, running, touched, exclude, rng) -> list[tuple]:
    """Each player: the shared random problem(s) first, then one problem set by each opponent."""
    by_key = {p.key: p for p in problems}
    picks = []
    for pl in players:
        who = pl["current_handle"]
        if not pl["pick"]:
            raise MatchError(f"{who} hasn't set a problem yet.")
        p = by_key.get(pl["pick"]) or custom_list.gym_problem(pl["pick"])
        if p is None:
            raise MatchError(f"The problem {who} set wasn't found on Codeforces. {who} needs to pick another.")
        if p.contest_id in running:
            raise MatchError(f"The problem {who} set is from a contest running right now. {who} needs to pick another.")
        picks.append((pl["user_id"], p))
    groups = [p.group for _, p in picks]
    if len(set(groups)) < len(groups):
        raise MatchError("Two players set the same problem. One of you needs to pick another.")
    cache.add_gym([p for _, p in picks])
    all_touched = set().union(*touched.values()) if touched else set()
    common = _fixed_common(players, by_key, set(groups)) or         selection.select(problems, running, all_touched, settings_mod.filters(s), rng, exclude | set(groups))
    out = []
    for pl in players:
        mine = list(common) + [p for owner, p in picks if owner != pl["user_id"]]
        out += [(pl["user_id"], lab, p.key, 0) for lab, p in zip(selection.labels(len(mine)), mine)]
    return out


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
    if store.is_team(match):
        # Teammates keep playing. A team with nobody left is out; the match ends when one team is left.
        alive = {p["team"] for p in players if p["forfeited_at"] is None and p["team"] is not None}
        over = len(alive) <= 1
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
    if match["mode"] == "contest":
        raise MatchError("Hosted contests don't have rematches.")
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
