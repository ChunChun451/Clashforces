"""Collect a user's platform history and feed it to core/stats."""

import json

from app import db
from app.core import stats
from app.matches import store


def finished_matches(user_id: int):
    return db.all(
        "SELECT m.*, mp.place AS my_place FROM matches m JOIN match_players mp ON mp.match_id = m.id"
        " WHERE mp.user_id = ? AND m.result_status = 'final' ORDER BY m.ended_at", (user_id,))


def outcomes_and_solves(user_id: int) -> tuple[list, list]:
    outcomes, solves = [], []
    for m in finished_matches(user_id):
        places = [r["place"] for r in db.all("SELECT place FROM match_players WHERE match_id = ?", (m["id"],))]
        pb = store.problems(m["id"])
        mine = store.player_problem_keys(pb, user_id)
        tags = set()
        info = {}
        for p in pb:
            if p["problem_key"] in mine:
                t = json.loads(p["tags"]) if p["tags"] else []
                tags |= set(t)
                info[p["problem_key"]] = (p["rating"], tuple(t))
        outcomes.append(stats.MatchOutcome(m["id"], m["mode"], m["my_place"],
                                           places.count(1) > 1 and m["my_place"] == 1, frozenset(tags)))
        rows, _ = store.standings(m)
        row = next((r for r in rows if r.player == user_id), None)
        if row:
            for key, cell in row.cells.items():
                if cell.solved and key in info:
                    solves.append(stats.Solve(info[key][0], info[key][1], cell.minute))
    return outcomes, solves


def user_stats(user_id: int) -> dict:
    outcomes, solves = outcomes_and_solves(user_id)
    return {
        "record": stats.record(outcomes),
        "solve_times": stats.solve_times(solves),
        "tag_win_rates": stats.tag_win_rates(outcomes),
        "matches_played": len(outcomes),
    }


def rating_history(user_id: int) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT h.before, h.after, h.created_at, m.code FROM rating_history h JOIN matches m ON m.id = h.match_id"
        " WHERE h.user_id = ? ORDER BY h.created_at, h.id", (user_id,))]


def recent_matches(user_id: int, limit: int = 20) -> list[dict]:
    out = []
    for m in reversed(finished_matches(user_id)[-limit:]):
        opp = db.all("SELECT handle, cf_rating, place FROM match_players WHERE match_id = ? AND user_id != ?",
                     (m["id"], user_id))
        out.append({"code": m["code"], "mode": m["mode"], "rules": m["rules"], "place": m["my_place"],
                    "players": len(opp) + 1, "ended_at": m["ended_at"],
                    "opponents": [dict(o) for o in opp][:9]})
    return out


def one_v_one_places(user_id: int) -> dict[int, int]:
    return {r["match_id"]: r["place"] for r in db.all(
        "SELECT mp.match_id, mp.place FROM match_players mp JOIN matches m ON m.id = mp.match_id"
        " WHERE mp.user_id = ? AND m.mode IN ('friend', 'ranked') AND m.result_status = 'final'", (user_id,))}


def h2h(a: int, b: int) -> dict:
    mine, theirs = one_v_one_places(a), one_v_one_places(b)
    res = stats.head_to_head(mine, theirs)
    shared = sorted(set(mine) & set(theirs), reverse=True)[:10]
    res["matches"] = [{"code": db.scalar("SELECT code FROM matches WHERE id = ?", (mid,)),
                       "result": "win" if mine[mid] < theirs[mid] else "loss" if mine[mid] > theirs[mid] else "draw"}
                      for mid in shared]
    return res
