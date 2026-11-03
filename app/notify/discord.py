"""Post a final result to Discord webhooks (the room's and each player's)."""

import asyncio
import logging

import httpx

from app import config, db
from app.matches import store

log = logging.getLogger("discord")

MODE_NAMES = {"friend": "friend duel", "group": "group room", "ranked": "ranked duel"}


def message(match) -> str:
    rows = sorted(store.players(match["id"]), key=lambda p: (p["place"] or 99, p["handle"]))
    rules = "ICPC" if match["rules"] == "icpc" else "Lockout"
    link = f"{config.PUBLIC_URL}/m/{match['code']}"
    if match["rules"] == "lockout":
        score = lambda p: f"{p['points']:g} pts"  # noqa: E731
    else:
        score = lambda p: f"{p['solved']} solved, {p['penalty']} penalty"  # noqa: E731
    top = [p for p in rows if p["place"] == 1]
    if len(top) > 1:
        head = "Draw between " + ", ".join(f"**{p['handle']}**" for p in top)
    else:
        head = f"**{top[0]['handle']}** won" if top else "Match finished"
    lines = [f"{head} — {MODE_NAMES.get(match['mode'], match['mode'])}, {rules}"]
    for p in rows:
        delta = ""
        if p["rating_before"] is not None and p["rating_after"] is not None:
            d = p["rating_after"] - p["rating_before"]
            delta = f" ({'+' if d >= 0 else ''}{d})"
        lines.append(f"{p['place']}. {p['handle']} — {score(p)}{delta}")
    lines.append(link)
    return "\n".join(lines)


def webhooks(match) -> list[str]:
    hooks = []
    s = store.settings(match)
    if s.get("discord_webhook"):
        hooks.append(s["discord_webhook"])
    for p in store.players(match["id"]):
        hook = db.scalar("SELECT discord_webhook FROM users WHERE id = ?", (p["user_id"],))
        if hook:
            hooks.append(hook)
    return list(dict.fromkeys(h for h in hooks if h.startswith(config.DISCORD_PREFIX)))


async def post_result(match_id: int) -> None:
    match = store.match_by_id(match_id)
    hooks = webhooks(match)
    if not hooks:
        return
    content = message(match)
    async with httpx.AsyncClient(timeout=10) as client:
        for hook in hooks:
            try:
                r = await client.post(hook, json={"content": content, "allowed_mentions": {"parse": []}})
                if r.status_code >= 400:
                    log.warning("discord webhook returned %s", r.status_code)
            except httpx.HTTPError as e:
                log.warning("discord webhook failed: %s", e)


def on_final(match_id: int) -> None:
    try:
        asyncio.get_running_loop().create_task(post_result(match_id))
    except RuntimeError:
        pass


def setup() -> None:
    from app.matches import finalize
    if on_final not in finalize.after_final:
        finalize.after_final.append(on_final)
