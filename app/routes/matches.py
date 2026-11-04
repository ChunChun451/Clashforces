from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import session
from app.cf import cache
from app.cf.parse import is_gym, submission_url
from app.core import custom_list, selection
from app.matches import contest, draft, reveal, service, store, view
from app.matches import settings as settings_mod
from app.matches.service import MatchError
from app.poller import ingest

router = APIRouter(prefix="/api")


class CreateIn(BaseModel):
    mode: str = "friend"
    rules: str = "icpc"
    settings: dict = {}


class ReadyIn(BaseModel):
    ready: bool = True


class SettingsIn(BaseModel):
    rules: str | None = None
    settings: dict


class TeamIn(BaseModel):
    team: int


class ArrangeIn(BaseModel):
    how: str  # shuffle | balance


class JoinIn(BaseModel):
    team: int | None = None  # 0-based; per-team invite links (/m/CODE?team=2) are 1-based in the URL


class MoveIn(BaseModel):
    user_id: int
    team: int | None = None  # None = back to the draft pool


class RenameIn(BaseModel):
    name: str = ""


class ValidateIn(BaseModel):
    text: str
    match_code: str | None = None


class CaptainIn(BaseModel):
    team: int
    user_id: int | None = None


class PickIn(BaseModel):
    user_id: int


class StartIn(BaseModel):
    force: bool = False


class ExtendIn(BaseModel):
    minutes: int


class AnnounceIn(BaseModel):
    body: str


class PlayerIn(BaseModel):
    user_id: int


def _run(fn, *args):
    try:
        return fn(*args)
    except MatchError as e:
        raise HTTPException(400, str(e))


def _match_or_404(code: str):
    match = store.match_by_code(code)
    if match is None:
        raise HTTPException(404, "No match with that code.")
    return match


@router.post("/matches")
async def create(body: CreateIn, user=Depends(session.require_user)):
    return {"code": _run(service.create, user, body.mode, body.rules, body.settings)}  # contests: POST /contests


@router.get("/matches/{code}")
async def get_match(code: str, user=Depends(session.current_user)):
    match = _match_or_404(code)
    return view.build(match, user["id"] if user else None)


@router.post("/matches/{code}/join")
async def join(code: str, body: JoinIn | None = None, user=Depends(session.require_user)):
    _run(service.join, user, code, body.team if body else None)
    return {"ok": True}


@router.post("/matches/{code}/leave")
async def leave(code: str, user=Depends(session.require_user)):
    _run(service.leave, user, code)
    return {"ok": True}


@router.patch("/matches/{code}/settings")
async def update_settings(code: str, body: SettingsIn, user=Depends(session.require_user)):
    raw = dict(body.settings)
    if body.rules:
        raw["rules"] = body.rules
    _run(service.update_settings, user, code, raw)
    return {"ok": True}


@router.post("/matches/{code}/ready")
async def ready(code: str, body: ReadyIn, user=Depends(session.require_user)):
    _run(service.set_ready, user, code, body.ready)
    return {"ok": True}


@router.post("/matches/{code}/team")
async def switch_team(code: str, body: TeamIn, user=Depends(session.require_user)):
    _run(service.switch_team, user, code, body.team)
    return {"ok": True}


@router.post("/matches/{code}/teams")
async def arrange_teams(code: str, body: ArrangeIn, user=Depends(session.require_user)):
    _run(service.arrange_teams, user, code, body.how)
    return {"ok": True}


@router.post("/matches/{code}/move")
async def move_player(code: str, body: MoveIn, user=Depends(session.require_user)):
    _run(service.move_player, user, code, body.user_id, body.team)
    return {"ok": True}


@router.put("/matches/{code}/teams/{idx}/name")
async def rename_team(code: str, idx: int, body: RenameIn, user=Depends(session.require_user)):
    return {"name": _run(service.rename_team, user, code, idx, body.name)}


@router.post("/matches/{code}/captain")
async def set_captain(code: str, body: CaptainIn, user=Depends(session.require_user)):
    _run(draft.set_captain, user, code, body.team, body.user_id)
    return {"ok": True}


@router.post("/matches/{code}/draft/start")
async def start_draft(code: str, user=Depends(session.require_user)):
    _run(draft.start, user, code)
    return {"ok": True}


@router.post("/matches/{code}/draft/pick")
async def draft_pick(code: str, body: PickIn, user=Depends(session.require_user)):
    _run(draft.pick, user, code, body.user_id)
    return {"ok": True}


# ---------- hosted contests ----------

@router.post("/contests")
async def create_contest(body: dict, user=Depends(session.require_user)):
    return {"code": _run(contest.create, user, body)}


@router.get("/contests/{code}/edit")
async def contest_edit_form(code: str, user=Depends(session.require_user)):
    return _run(contest.editable, user, code)


@router.patch("/contests/{code}")
async def update_contest(code: str, body: dict, user=Depends(session.require_user)):
    _run(contest.update, user, code, body)
    return {"ok": True}


@router.post("/contests/{code}/start")
async def start_contest(code: str, body: StartIn | None = None, user=Depends(session.require_user)):
    _run(contest.start_now, user, code, bool(body and body.force))
    return {"ok": True}


@router.post("/contests/{code}/extend")
async def extend_contest(code: str, body: ExtendIn, user=Depends(session.require_user)):
    return {"end_time": _run(contest.extend, user, code, body.minutes)}


@router.post("/contests/{code}/end")
async def end_contest(code: str, user=Depends(session.require_user)):
    _run(contest.end_early, user, code)
    return {"ok": True}


@router.post("/contests/{code}/cancel")
async def cancel_contest(code: str, user=Depends(session.require_user)):
    _run(contest.cancel, user, code)
    return {"ok": True}


