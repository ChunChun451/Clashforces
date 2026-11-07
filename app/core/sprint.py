"""Sprint: Lockout, one problem at a time (pure).

Only the current problem is visible. The first Accepted on it takes its points and
reveals the next one for everyone. With a time limit, a problem nobody solves in
time is skipped (no points) and the next one is revealed.

Everything is worked out from Codeforces submission times, so the schedule is the
same whoever computes it and whenever: a verdict that arrives late moves the reveal
to the moment the solution was submitted.
"""

from dataclasses import dataclass

from app.models import OK, Sub


@dataclass(frozen=True)
class Step:
    key: str
    revealed_at: int
    closed_at: int | None      # None = the current, still open problem
    claimed: bool              # closed by an Accepted (False = skipped, or still open)


def schedule(start: int, keys: list[str], subs: list[Sub], limit_s: int | None, now: int) -> list[Step]:
    """The problems revealed so far, in order. `keys` is the problem order (A, B, C...)."""
    oks: dict[str, list[int]] = {}
    for s in subs:
        if s.verdict == OK:
            oks.setdefault(s.problem, []).append(s.time)
    steps: list[Step] = []
    t = start
    for key in keys:
        deadline = t + limit_s if limit_s else None
        first = min((x for x in oks.get(key, ()) if x >= t), default=None)
        if first is not None and (deadline is None or first < deadline):
            steps.append(Step(key, t, first, True))
            t = first
        elif deadline is not None and now >= deadline:
            steps.append(Step(key, t, deadline, False))
            t = deadline
        else:
            steps.append(Step(key, t, None, False))
            break
    return steps


def in_window(steps: list[Step], subs: list[Sub]) -> list[Sub]:
    """Submissions that count: each one made while its problem was open (ties at the claim count)."""
    window = {s.key: (s.revealed_at, s.closed_at) for s in steps}
    out = []
    for s in subs:
        w = window.get(s.problem)
        if w and s.time >= w[0] and (w[1] is None or s.time <= w[1]):
            out.append(s)
    return out


def finished(steps: list[Step], keys: list[str]) -> bool:
    return len(steps) == len(keys) and all(s.closed_at is not None for s in steps)


def skipped(steps: list[Step]) -> set[str]:
    return {s.key for s in steps if s.closed_at is not None and not s.claimed}
