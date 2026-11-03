"""Lockout scoring for N players (pure).

- Only the first in-window OK on a problem scores its points.
- OKs in the same Codeforces second split the points evenly.
- Wrong submissions cost nothing.
- Rank: more points, then earlier last scoring OK, else shared place.
- The match is decided early when no player who can still score could reach
  the leader with the points still open.
"""

from app.core.icpc import assign_places, window_subs
from app.models import OK, TESTING, Cell, PlayerInput, Row, Sub


def score_lockout(start: int, players: list[PlayerInput], points: dict[str, int], subs: list[Sub]) -> list[Row]:
    grouped = window_subs(start, players, subs)
    rows = {p.id: Row(player=p.id, forfeited=p.forfeited) for p in players}

    for prob, value in points.items():
        first_ok = {}  # player -> time of their first OK on this problem
        for p in players:
            cell = Cell(prob)
            for s in grouped.get((p.id, prob), []):
                if s.verdict == OK:
                    cell.solved, cell.solved_at = True, s.time
                    cell.minute = (s.time - start) // 60
                    first_ok[p.id] = s.time
                    break
                if s.verdict == TESTING:
                    cell.pending = True
                else:
                    cell.tries += 1
            rows[p.id].cells[prob] = cell
        if not first_ok:
            continue
        t = min(first_ok.values())
        owners = [pid for pid, ok_time in first_ok.items() if ok_time == t]
        share = value / len(owners)
        for pid in owners:
            row = rows[pid]
            row.cells[prob].first = True
            row.cells[prob].points = share
            row.points += share
            row.solved += 1
            row.last_ok = max(row.last_ok or 0, t)

    return assign_places(list(rows.values()), lambda r: (r.forfeited, -r.points, r.last_ok or 0))


def owner_time(rows: list[Row], prob: str) -> int | None:
    times = [r.cells[prob].solved_at for r in rows if r.cells.get(prob) and r.cells[prob].first]
    return min(times) if times else None


def open_points(rows: list[Row], points: dict[str, int]) -> int:
    return sum(v for prob, v in points.items() if owner_time(rows, prob) is None)


def has_unsettled(rows: list[Row], points: dict[str, int]) -> bool:
    """A TESTING submission could still change who owns a problem."""
    for prob in points:
        if any(r.cells.get(prob) and r.cells[prob].pending for r in rows):
            return True
    return False


def decided(rows: list[Row], points: dict[str, int], can_score: set) -> bool:
    """True when the winner can no longer change.

    `can_score` = players whose window is still open and who did not forfeit.
    """
    if has_unsettled(rows, points):
        return False
    remaining = open_points(rows, points)
    if remaining == 0:
        return True  # every problem is taken: points and tie-breaks are final
    ranked = sorted(rows, key=lambda r: (r.forfeited, -r.points))
    leader = ranked[0]
    for r in ranked[1:]:
        if r.forfeited:
            continue
        reach = r.points + (remaining if r.player in can_score else 0)
        if reach >= leader.points:
            return False
    return True
