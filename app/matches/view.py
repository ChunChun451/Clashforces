"""Build the JSON a viewer sees for a match (freeze-aware)."""

import json
from collections import Counter

from app import clock
from app.core import freeze
from app.core import team as team_core
from app.matches import store
from app.models import Row


def freeze_start(match, s: dict) -> int | None:
    if match["rules"] != "icpc" or not s.get("freeze_min") or match["end_time"] is None:
        return None
    return match["end_time"] - s["freeze_min"] * 60


def freeze_active(match, s: dict) -> bool:
    """True while viewers should get the frozen view (until the result is final)."""
    fs = freeze_start(match, s)
    return fs is not None and clock.now() >= fs and match["result_status"] != "final"


def cell_json(cell, label: str, frozen_tries: int = 0) -> dict:
    if cell is None:
        if frozen_tries:
            return {"label": label, "state": "frozen", "tries": 0, "frozen_tries": frozen_tries}
        return {"label": label, "state": "none"}
    out = {"label": label, "tries": cell.tries}
    if cell.solved:
        out.update(state="ok", minute=cell.minute, time=cell.solved_at)
    elif cell.pending:
        out["state"] = "testing"
    elif frozen_tries:
        out["state"] = "frozen"
    elif cell.tries:
        out["state"] = "wrong"
    else:
        out["state"] = "none"
    if frozen_tries and not cell.solved:
        out["state"] = "frozen"
        out["frozen_tries"] = frozen_tries
    if cell.first or cell.points:
        out.update(first=cell.first, points=round(cell.points, 2))
    return out


def rows_json(rows: list[Row], player_rows, problem_rows, hidden=None) -> list[dict]:
    info = {p["user_id"]: p for p in player_rows}
    hidden = hidden or {}
    out = []
    for row in rows:
        p = info[row.player]
        labels = {pb["problem_key"]: pb["label"] for pb in problem_rows
                  if pb["problem_key"] in store.player_problem_keys(problem_rows, row.player)}
        cells = []
        for key, label in sorted(labels.items(), key=lambda kv: kv[1]):
            cells.append(cell_json(row.cells.get(key), label, hidden.get((row.player, key), 0)))
        out.append({
            "user_id": row.player,
            "handle": p["handle"] or p["current_handle"],
            "cf_rating": p["cf_rating"] or p["current_rating"],
            "place": row.place,
            "solved": row.solved,
            "penalty": row.penalty,
            "points": round(row.points, 2),
            "forfeited": row.forfeited,
            "cells": cells,
        })
    return out


def team_rows_json(match, rows: list[Row], player_rows, problem_rows, member_subs, hidden_subs=()) -> list[dict]:
    """Team standings: one row per team with its members underneath.

    member_subs are the submissions this viewer may see; hidden_subs only add
    frozen-try counts. Each solved team cell says which member solved it.
    """
    info = {p["user_id"]: p for p in player_rows}
    members = store.team_of(player_rows)
    ends = store.member_end(match, player_rows)
    labels = sorted(((pb["problem_key"], pb["label"]) for pb in problem_rows if pb["user_id"] is None),
                    key=lambda kv: kv[1])
    who = team_core.solvers(member_subs, members, ends, match["start_time"])
    hidden_team = Counter((members[s.player], s.problem) for s in hidden_subs if s.player in members)
    hidden_member = freeze.hidden_counts(list(hidden_subs))
    mrows = store.member_rows(match, player_rows, problem_rows, member_subs)

    def handle(uid):
        p = info[uid]
        return p["handle"] or p["current_handle"]

    out = []
    for row in rows:
        t = row.player
        cells = []
        for key, label in labels:
            c = cell_json(row.cells.get(key), label, hidden_team.get((t, key), 0))
            if c["state"] == "ok" and (t, key) in who:
                c["by"] = who[(t, key)]
                c["by_handle"] = handle(who[(t, key)])
            cells.append(c)
        mem = []
        for p in player_rows:
            if members.get(p["user_id"]) != t:
                continue
            mr = mrows.get(p["user_id"])
            mem.append({
                "user_id": p["user_id"],
                "handle": handle(p["user_id"]),
                "cf_rating": p["cf_rating"] or p["current_rating"],
                "forfeited": p["forfeited_at"] is not None,
                "solved": mr.solved if mr else 0,
                "cells": [cell_json(mr.cells.get(key) if mr else None, label,
                                    hidden_member.get((p["user_id"], key), 0)) for key, label in labels],
            })
        out.append({
            "key": f"t{t}",
            "user_id": f"t{t}",
            "team": t,
            "name": team_core.TEAM_NAMES[t],
            "place": row.place,
            "solved": row.solved,
            "penalty": row.penalty,
            "points": round(row.points, 2),
            "forfeited": row.forfeited,
            "cells": cells,
            "members": mem,
        })
    return out


