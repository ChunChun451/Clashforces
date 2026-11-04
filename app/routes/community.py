"""The Players page: members, live matches and recent results, and challenges."""

from fastapi import APIRouter, Depends, HTTPException

from app import community
from app.auth import session

router = APIRouter(prefix="/api")


@router.get("/players")
async def players(me=Depends(session.current_user)):
    return {"players": community.players(me["id"] if me else None)}


@router.get("/activity")
async def activity():
    return community.activity()


@router.get("/challenges")
async def challenges(me=Depends(session.require_user)):
    return community.lists(me["id"])


@router.post("/challenges/{challenge_id}/decline")
async def decline(challenge_id: int, me=Depends(session.require_user)):
    try:
        community.decline(me, challenge_id)
    except community.ChallengeError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
