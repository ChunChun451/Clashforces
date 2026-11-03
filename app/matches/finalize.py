"""Turn a finished match into a final result: places, upsolve list, ratings, notifications."""

import json
import logging
from typing import Callable

from app import clock, db
from app.matches import events, store

log = logging.getLogger("finalize")

# Called after the result is stored, before it is broadcast: fn(match_row, rows).
before_broadcast: list[Callable] = []
# Called after the broadcast: fn(match_id).
after_final: list[Callable] = []


def finalize(match_id: int) -> bool:
    match = store.match_by_id(match_id)
    if match is None or match["result_status"] == "final" or match["status"] != "ended":
        return False
    pr = store.players(match_id)
    if any(not p["final_checked"] for p in pr):
        return False
    pb = store.problems(match_id)
    rows, all_subs = store.standings(match)
    now = clock.now()
    team = store.is_team(match)
    with db.transaction():
        if team:
            # Every member gets the team's place and totals: a team win is a win for each of them.
            members = store.team_of(pr)
            names = store.team_names(match)
            by_team = {r.player: r for r in rows}
            for uid, t in members.items():
                r = by_team[t]
                db.execute(
                    "UPDATE match_players SET place = ?, solved = ?, penalty = ?, points = ? WHERE match_id = ? AND user_id = ?",
                    (r.place, r.solved, r.penalty, r.points, match_id, uid))
            summary = [{"team": r.player, "name": names[r.player], "place": r.place, "solved": r.solved, "penalty": r.penalty,
                        "points": r.points, "forfeited": r.forfeited,
                        "user_ids": [u for u, t in members.items() if t == r.player]} for r in rows]
            # Upsolve goes by what each member solved personally.
            rows = list(store.member_rows(match, pr, pb, all_subs).values())
        else:
            for row in rows:
                db.execute(
                    "UPDATE match_players SET place = ?, solved = ?, penalty = ?, points = ? WHERE match_id = ? AND user_id = ?",
                    (row.place, row.solved, row.penalty, row.points, match_id, row.player),
                )
            summary = [{"user_id": r.player, "place": r.place, "solved": r.solved, "penalty": r.penalty,
                        "points": r.points, "forfeited": r.forfeited} for r in rows]
        db.execute("UPDATE matches SET result_status = 'final', result = ? WHERE id = ?",
                   (json.dumps(summary), match_id))
        for row in rows:
            for key in store.player_problem_keys(pb, row.player):
                cell = row.cells.get(key)
                if cell and cell.solved:
                    continue
                solved_before = db.scalar("SELECT solved FROM touched WHERE user_id = ? AND problem_key = ?",
                                          (row.player, key))
                if solved_before:
                    continue
                db.execute("INSERT OR IGNORE INTO upsolve(user_id, problem_key, match_id, added_at) VALUES (?,?,?,?)",
                           (row.player, key, match_id, now))
        match = store.match_by_id(match_id)
        for fn in before_broadcast:
            fn(match, rows)
    events.result(match_id)
    for fn in after_final:
        try:
            fn(match_id)
        except Exception:
            log.exception("after_final hook failed")
    return True