def lobby_blocker(match, s: dict, player_rows) -> str | None:
    """Why the match can't start yet (team mode), or None."""
    if not store.is_team(match):
        return None
    return team_core.can_start(store.team_of(player_rows) | {p["user_id"]: -1 for p in player_rows
                                                             if p["team"] not in (0, 1)},
                               s.get("team_size", 2))


def problem_json(pb, show_tags: bool = True) -> dict:
    tags = json.loads(pb["tags"]) if pb["tags"] else []
    return {
        "label": pb["label"],
        "key": pb["problem_key"],
        "name": pb["name"] or pb["problem_key"],
        "rating": pb["rating"],
        "tags": tags if show_tags else [],
        "points": pb["points"],
        "owner": pb["user_id"],
        "url": f"https://codeforces.com/contest/{pb['contest_id']}/problem/{pb['idx']}" if pb["contest_id"] else None,
        "contest_url": f"https://codeforces.com/contest/{pb['contest_id']}" if pb["contest_id"] else None,
    }


def build(match, viewer: int | None) -> dict:
    s = store.settings(match)
    pr = store.players(match["id"])
    pb = store.problems(match["id"])
    now = clock.now()
    me = next((p for p in pr if p["user_id"] == viewer), None)
    revealed = match["start_time"] is not None and now >= match["start_time"] and match["status"] in ("running", "ended")

    state = {
        "code": match["code"],
        "mode": match["mode"],
        "rules": match["rules"],
        "status": match["status"],
        "result_status": match["result_status"],
        "server_time": now,
        "start_time": match["start_time"],
        "end_time": match["end_time"],
        "ended_at": match["ended_at"],
        "end_reason": match["end_reason"],
        "creator_id": match["creator_id"],
        "settings": public_settings(s),
        "freeze_start": freeze_start(match, s),
        "me": None if me is None else {
            "user_id": viewer, "ready": bool(me["ready"]), "end_time": me["end_time"],
            "forfeited": me["forfeited_at"] is not None,
        },
        "viewer_id": viewer,
        "players": [{
            "user_id": p["user_id"],
            "handle": p["handle"] or p["current_handle"],
            "cf_rating": p["cf_rating"] or p["current_rating"],
            "duel_rating": p["duel_rating"],
            "ready": bool(p["ready"]),
            "end_time": p["end_time"],
            "penalty_bonus": p["penalty_bonus"],
            "rating_min": p["rating_min"],
            "rating_max": p["rating_max"],
            "forfeited": p["forfeited_at"] is not None,
            "needs_reverify": bool(p["needs_reverify"]),
            "rating_before": p["rating_before"],
            "rating_after": p["rating_after"],
            "team": p["team"],
        } for p in pr],
        "problems": [problem_json(x) for x in pb] if revealed else [],
        "standings": [],
        "rematch_code": rematch_code(match),
    }
    if store.is_team(match):
        state["team_size"] = s.get("team_size", 2)
        state["teams"] = [{"team": t, "name": team_core.TEAM_NAMES[t],
                           "user_ids": [p["user_id"] for p in pr if p["team"] == t]} for t in (0, 1)]
        state["my_team"] = me["team"] if me else None
        if match["status"] in ("lobby", "starting"):
            state["start_blocked"] = lobby_blocker(match, s, pr)
    if not revealed:
        return state

    score = store.scorer(match, pr, pb)
    all_subs = store.subs(match, pr, pb)
    if store.is_team(match):
        if freeze_active(match, s):
            mates = {p["user_id"] for p in pr if me and p["team"] == me["team"]} or None
            visible, hidden = freeze.split_visible(all_subs, mates, freeze_start(match, s))
            state["frozen"] = True
        else:
            visible, hidden = all_subs, []
            state["frozen"] = False
        state["standings"] = team_rows_json(match, score(visible), pr, pb, visible, hidden)
        return state
    if freeze_active(match, s):
        visible, hidden = freeze.split_visible(all_subs, viewer if me else None, freeze_start(match, s))
        state["frozen"] = True
        state["standings"] = rows_json(score(visible), pr, pb, freeze.hidden_counts(hidden))
    else:
        state["frozen"] = False
        state["standings"] = rows_json(score(all_subs), pr, pb)
    return state