@router.post("/contests/{code}/announcements")
async def announce(code: str, body: AnnounceIn, user=Depends(session.require_user)):
    return _run(contest.announce, user, code, body.body)


@router.post("/contests/{code}/kick")
async def kick(code: str, body: PlayerIn, user=Depends(session.require_user)):
    _run(contest.kick, user, code, body.user_id)
    return {"ok": True}


@router.post("/contests/{code}/allow")
async def allow(code: str, body: PlayerIn, user=Depends(session.require_user)):
    _run(contest.allow, user, code, body.user_id)
    return {"ok": True}


class RevealIn(BaseModel):
    action: str  # start | next | auto | pause | skip


@router.post("/contests/{code}/reveal")
async def reveal_control(code: str, body: RevealIn, user=Depends(session.require_user)):
    return _run(reveal.control, user, code, body.action)


@router.get("/events")
async def events_listing(user=Depends(session.current_user)):
    return contest.listing(user["id"] if user else None)


@router.post("/matches/{code}/submitted")
async def submitted(code: str, user=Depends(session.require_user)):
    queued = _run(service.hint, user, code)
    return {"queued": queued}


@router.post("/matches/{code}/forfeit")
async def forfeit(code: str, user=Depends(session.require_user)):
    _run(service.forfeit, user, code)
    return {"ok": True}


@router.post("/matches/{code}/rematch")
async def rematch(code: str, user=Depends(session.require_user)):
    return {"code": _run(service.rematch, user, code)}


@router.get("/matches/{code}/result")
async def result(code: str):
    match = _match_or_404(code)
    if match["result_status"] != "final":
        raise HTTPException(409, "The result is not final yet.")
    if reveal.locked(match):
        raise HTTPException(409, "The host reveals the final results first.")
    return view.result_payload(match)


@router.get("/matches/{code}/timeline")
async def timeline(code: str, user=Depends(session.current_user)):
    match = _match_or_404(code)
    if match["status"] != "ended" or match["result_status"] != "final":
        raise HTTPException(409, "The timeline appears once the result is confirmed.")
    if reveal.locked(match):
        raise HTTPException(409, "The timeline appears once the host has revealed the results.")
    pr = store.players(match["id"])
    pb = store.problems(match["id"])
    labels = {}
    for p in pb:
        labels[(p["user_id"], p["problem_key"])] = p["label"]
    names = {p["user_id"]: (p["handle"], p["cf_rating"]) for p in pr}
    team = store.team_of(pr) if store.is_team(match) else {}
    team_names = store.team_names(match) if team else []
    items = []
    for s in store.subs(match, pr, pb):
        label = labels.get((s.player, s.problem)) or labels.get((None, s.problem))
        end = next((p["end_time"] for p in pr if p["user_id"] == s.player), None)
        if end is None or s.player not in names or s.time >= end or label is None:
            continue
        p = cache.get(s.problem)
        items.append({
            "id": s.id, "user_id": s.player, "handle": names[s.player][0], "cf_rating": names[s.player][1],
            "label": label, "problem_key": s.problem, "verdict": s.verdict, "time": s.time,
            "minute": (s.time - match["start_time"]) // 60, "team": team.get(s.player),
            "team_name": team_names[team[s.player]] if s.player in team else None,
            "url": submission_url(p.contest_id, s.id) if p else None,
        })
    return {"start_time": match["start_time"], "items": items}


# ---------- problem helpers for the create form ----------

@router.get("/tags")
async def tags():
    return {"tags": cache.all_tags()}


@router.post("/problems/preview")
async def preview(body: dict, user=Depends(session.current_user)):
    """Dry-run selection for the current user so the form can warn early."""
    try:
        s = settings_mod.clean(body, body.get("rules", "icpc"), "friend")
    except settings_mod.SettingsError as e:
        return {"ok": False, "message": str(e)}
    if s.get("custom_problems"):
        return {"ok": True}
    touched = ingest.touched_groups(user["id"]) if user else set()
    try:
        pool = selection.filtered_pool(cache.problems(), cache.running_contest_ids(), touched,
                                       settings_mod.filters(s), who="you")
    except selection.SelectionError as e:
        return {"ok": False, "message": e.message, "stage": e.stage}
    return {"ok": True, "available": len({p.group for p in pool})}


@router.post("/problems/validate")
async def validate(body: ValidateIn, user=Depends(session.current_user)):
    touched = {}
    if body.match_code:
        match = store.match_by_code(body.match_code)
        if match and match["mode"] == "contest" and not (user and match["creator_id"] == user["id"]):
            # who has tried what would hint at a contest's hidden problems
            raise HTTPException(403, "Only the host can check problems for this contest.")
        if match:
            for p in store.players(match["id"]):
                touched[p["handle"] or p["current_handle"]] = ingest.touched_groups(p["user_id"])
    elif user:
        touched[user["handle"]] = ingest.touched_groups(user["id"])
    by_key = {p.key: p for p in cache.problems()}
    res = custom_list.check(body.text, by_key, cache.running_contest_ids(), touched)
    return {
        "problems": [{"key": p.key, "name": p.name, "rating": p.rating, "gym": is_gym(p.contest_id)}
                     for p in res.problems],
        "errors": res.errors,
        "warnings": [f"{who} has already tried {key}" for who, key in res.warnings],
    }


@router.get("/me/active")
async def my_active(user=Depends(session.require_user)):
    row = db.one("SELECT l.kind, m.code FROM active_locks l LEFT JOIN matches m ON m.id = l.match_id"
                 " WHERE l.user_id = ?", (user["id"],))
    return {"active": dict(row) if row else None}
