"""Scoreboard freeze (pure). ICPC rules only.

During the freeze a viewer sees their own submissions, and other players'
submissions from before freeze_start. Later submissions by others show only
as a count of hidden tries. At the end, hidden cells are revealed one at a
time, ICPC style: always the lowest-ranked player that still has a hidden
cell, their leftmost hidden problem first.
"""

from collections import Counter, defaultdict
from typing import Callable

from app.models import Row, Sub


def split_visible(subs: list[Sub], viewer, freeze_start: int | None) -> tuple[list[Sub], list[Sub]]:
    """viewer: one player id, or a set of ids (a team sees all of its members' submissions)."""
    if freeze_start is None:
        return list(subs), []
    own = viewer if isinstance(viewer, (set, frozenset)) else {viewer}
    visible, hidden = [], []
    for s in subs:
        (visible if s.player in own or s.time < freeze_start else hidden).append(s)
    return visible, hidden


def hidden_counts(hidden: list[Sub]) -> Counter:
    return Counter((s.player, s.problem) for s in hidden)


def reveal_steps(score: Callable[[list[Sub]], list[Row]], problem_order: dict, subs: list[Sub],
                 freeze_start: int | None) -> list[dict]:
    """score(subs) -> ranked rows. problem_order: player -> tuple of problem keys (left to right).

    Returns [{"player", "problem", "rows"}] where rows is the standings after that step.
    """
    visible, hidden = split_visible(subs, None, freeze_start)
    pending = defaultdict(list)
    for s in hidden:
        pending[(s.player, s.problem)].append(s)
    steps = []
    while pending:
        rows = score(visible)
        target = None
        for row in reversed(rows):
            for prob in problem_order.get(row.player, ()):
                if (row.player, prob) in pending:
                    target = (row.player, prob)
                    break
            if target:
                break
        if target is None:  # hidden subs for problems not in anyone's order; drop them
            break
        visible += pending.pop(target)
        steps.append({"player": target[0], "problem": target[1], "rows": score(visible)})
    return steps


def reveal_targets(score: Callable[[list[Sub]], list[Row]], problem_order: dict, subs: list[Sub],
                   freeze_start: int | None) -> list[tuple]:
    """The same ICPC order as reveal_steps, as (player, problem) pairs only (no rows kept per step).

    Always the lowest-ranked player that still has a hidden cell, their leftmost hidden problem first.
    """
    visible, hidden = split_visible(subs, None, freeze_start)
    pending = defaultdict(list)
    for s in hidden:
        pending[(s.player, s.problem)].append(s)
    out = []
    while pending:
        target = None
        for row in reversed(score(visible)):
            target = next(((row.player, p) for p in problem_order.get(row.player, ()) if (row.player, p) in pending),
                          None)
            if target:
                break
        if target is None:
            break
        visible += pending.pop(target)
        out.append(target)
    return out
