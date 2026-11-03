"""Problem selection (pure).

Filters run in a fixed order and we count after each one, so when there are
too few problems we can say exactly which filter to relax. Div.1/Div.2 copies
share a group: touching either copy excludes both, and a match never gets two
copies of one problem. Chosen problems are spread across the rating range.
"""

import math
import random
from dataclasses import dataclass, field

from app import config
from app.models import Problem


@dataclass(frozen=True)
class Filters:
    count: int
    rating_min: int
    rating_max: int
    tags_include: tuple[str, ...] = ()
    tags_avoid: tuple[str, ...] = ()
    avoid_interactive: bool = False


@dataclass
class SelectionError(Exception):
    stage: str
    available: int
    needed: int
    message: str
    counts: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def filter_errors(f: Filters) -> list[str]:
    errors = []
    if not config.PROBLEM_COUNT_MIN <= f.count <= config.PROBLEM_COUNT_MAX:
        errors.append(f"Pick between {config.PROBLEM_COUNT_MIN} and {config.PROBLEM_COUNT_MAX} problems.")
    for r in (f.rating_min, f.rating_max):
        if r % 100 or not config.RATING_MIN <= r <= config.RATING_MAX:
            errors.append(f"Ratings must be multiples of 100 between {config.RATING_MIN} and {config.RATING_MAX}.")
            break
    if f.rating_min > f.rating_max:
        errors.append("The minimum rating is above the maximum.")
    both = set(f.tags_include) & set(f.tags_avoid)
    if both:
        errors.append(f"A tag can't be both included and avoided: {', '.join(sorted(both))}.")
    return errors


def round100(x: float) -> int:
    return int(math.floor(x / 100 + 0.5)) * 100


def targets(lo: int, hi: int, k: int) -> list[int]:
    if k == 1:
        return [round100((lo + hi) / 2)]
    return [round100(lo + i * (hi - lo) / (k - 1)) for i in range(k)]


def base_pool(problems: list[Problem], running: set[int]) -> list[Problem]:
    return [
        p for p in problems
        if p.rating is not None
        and "*special" not in p.tags
        and p.contest_id not in running
        and p.contest_id < 100000  # never gym
    ]


def _groups(pool: list[Problem]) -> int:
    return len({p.group for p in pool})


def _tags(tags) -> str:
    return ", ".join(tags)


def filtered_pool(problems: list[Problem], running: set[int], touched_groups: set[str],
                  f: Filters, exclude_groups: set[str] = frozenset(), who: str = "") -> list[Problem]:
    """Apply filters in order. Raises SelectionError naming the filter to relax."""
    lo, hi, k = f.rating_min, f.rating_max, f.count
    rng_txt = f"{lo}–{hi}" if lo != hi else str(lo)
    stages = [
        ("rating", lambda p: lo <= p.rating <= hi,
         lambda n: f"Only {n} rated problems exist in {rng_txt}. Widen the rating range."),
    ]
    if f.tags_include:
        inc = set(f.tags_include)
        stages.append(("tags_include", lambda p: inc & set(p.tags),
                       lambda n: f"Only {n} problems in {rng_txt} have any of these tags: {_tags(f.tags_include)}. "
                                 "Add more tags or widen the rating range."))
    if f.tags_avoid:
        avoid = set(f.tags_avoid)
        stages.append(("tags_avoid", lambda p: not (avoid & set(p.tags)),
                       lambda n: f"Avoiding {_tags(f.tags_avoid)} leaves only {n} problems. Avoid fewer tags "
                                 "or widen the rating range."))
    if f.avoid_interactive:
        stages.append(("interactive", lambda p: "interactive" not in p.tags,
                       lambda n: f"Without interactive problems only {n} are left. Allow interactive problems "
                                 "or widen the rating range."))
    stages.append(("touched", lambda p: p.group not in touched_groups and p.group not in exclude_groups,
                   lambda n: f"Only {n} problems match your filters that {who or 'every player'} hasn't tried yet. "
                             "Widen the rating range or loosen the tag filters."))

    pool = base_pool(problems, running)
    counts = {}
    for name, keep, message in stages:
        pool = [p for p in pool if keep(p)]
        counts[name] = _groups(pool)
        if counts[name] < k:
            raise SelectionError(name, counts[name], k, message(counts[name]), counts)
    return pool


def spread_pick(pool: list[Problem], f: Filters, rng: random.Random) -> list[Problem]:
    """Pick one problem per target rating, closest rating first, random among equals."""
    by_group: dict[str, list[Problem]] = {}
    for p in pool:
        by_group.setdefault(p.group, []).append(p)
    candidates = [rng.choice(sorted(copies, key=lambda p: p.key)) for _, copies in sorted(by_group.items())]
    chosen = []
    for target in targets(f.rating_min, f.rating_max, f.count):
        best = min(abs(p.rating - target) for p in candidates)
        pick = rng.choice([p for p in candidates if abs(p.rating - target) == best])
        chosen.append(pick)
        candidates = [p for p in candidates if p.group != pick.group]
    return sorted(chosen, key=lambda p: (p.rating, p.key))


def select(problems: list[Problem], running: set[int], touched_groups: set[str], f: Filters,
           rng: random.Random | None = None, exclude_groups: set[str] = frozenset(), who: str = "") -> list[Problem]:
    """Choose f.count problems. Raises SelectionError with a 'relax X' message."""
    errors = filter_errors(f)
    if errors:
        raise SelectionError("settings", 0, f.count, errors[0])
    pool = filtered_pool(problems, running, touched_groups, f, exclude_groups, who)
    return spread_pick(pool, f, rng or random.Random())


def select_per_player(problems: list[Problem], running: set[int], players: list[tuple],
                      f: Filters, rng: random.Random | None = None,
                      exclude_groups: set[str] = frozenset()) -> dict:
    """Per-player rating range handicap.

    players: [(player_id, name, rating_min, rating_max, touched_groups)].
    Each player gets their own set; no problem appears in two sets.
    """
    rng = rng or random.Random()
    used = set(exclude_groups)
    out = {}
    for pid, name, lo, hi, touched in players:
        pf = Filters(f.count, lo, hi, f.tags_include, f.tags_avoid, f.avoid_interactive)
        picked = select(problems, running, touched, pf, rng, used, who=name)
        used |= {p.group for p in picked}
        out[pid] = picked
    return out


def lockout_points(count: int, step: int = config.LOCKOUT_POINT_STEP) -> list[int]:
    return [step * (i + 1) for i in range(count)]


def labels(n: int) -> list[str]:
    return [chr(ord("A") + i) for i in range(n)]
