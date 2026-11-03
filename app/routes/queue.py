from fastapi import APIRouter, Depends, HTTPException

from app.auth import session
from app.matches import queue

router = APIRouter(prefix="/api")


@router.post("/queue/join")
async def join(user=Depends(session.require_user)):
    try:
        queue.join(user)
    except queue.QueueError as e:
        raise HTTPException(400, str(e))
    return queue.status(user["id"])


@router.post("/queue/leave")
async def leave(user=Depends(session.require_user)):
    queue.leave(user["id"])
    return queue.status(user["id"])


@router.get("/queue")
async def status(user=Depends(session.require_user)):
    return queue.status(user["id"])
