"""ICPC-style freeze reveal for hosted contests, run by the host and shared by every open screen.

When a contest with a freeze ends and its result is final, the frozen cells stay hidden
("waiting"). The host starts the reveal: starting from the last-placed row, one hidden cell
at a time is resolved, and the board re-ranks ("running", step k of n). Everyone watching
(big screen, players, spectators) sees the same board while it runs. The final results
(standings, timeline, result API, Events winner, profile history) unlock when the last
cell is shown or the host skips to the end ("done").

The state lives in the match settings under "reveal": {"status", "step"}. Auto-play is an
in-memory task, so a server restart just pauses it. Contests without a freeze, or whose
freeze hid nothing, never lock: their results show right away, as before.
"""

import asyncio
import json
import logging

from app import config, db
from app.core import freeze
from app.matches import store
from app.matches.service import MatchError
from app.ws.hub import hub

log = logging.getLogger("reveal")

LOCKED = ("waiting", "running")
# SQL condition (matches table aliased as m): the final result of this match may be shown.
UNLOCKED_SQL = "COALESCE(json_extract(m.settings, '$.reveal.status'), 'done') NOT IN ('waiting', 'running')"

_plans: dict[str, "Plan"] = {}             # match code -> reveal order (the result is final, so it never changes)
_rows: dict[str, tuple[int, list]] = {}    # match code -> (step, board) last built
_auto: dict[str, asyncio.Task] = {}        # match code -> auto-play task


def status(match) -> str | None:
    st = store.settings(match).get("reveal")
    return st.get("status") if st else None


def locked(match) -> bool:
    """True while the final result must stay hidden (frozen cells not revealed yet)."""
    return match["mode"] == "contest" and status(match) in LOCKED


class Plan:
    """Which hidden cell is revealed at each step, and the board after any step."""

    def __init__(self, match):
        from app.matches import view
        self.match = match
        self.pr = store.players(match["id"])
        self.pb = store.problems(match["id"])
        self.team = store.is_team(match)
        subs = store.subs(match, self.pr, self.pb)
        fs = view.freeze_start(match, store.settings(match))
        self.visible, self.hidden = freeze.split_visible(subs, None, fs)
        if self.team:
            score_team, to_team = store.team_parts(match, self.pr, self.pb)
            members = store.team_of(self.pr)
            keys = store.player_problem_keys(self.pb, -1)
            order = {t: keys for t in store.team_inputs(match, self.pr, self.pb)}
            self.targets = freeze.reveal_targets(score_team, order, to_team(subs), fs)
            self.score = lambda s: score_team(to_team(s))
            self.owner = lambda s: members.get(s.player)
        else:
            self.score = store.scorer(match, self.pr, self.pb)
            order = {p["user_id"]: store.player_problem_keys(self.pb, p["user_id"]) for p in self.pr}
            self.targets = freeze.reveal_targets(self.score, order, subs, fs)
            self.owner = lambda s: s.player
        self.labels = {}
        for p in self.pb:
            self.labels.setdefault(p["problem_key"], p["label"])

    def key(self, owner) -> str | int:
        return f"t{owner}" if self.team else owner

    def target(self, i: int) -> dict:
        owner, problem = self.targets[i]
        return {"row": self.key(owner), "label": self.labels.get(problem)}

    def board(self, step: int) -> list[dict]:
        from app.matches import view
        shown_cells = set(self.targets[:step])
        shown = list(self.visible)
        rest = []
        for h in self.hidden:
            (shown if (self.owner(h), h.problem) in shown_cells else rest).append(h)
        if self.team:
            return view.team_rows_json(self.match, self.score(shown), self.pr, self.pb, shown, rest)
        return view.rows_json(self.score(shown), self.pr, self.pb, freeze.hidden_counts(rest))


def plan(match) -> Plan:
    p = _plans.get(match["code"])
    if p is None:
        if len(_plans) > 16:
            _plans.clear()
        p = _plans[match["code"]] = Plan(match)
    return p


def board(match, step: int) -> list[dict]:
    """The shared board after `step` cells were revealed (cached: every viewer gets the same one)."""
    cached = _rows.get(match["code"])
    if cached and cached[0] == step:
        return cached[1]
    rows = plan(match).board(step)
    _rows[match["code"]] = (step, rows)
    return rows


