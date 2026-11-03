"""Collect a user's platform history and feed it to core/stats."""

import json

from app import db
from app.core import stats
from app.matches import store


def finished_matches(user_id: int):
    return db.all(
        "SELECT m.*, mp.place AS my_place, mp.team AS my_team FROM matches m JOIN match_players mp ON mp.match_id = m.id"
        " WHERE mp.user_id = ? AND m.result_status = 'final' ORDER BY m.ended_at", (user_id,))


def outcomes_and_solves(user_id: int) -> tuple[list, list]:
    outcomes, solves = [], []
    for m in finished_matches(user_id):
        if m["mode"] == "team":
            # one place per team, so a team win isn't mistaken for a shared first place
            places = [r["place"] for r in db.all("SELECT DISTINCT team, place FROM match_players WHERE match_id = ?",
                                                 (m["id"],))]
        else:
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
        if m["mode"] == "team":
            pr = store.players(m["id"])
            row = store.member_rows(m, pr, pb, store.subs(m, pr, pb)).get(user_id)
        else:
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
        others = db.all("SELECT handle, cf_rating, place, team FROM match_players WHERE match_id = ? AND user_id != ?",
                        (m["id"], user_id))
        item = {"code": m["code"], "mode": m["mode"], "rules": m["rules"], "place": m["my_place"],
                "players": len(others) + 1, "ended_at": m["ended_at"]}
        if m["mode"] == "team":
            item["players"] = 2  # two teams: place is 1/2 (or 1/2 for both on a draw)
            item["teammates"] = [_person(o) for o in others if o["team"] == m["my_team"]]
            item["opponents"] = [_person(o) for o in others if o["team"] != m["my_team"]]
        else:
            item["opponents"] = [_person(o) for o in others][:9]
        if m["mode"] != "group":
            item["result"] = _result(m)
        out.append(item)
    return out


def _person(row) -> dict:
    return {"handle": row["handle"], "cf_rating": row["cf_rating"], "place": row["place"]}


def _result(m) -> str:
    """win / loss / draw for two-sided matches (1v1 and team). A draw is a shared first place."""
    if m["my_place"] != 1:
        return "loss"
    sides = db.scalar("SELECT COUNT(DISTINCT COALESCE(team, user_id)) FROM match_players WHERE match_id = ? AND place = 1"
                      if m["mode"] == "team" else
                      "SELECT COUNT(*) FROM match_players WHERE match_id = ? AND place = 1", (m["id"],))
    return "draw" if sides > 1 else "win"


def one_v_one_places(user_id: int) -> dict[int, int]:
    return {r["match_id"]: r["place"] for r in db.all(
        "SELECT mp.match_id, mp.place FROM match_players mp JOIN matches m ON m.id = mp.match_id"
        " WHERE mp.user_id = ? AND m.mode IN ('friend', 'ranked') AND m.result_status = 'final'", (user_id,))}


def opposing_team_places(a: int, b: int) -> tuple[dict[int, int], dict[int, int]]:
    """Team matches where a and b were on opposite sides: match_id -> place, for each of them."""
    rows = db.all(
        "SELECT pa.match_id, pa.place AS pa, pb.place AS pb FROM match_players pa"
        " JOIN match_players pb ON pb.match_id = pa.match_id AND pb.user_id = ?"
        " JOIN matches m ON m.id = pa.match_id"
        " WHERE pa.user_id = ? AND m.mode = 'team' AND m.result_status = 'final' AND pa.team != pb.team",
        (b, a))
    return {r["match_id"]: r["pa"] for r in rows}, {r["match_id"]: r["pb"] for r in rows}


def h2h(a: int, b: int) -> dict:
    """1v1 matches, plus team matches where the two were on opposite teams."""
    mine, theirs = one_v_one_places(a), one_v_one_places(b)
    team_a, team_b = opposing_team_places(a, b)
    mine.update(team_a)
    theirs.update(team_b)
    res = stats.head_to_head(mine, theirs)
    shared = sorted(set(mine) & set(theirs), reverse=True)[:10]
    res["matches"] = [{"code": db.scalar("SELECT code FROM matches WHERE id = ?", (mid,)),
                       "result": "win" if mine[mid] < theirs[mid] else "loss" if mine[mid] > theirs[mid] else "draw"}
                      for mid in shared]
    return res
