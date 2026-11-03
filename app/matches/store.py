"""Load match data from the DB and run the pure scoring on it."""

import json
import sqlite3

from app import db
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
    return [Sub(r["id"], r["user_id"], r["problem_key"], r["verdict"], r["creation_time"]) for r in rows]


def points(problem_rows) -> dict[str, int]:
    return {p["problem_key"]: p["points"] for p in problem_rows if p["user_id"] is None}


def scorer(match, player_rows, problem_rows):
    """Returns score(subs) -> ranked rows for this match's rules."""
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
