"""Ranked matchmaking (pure).

A player's search window starts at +-QUEUE_START_WINDOW and widens by
QUEUE_WIDEN_BY every QUEUE_WIDEN_EVERY_S, up to QUEUE_MAX_WINDOW. Two players
can pair when their gap fits the wider of their two windows. The longest
waiting player is paired first, with their closest-rated candidate.
"""

from dataclasses import dataclass

from app import config


@dataclass(frozen=True)
class Entry:
    user_id: int
    rating: int
    joined_at: int


def window(waited_s: float) -> int:
    steps = int(max(0, waited_s) // config.QUEUE_WIDEN_EVERY_S)
    return min(config.QUEUE_START_WINDOW + config.QUEUE_WIDEN_BY * steps, config.QUEUE_MAX_WINDOW)


def pair(entries: list[Entry], now: int) -> list[tuple[Entry, Entry]]:
    pairs = []
    used = set()
    for e in sorted(entries, key=lambda x: (x.joined_at, x.user_id)):
        if e.user_id in used:
            continue
        best = None
        for c in entries:
            if c.user_id == e.user_id or c.user_id in used:
                continue
            gap = abs(c.rating - e.rating)
            if gap > max(window(now - e.joined_at), window(now - c.joined_at)):
                continue
            key = (gap, c.joined_at, c.user_id)
            if best is None or key < best[0]:
                best = (key, c)
        if best:
            pairs.append((e, best[1]))
            used |= {e.user_id, best[1].user_id}
    return pairs


def ranked_range(cf_a: int, cf_b: int) -> tuple[int, int]:
    def r(x):
        return x if x > 0 else config.UNRATED_START
    avg = int((r(cf_a) + r(cf_b)) / 2 / 100 + 0.5) * 100
    lo = max(config.RATING_MIN, avg - config.RANKED_RANGE_HALF)
    hi = min(config.RATING_MAX, avg + config.RANKED_RANGE_HALF)
    return lo, hi
