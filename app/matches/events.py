"""Push match updates to connected viewers."""

from app.matches import store, view
from app.models import OK, Submission
from app.ws.hub import hub


def state(match_id: int) -> None:
    match = store.match_by_id(match_id)
    if match is None:
        return
    hub.send_match(match["code"], "match.state", lambda uid: view.build(match, uid))


def blocked(match, message: str, handle: str | None = None) -> None:
    hub.send_match(match["code"], "lobby.blocked", {"message": message, "handle": handle})


def submissions(match_id: int, user_id: int, changed: list[Submission]) -> None:
    """New/changed submissions for one player: standings + timeline items, freeze-aware."""
    match = store.match_by_id(match_id)
    if match is None:
        return
    s = store.settings(match)
    labels = {p["problem_key"]: p["label"] for p in store.problems(match_id)}
    players = store.players(match_id)
    handle = next((p["handle"] for p in players if p["user_id"] == user_id), None)
    fs = view.freeze_start(match, s)
    team = store.team_of(players) if store.is_team(match) else {}

    def same_side(viewer) -> bool:
        if viewer == user_id:
            return True
        return bool(team) and viewer in team and team.get(viewer) == team.get(user_id)

    def for_viewer(viewer):
        snapshot = view.build(match, viewer)
        visible = [c for c in changed
                   if not (snapshot.get("frozen") and not same_side(viewer) and fs is not None and c.time >= fs)]
        return {
            "standings": snapshot["standings"],
            "frozen": snapshot.get("frozen", False),
            "changed": [{"user_id": user_id, "handle": handle, "label": labels.get(c.problem_key),
                         "team": team.get(user_id), "verdict": c.verdict, "time": c.time,
                         "accepted": c.verdict == OK} for c in visible],
        }

    hub.send_match(match["code"], "standings", for_viewer)


def ended(match_id: int) -> None:
    match = store.match_by_id(match_id)
    hub.send_match(match["code"], "match.ended", {"reason": match["end_reason"],
                                                  "result_status": match["result_status"]})
    state(match_id)


def result(match_id: int) -> None:
    from app.matches import reveal
    match = store.match_by_id(match_id)
    if not reveal.locked(match):  # a contest waiting for its freeze reveal keeps the result to itself
        hub.send_match(match["code"], "match.result", view.result_payload(match))
    state(match_id)


def warning(match, user_id: int, minutes_left: int) -> None:
    hub.send_user(user_id, "match.warning", {"code": match["code"], "minutes_left": minutes_left})


def freeze_started(match) -> None:
    hub.send_match(match["code"], "match.freeze", {"freeze_start": view.freeze_start(match, store.settings(match))})
