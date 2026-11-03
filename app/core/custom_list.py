"""Parse and validate a pasted list of problem IDs (pure).

Accepts "2043C", "2043 C", "2043/C", "2043c", and Codeforces problem URLs,
separated by commas, spaces or new lines.
"""

import re
from dataclasses import dataclass, field

from app import config
from app.models import Problem

URL_RE = re.compile(
    r"https?://(?:www\.)?codeforces\.com/(?:contest/(\d+)/problem/|problemset/problem/(\d+)/)([A-Za-z]\d?)",
)
ID_RE = re.compile(r"(?<![\w/])(\d{1,6})\s*/?\s*([A-Za-z]\d?)(?![A-Za-z0-9])")


@dataclass
class CustomCheck:
    problems: list[Problem] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)       # IDs not in the cache
    unreadable: list[str] = field(default_factory=list)    # text we couldn't parse
    running: list[str] = field(default_factory=list)       # from a contest running now
    duplicates: list[str] = field(default_factory=list)    # same problem twice (incl. Div.1/Div.2 copy)
    warnings: list[tuple] = field(default_factory=list)    # (player, key): already touched

    @property
    def errors(self) -> list[str]:
        out = []
        if self.unreadable:
            out.append("Couldn't read: " + ", ".join(self.unreadable))
        if self.unknown:
            out.append("Not found on Codeforces: " + ", ".join(self.unknown))
        if self.running:
            out.append("From a contest that is running right now: " + ", ".join(self.running))
        if self.duplicates:
            out.append("Listed twice (or a Div.1/Div.2 copy): " + ", ".join(self.duplicates))
        if not self.problems and not out:
            out.append("Paste at least one problem ID, e.g. 2043C.")
        if len(self.problems) > config.PROBLEM_COUNT_MAX:
            out.append(f"At most {config.PROBLEM_COUNT_MAX} problems.")
        return out


def parse(text: str) -> tuple[list[str], list[str]]:
    """Return (keys in order, unreadable fragments)."""
    keys = []
    found = []
    for m in URL_RE.finditer(text):
        found.append((m.start(), m.end(), f"{m.group(1) or m.group(2)}{m.group(3).upper()}"))
    rest = URL_RE.sub(lambda m: " " * (m.end() - m.start()), text)
    for m in ID_RE.finditer(rest):
        found.append((m.start(), m.end(), f"{int(m.group(1))}{m.group(2).upper()}"))
        rest = rest[:m.start()] + " " * (m.end() - m.start()) + rest[m.end():]
    found.sort()
    keys = [k for _, _, k in found]
    leftovers = [t for t in re.split(r"[\s,;]+", rest) if t]
    return keys, leftovers


def check(text: str, by_key: dict[str, Problem], running: set[int], touched_by_player: dict) -> CustomCheck:
    """touched_by_player: player name -> set of touched dup groups."""
    keys, leftovers = parse(text)
    result = CustomCheck(unreadable=leftovers)
    seen_groups = set()
    for key in keys:
        p = by_key.get(key)
        if p is None:
            result.unknown.append(key)
            continue
        if p.contest_id in running:
            result.running.append(key)
            continue
        if p.group in seen_groups:
            result.duplicates.append(key)
            continue
        seen_groups.add(p.group)
        result.problems.append(p)
        for player, groups in touched_by_player.items():
            if p.group in groups:
                result.warnings.append((player, key))
    return result
