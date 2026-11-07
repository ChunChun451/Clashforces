"""Load match data from the DB and run the pure scoring on it."""

import json
import sqlite3

from app import clock, db
from app.core import sprint as sprint_core
from app.core import team as team_core
from app.core.icpc import score_icpc
from app.core.lockout import score_lockout
from app.models import PlayerInput, Row, Sub


def match_by_code(code: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM matches WHERE code = ?", (code.upper(),))


def match_by_id(match_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM matches WHERE id = ?", (match_id,))


def settings(match) -> dict:
    return json.loads(match["settings"])


def players(match_id: int) -> list[sqlite3.Row]:
    return db.all(
        "SELECT mp.*, u.handle AS current_handle, u.cf_rating AS current_rating, u.duel_rating, u.needs_reverify"
        " FROM match_players mp JOIN users u ON u.id = mp.user_id WHERE mp.match_id = ? ORDER BY mp.joined_at, mp.user_id",
        (match_id,),
    )


def problems(match_id: int) -> list[sqlite3.Row]:
    return db.all(
        "SELECT mp.*, p.name, p.rating, p.tags, p.contest_id, p.idx FROM match_problems mp"
        " LEFT JOIN problems p ON p.key = mp.problem_key WHERE mp.match_id = ? ORDER BY mp.user_id, mp.label",
        (match_id,),
    )


def player_problem_keys(problem_rows, user_id: int) -> tuple[str, ...]:
    """Shared problems, or this player's own set for the per-player range handicap."""
    own = [p["problem_key"] for p in problem_rows if p["user_id"] == user_id]
    if own:
        return tuple(own)
    return tuple(p["problem_key"] for p in problem_rows if p["user_id"] is None)


def is_running(match_id: int, user_id: int) -> bool:
    return db.one(
        "SELECT 1 FROM match_players mp JOIN matches m ON m.id = mp.match_id"
        " WHERE mp.match_id = ? AND mp.user_id = ? AND m.status = 'running'", (match_id, user_id)) is not None


def running_match_for(user_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT m.* FROM matches m JOIN match_players mp ON mp.match_id = m.id"
        " WHERE mp.user_id = ? AND m.status = 'running' ORDER BY m.id DESC LIMIT 1", (user_id,))


def inputs(match, player_rows, problem_rows) -> list[PlayerInput]:
    return [
        PlayerInput(
            id=p["user_id"],
            end_time=p["end_time"] or match["end_time"],
            problems=player_problem_keys(problem_rows, p["user_id"]),
            penalty_bonus=p["penalty_bonus"],
            forfeited=p["forfeited_at"] is not None,
        )
        for p in player_rows
    ]


def subs(match, player_rows, problem_rows) -> list[Sub]:
    if match["start_time"] is None or not player_rows or not problem_rows:
        return []
    ids = [p["user_id"] for p in player_rows]
    keys = list({p["problem_key"] for p in problem_rows})
    last_end = max((p["end_time"] or match["end_time"]) for p in player_rows)
    q_ids = ",".join("?" * len(ids))
    q_keys = ",".join("?" * len(keys))
    rows = db.all(
        f"SELECT id, user_id, problem_key, verdict, creation_time FROM submissions"
        f" WHERE user_id IN ({q_ids}) AND problem_key IN ({q_keys}) AND creation_time >= ? AND creation_time < ?"
        f" ORDER BY creation_time, id",
        (*ids, *keys, match["start_time"], last_end),
    )
    out = [Sub(r["id"], r["user_id"], r["problem_key"], r["verdict"], r["creation_time"]) for r in rows]
    if is_sprint(match):
        # only what was submitted while its problem was the open one counts
        out = sprint_core.in_window(sprint_steps(match, problem_rows, out, last_end), out)
    return out


# ---------- sprint: lockout with one problem open at a time ----------

def is_sprint(match) -> bool:
    return match["rules"] == "lockout" and bool(settings(match).get("sprint"))


def sprint_keys(problem_rows) -> list[str]:
    shared = sorted((p for p in problem_rows if p["user_id"] is None), key=lambda p: p["label"])
    return [p["problem_key"] for p in shared]


def sprint_limit_s(match) -> int | None:
    m = int(settings(match).get("sprint_limit_min") or 0)
    return m * 60 if m else None


def sprint_steps(match, problem_rows, raw_subs, last_end: int) -> list:
    now = min(clock.now(), last_end)
    if match["ended_at"] is not None:
        now = min(now, match["ended_at"])
    return sprint_core.schedule(match["start_time"], sprint_keys(problem_rows), raw_subs, sprint_limit_s(match), now)


def sprint_state(match, player_rows, problem_rows) -> list:
    """The problems revealed so far, as sprint steps (empty before the start)."""
    if match["start_time"] is None or not player_rows or not problem_rows:
        return []
    last_end = max((p["end_time"] or match["end_time"]) for p in player_rows)
    return sprint_steps(match, problem_rows, subs(match, player_rows, problem_rows), last_end)


def points(problem_rows) -> dict[str, int]:
    return {p["problem_key"]: p["points"] for p in problem_rows if p["user_id"] is None}


# ---------- team matches ----------

def is_team(match) -> bool:
    """Team duels, and hosted contests played in teams."""
    if match["mode"] == "team":
        return True
    return match["mode"] == "contest" and settings(match).get("format") == "teams"


def team_count(match) -> int:
    return int(settings(match).get("team_count", 2))


def team_size(match) -> int:
    return int(settings(match).get("team_size", 2))


def teams(match_id: int) -> list[sqlite3.Row]:
    return db.all("SELECT * FROM match_teams WHERE match_id = ? ORDER BY idx", (match_id,))


def team_names(match) -> list[str]:
    """Names by index. Missing rows (very old matches) fall back to Team A, B, ..."""
    stored = {t["idx"]: t["name"] for t in teams(match["id"])}
    return [stored.get(i) or team_core.default_name(i) for i in range(team_count(match))]


def ensure_teams(match_id: int, count: int) -> None:
    """Make sure rows 0..count-1 exist (default names) and drop rows past count."""
    db.execute("DELETE FROM match_teams WHERE match_id = ? AND idx >= ?", (match_id, count))
    taken = {t["name"].casefold() for t in teams(match_id)}
    for i in range(count):
        if db.one("SELECT 1 FROM match_teams WHERE match_id = ? AND idx = ?", (match_id, i)):
            continue
        name = team_core.default_name(i)
        n = 2
        while name.casefold() in taken:  # a renamed team already uses the default name
            name = f"{team_core.default_name(i)} {n}"
            n += 1
        taken.add(name.casefold())
        db.execute("INSERT INTO match_teams(match_id, idx, name) VALUES (?,?,?)", (match_id, i, name))


def team_of(player_rows) -> dict[int, int]:
    """user_id -> team index. Players without a team are left out."""
    return {p["user_id"]: p["team"] for p in player_rows if p["team"] is not None and p["team"] >= 0}


def member_end(match, player_rows) -> dict[int, int]:
    return {p["user_id"]: p["end_time"] or match["end_time"] for p in player_rows}


def team_inputs(match, player_rows, problem_rows) -> dict[int, PlayerInput]:
    """One scoring input per team that has players."""
    keys = player_problem_keys(problem_rows, -1)  # shared set (team mode has no per-player sets)
    out = {}
    for t in range(team_count(match)):
        members = [p for p in player_rows if p["team"] == t]
        if not members:
            continue
        ends = [p["end_time"] or match["end_time"] for p in members]
        out[t] = team_core.team_input(t, max(ends), keys,
                                      forfeited=all(p["forfeited_at"] is not None for p in members))
    return out


def team_parts(match, player_rows, problem_rows):
    """(score(team_subs) -> ranked team rows, to_team(member_subs) -> team_subs)."""
    teams_in = team_inputs(match, player_rows, problem_rows)
    members = team_of(player_rows)
    ends = member_end(match, player_rows)
    ident = {t: t for t in teams_in}
    if match["rules"] == "lockout":
        pts = points(problem_rows)
        score = lambda ts: team_core.score_team_lockout(match["start_time"], teams_in, ident, pts, ts)  # noqa: E731
    else:
        score = lambda ts: team_core.score_team(match["start_time"], teams_in, ident, ts)  # noqa: E731
    return score, lambda s: team_core.team_subs(s, members, ends)


def member_rows(match, player_rows, problem_rows, subs_list) -> dict:
    """Each member's own ICPC-style cells (who solved what, own tries). user_id -> Row."""
    rows = score_icpc(match["start_time"], inputs(match, player_rows, problem_rows), subs_list)
    return {r.player: r for r in rows}


def scorer(match, player_rows, problem_rows):
    """Returns score(subs) -> ranked rows for this match's rules.

    Team matches return one row per team (row.player = team index).
    """
    if is_team(match):
        score, to_team = team_parts(match, player_rows, problem_rows)
        return lambda s: score(to_team(s))
    ins = inputs(match, player_rows, problem_rows)
    if match["rules"] == "lockout":
        pts = points(problem_rows)
        return lambda s: score_lockout(match["start_time"], ins, pts, s)
    return lambda s: score_icpc(match["start_time"], ins, s)


def standings(match) -> tuple[list[Row], list[Sub]]:
    pr = players(match["id"])
    pb = problems(match["id"])
    all_subs = subs(match, pr, pb)
    return scorer(match, pr, pb)(all_subs), all_subs
