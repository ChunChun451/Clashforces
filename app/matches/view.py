"""Build the JSON a viewer sees for a match (freeze-aware)."""

import json

from app import clock
from app.core import freeze
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
        } for p in pr],
        "problems": [problem_json(x) for x in pb] if revealed else [],
        "standings": [],
        "rematch_code": rematch_code(match),
    }
    if not revealed:
        return state

    score = store.scorer(match, pr, pb)
    all_subs = store.subs(match, pr, pb)
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
                       "custom_problems", "duration_min", "freeze_min", "handicap")


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
