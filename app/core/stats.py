"""Personal stats (pure). Only data from matches on this platform."""

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatchOutcome:
    match_id: int
    mode: str
    place: int
    shared_first: bool        # someone else also has place 1
    tags: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Solve:
    rating: int | None
    tags: tuple[str, ...]
    minutes: int


def outcome(o: MatchOutcome) -> str:
    if o.place == 1:
        return "draw" if o.shared_first else "win"
    return "loss"


def record(outcomes: list[MatchOutcome]) -> dict:
    """{"overall": {win, loss, draw}, "by_mode": {mode: {...}}}"""
    def empty():
        return {"win": 0, "loss": 0, "draw": 0}
    overall, by_mode = empty(), defaultdict(empty)
    for o in outcomes:
        r = outcome(o)
        overall[r] += 1
        by_mode[o.mode][r] += 1
    return {"overall": overall, "by_mode": dict(by_mode)}


def solve_times(solves: list[Solve], min_count: int = 1) -> list[dict]:
    """Average minutes to solve, per (rating, tag). Example: 1600 dp: 31 min."""
    buckets = defaultdict(list)
    for s in solves:
        if s.rating is None:
            continue
        for t in s.tags:
            if t.startswith("*"):
                continue
            buckets[(s.rating, t)].append(s.minutes)
    out = [{"rating": r, "tag": t, "avg_minutes": round(sum(v) / len(v)), "count": len(v)}
           for (r, t), v in buckets.items() if len(v) >= min_count]
    return sorted(out, key=lambda x: (x["rating"], x["tag"]))


def tag_win_rates(outcomes: list[MatchOutcome]) -> list[dict]:
    """Win rate in matches that included each tag (draws count as half)."""
    totals = defaultdict(lambda: [0, 0.0])
    for o in outcomes:
        r = outcome(o)
        for t in o.tags:
            if t.startswith("*"):
                continue
            totals[t][0] += 1
            totals[t][1] += 1.0 if r == "win" else 0.5 if r == "draw" else 0.0
    out = [{"tag": t, "matches": n, "win_rate": round(w / n, 3)} for t, (n, w) in totals.items()]
    return sorted(out, key=lambda x: (-x["matches"], x["tag"]))


def head_to_head(my_places: dict[int, int], their_places: dict[int, int]) -> dict:
    """my_places / their_places: match_id -> place, for 1v1 matches both played."""
    res = {"win": 0, "loss": 0, "draw": 0}
    for mid, mine in my_places.items():
        theirs = their_places.get(mid)
        if theirs is None:
            continue
        res["win" if mine < theirs else "loss" if mine > theirs else "draw"] += 1
    return res
