"""Check that players' handles still exist on Codeforces (they can be renamed)."""

from app import db
from app.auth import users
from app.cf.client import HandleNotFound, Lane, get_client


async def check_players(user_ids: list[int]) -> list[str]:
    """Batched user.info for these users. Refreshes ratings.

    Returns the handles that no longer exist (those users are marked
    needs_reverify). An empty list means everyone is fine. Raises CFError if
    Codeforces can't be reached.
    """
    rows = [users.by_id(uid) for uid in user_ids]
    missing = [f"user #{r['id']}" for r in rows if r["handle"] is None]
    pending = {r["handle"].lower(): r for r in rows if r["handle"] is not None}
    for r in rows:
        if r["handle"] is None:
            users.mark_needs_reverify(r["id"])
    while pending:
        handles = ";".join(r["handle"] for r in pending.values())
        try:
            infos = await get_client().call("user.info", {"handles": handles}, lane=Lane.URGENT)
        except HandleNotFound as e:
            row = pending.pop(e.handle.lower(), None)
            if row is None:  # can't tell which one; block everyone rather than guess
                missing.extend(r["handle"] for r in pending.values())
                break
            users.mark_needs_reverify(row["id"])
            missing.append(row["handle"])
            continue
        for info in infos:
            row = pending.get(info["handle"].lower())
            if row is None:
                continue
            db.execute(
                "UPDATE users SET handle = ?, cf_rating = ?, cf_max_rating = ? WHERE id = ?",
                (info["handle"], info.get("rating", 0), info.get("maxRating", 0), row["id"]),
            )
        break
    return missing