def info(match) -> dict | None:
    """What clients need to follow the reveal: status, progress, the cell just shown and the next one."""
    if match["mode"] != "contest":
        return None
    st = store.settings(match).get("reveal")
    if not st:
        return None
    out = {"status": st["status"], "step": st["step"], "total": st.get("total", 0),
           "auto": match["code"] in _auto, "last": None, "next": None}
    if st["status"] == "running":
        p = plan(match)
        out["total"] = len(p.targets)
        if st["step"] > 0:
            out["last"] = p.target(st["step"] - 1)
        if st["step"] < len(p.targets):
            out["next"] = p.target(st["step"])
    return out


# ---------- the end of a contest ----------

def on_final(match, rows) -> None:
    """finalize hook: a contest whose freeze hid something waits for the host's reveal."""
    from app.matches import view
    if match["mode"] != "contest" or match["rules"] != "icpc":
        return
    s = store.settings(match)
    if s.get("reveal") or view.freeze_start(match, s) is None:
        return
    _plans.pop(match["code"], None)
    _rows.pop(match["code"], None)
    total = len(plan(match).targets)
    if not total:
        return  # nobody submitted during the freeze: nothing to reveal
    s["reveal"] = {"status": "waiting", "step": 0, "total": total}
    db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))


def setup() -> None:
    from app.matches import finalize
    if on_final not in finalize.before_broadcast:
        finalize.before_broadcast.append(on_final)


setup()


# ---------- host controls ----------

def _host_match(user, code: str):
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    if match["mode"] != "contest" or user is None or match["creator_id"] != user["id"]:
        raise MatchError("Only the host can run the reveal.")
    if match["status"] != "ended":
        raise MatchError("The reveal starts once the contest is over.")
    if match["result_status"] != "final":
        raise MatchError("Waiting for Codeforces to confirm the last submissions.")
    if status(match) not in LOCKED:
        raise MatchError("There is nothing left to reveal.")
    return match


def control(user, code: str, action: str) -> dict:
    """start | next | auto | pause | skip. Returns the new reveal info."""
    match = _host_match(user, code)
    st = store.settings(match)["reveal"]
    if action == "start":
        if st["status"] == "waiting":
            _save(match, "running", 0)
    elif action == "next":
        _save(match, "running", 0 if st["status"] == "waiting" else st["step"] + 1)
    elif action == "auto":
        if st["status"] == "waiting":
            _save(match, "running", 0)
        if match["code"] not in _auto:
            try:
                _auto[match["code"]] = asyncio.get_running_loop().create_task(_auto_play(match["code"]))
            except RuntimeError:
                pass  # no loop (sync callers)
            _broadcast(match["code"])
    elif action == "pause":
        if _stop_auto(match["code"]):
            _broadcast(match["code"])
    elif action == "skip":
        _save(match, "done", None)
    else:
        raise MatchError("Unknown reveal action.")
    return info(store.match_by_code(code))


def _save(match, new_status: str, step: int | None) -> None:
    s = store.settings(match)
    old = s["reveal"]
    total = len(plan(match).targets)
    if new_status == "running" and step is not None and step >= total:
        new_status, step = "done", total
    if new_status == "done":
        step = total
        _stop_auto(match["code"])
    s["reveal"] = {"status": new_status, "step": step, "total": total}
    db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match["id"]))
    _broadcast(match["code"], full=old["status"] != new_status)
    if new_status == "done":
        _plans.pop(match["code"], None)
        _rows.pop(match["code"], None)


def _broadcast(code: str, full: bool = False) -> None:
    """Every screen gets the same board. A status change also rebuilds each viewer's full state."""
    from app.matches import events
    match = store.match_by_code(code)
    data = info(match)
    rows = board(match, data["step"]) if data["status"] in ("running", "done") else None
    hub.send_match(code, "match.reveal", {"reveal": data, "standings": rows})
    if full:
        events.state(match["id"])


def _stop_auto(code: str) -> bool:
    task = _auto.pop(code, None)
    if task is None:
        return False
    if task is not asyncio.current_task():
        task.cancel()
    return True


async def _auto_play(code: str) -> None:
    try:
        while True:
            await asyncio.sleep(config.REVEAL_AUTO_S)
            match = store.match_by_code(code)
            if match is None or status(match) != "running":
                break
            _save(match, "running", store.settings(match)["reveal"]["step"] + 1)
    except Exception:
        log.exception("reveal auto-play failed")
    finally:
        if _auto.get(code) is asyncio.current_task():
            _auto.pop(code, None)
