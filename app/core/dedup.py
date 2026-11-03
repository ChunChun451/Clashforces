"""Find Div.1 / Div.2 copies of the same problem (pure).

Contests that start at the same moment often share problems under different
IDs (Div.1 B = Div.2 D). Problems with the same name in contests with the same
start time get the same group id: the smallest problem key in the group.
"""

from collections import defaultdict

from app.models import Contest, Problem


def dup_groups(problems: list[Problem], contests: list[Contest]) -> dict[str, str]:
    start = {c.id: c.start_time for c in contests}
    buckets: dict[tuple, list[str]] = defaultdict(list)
    groups: dict[str, str] = {}
    for p in problems:
        t = start.get(p.contest_id)
        if t is None:
            groups[p.key] = p.key
        else:
            buckets[(t, p.name.strip().lower())].append(p.key)
    for keys in buckets.values():
        leader = min(keys)
        for k in keys:
            groups[k] = leader
    return groups
