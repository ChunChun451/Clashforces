"""Fetch a user's Codeforces history to know which problems they've touched.

First time: one full user.status call. After that: incremental pages until we
reach a submission we've already seen.
"""

from app import config, db
from app.cf.client import Lane, get_client
from app.cf.parse import parse_submissions
from app.poller import ingest

MAX_PAGES = 20


async def sync(user_id: int, lane: Lane = Lane.INTERACTIVE) -> None:
    user = db.one("SELECT handle, history_synced, last_sub_id FROM users WHERE id = ?", (user_id,))
    if user is None or user["handle"] is None:
        return
    cf = get_client()
    handle = user["handle"]
    if not user["history_synced"]:
        raw = await cf.call("user.status", {"handle": handle}, lane=lane, timeout=config.CF_LONG_TIMEOUT_S)
        subs = parse_submissions(raw, handle)
        ingest.mark_touched(user_id, subs)
        top = max((s.id for s in subs), default=0)
        db.execute("UPDATE users SET history_synced = 1, last_sub_id = MAX(last_sub_id, ?) WHERE id = ?",
                   (top, user_id))
        return
    known = user["last_sub_id"]
    start = 1
    for _ in range(MAX_PAGES):
        raw = await cf.call("user.status", {"handle": handle, "from": start, "count": config.POLL_PAGE_COUNT},
                            lane=lane)
        subs = parse_submissions(raw, handle)
        fresh = [s for s in subs if s.id > known]
        ingest.mark_touched(user_id, fresh)
        if fresh:
            db.execute("UPDATE users SET last_sub_id = MAX(last_sub_id, ?) WHERE id = ?",
                       (max(s.id for s in fresh), user_id))
        if len(fresh) < len(subs) or len(raw) < config.POLL_PAGE_COUNT:
            return
        start += config.POLL_PAGE_COUNT