# Only these settings are sent to browsers. An allow-list, so private keys stored by
# older versions (or added later) never leak.
PUBLIC_SETTING_KEYS = ("count", "rating_min", "rating_max", "tags_include", "tags_avoid", "avoid_interactive",
                       "custom_problems", "duration_min", "freeze_min", "handicap", "team_size")


def public_settings(s: dict) -> dict:
    return {k: s[k] for k in PUBLIC_SETTING_KEYS if k in s}


def rematch_code(match) -> str | None:
    from app import db
    return db.scalar("SELECT code FROM matches WHERE rematch_of = ? ORDER BY id DESC LIMIT 1", (match["id"],))


def result_payload(match) -> dict:
    """Final standings plus the freeze reveal sequence (if the match had a freeze)."""
    s = store.settings(match)
    pr = store.players(match["id"])
    pb = store.problems(match["id"])
    if store.is_team(match):
        return _team_result_payload(match, s, pr, pb)
    score = store.scorer(match, pr, pb)
    all_subs = store.subs(match, pr, pb)
    fs = freeze_start(match, s)
    payload = {"rows": rows_json(score(all_subs), pr, pb), "reveal": None}
    if fs is not None:
        visible, hidden = freeze.split_visible(all_subs, None, fs)
        order = {p["user_id"]: store.player_problem_keys(pb, p["user_id"]) for p in pr}
        steps = freeze.reveal_steps(score, order, all_subs, fs)
        if steps:
            labels = {(p, k): lab for p in order for k, lab in
                      ((x["problem_key"], x["label"]) for x in pb if x["problem_key"] in order[p])}
            remaining = freeze.hidden_counts(hidden)
            out_steps = []
            for st in steps:
                remaining.pop((st["player"], st["problem"]), None)
                out_steps.append({"user_id": st["player"], "label": labels.get((st["player"], st["problem"])),
                                  "rows": rows_json(st["rows"], pr, pb, remaining)})
            payload["reveal"] = {
                "start": rows_json(score(visible), pr, pb, freeze.hidden_counts(hidden)),
                "steps": out_steps,
            }
    payload["players"] = [{"user_id": p["user_id"], "rating_before": p["rating_before"],
                           "rating_after": p["rating_after"]} for p in pr]
    return payload


def _team_result_payload(match, s: dict, pr, pb) -> dict:
    """Team version: rows are teams. The reveal runs per team, ICPC style."""
    score_team_subs, to_team = store.team_parts(match, pr, pb)
    all_subs = store.subs(match, pr, pb)
    rows = score_team_subs(to_team(all_subs))
    payload = {"rows": team_rows_json(match, rows, pr, pb, all_subs), "reveal": None, "players": []}
    fs = freeze_start(match, s)
    if fs is None:
        return payload
    visible, hidden = freeze.split_visible(all_subs, None, fs)
    keys = store.player_problem_keys(pb, -1)
    steps = freeze.reveal_steps(score_team_subs, {0: keys, 1: keys}, to_team(all_subs), fs)
    if not steps:
        return payload
    members = store.team_of(pr)
    labels = {x["problem_key"]: x["label"] for x in pb if x["user_id"] is None}
    shown = list(visible)
    out_steps = []
    for st in steps:
        # move this team's hidden subs on that problem into view
        moved = [h for h in hidden if members.get(h.player) == st["player"] and h.problem == st["problem"]]
        shown += moved
        hidden = [h for h in hidden if h not in moved]
        out_steps.append({"user_id": f"t{st['player']}", "label": labels.get(st["problem"]),
                          "rows": team_rows_json(match, st["rows"], pr, pb, shown, hidden)})
    _, start_hidden = freeze.split_visible(all_subs, None, fs)
    payload["reveal"] = {
        "start": team_rows_json(match, score_team_subs(to_team(visible)), pr, pb, visible, start_hidden),
        "steps": out_steps,
    }
    return payload
