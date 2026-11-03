"""Team matches (pure).

2 to 6 teams play as if each were one player:

- ICPC: a problem is solved for the team when any member gets OK. Rejected
  submissions from ALL members before the team's first OK add penalty.
- Lockout: a problem goes to the team whose member solved it first. Ties in
  the same Codeforces second split the points, like solo Lockout.

A member's submissions only count inside that member's own window, so a member
who forfeits stops counting while their teammates keep playing.
"""

import random
import re
import unicodedata

from app import config
from app.core.icpc import score_icpc
from app.core.lockout import score_lockout
from app.models import OK, PlayerInput, Row, Sub


def letter(idx: int) -> str:
    return chr(ord("A") + idx)


def default_name(idx: int) -> str:
    return f"Team {letter(idx)}"


# Kept for older imports: the two default names of a 2-team duel.
TEAM_NAMES = (default_name(0), default_name(1))


def team_subs(subs: list[Sub], members: dict, member_end: dict | None = None) -> list[Sub]:
    """Re-label member submissions with their team id. members: player_id -> team_id.

    member_end (player_id -> end_time) drops submissions after that member's window.
    """
    out = []
    for s in subs:
        if s.player not in members:
            continue
        if member_end is not None and s.time >= member_end.get(s.player, s.time + 1):
            continue
        out.append(Sub(s.id, members[s.player], s.problem, s.verdict, s.time))
    return out


def score_team(start: int, teams: dict, members: dict, subs: list[Sub], member_end: dict | None = None) -> list[Row]:
    """Team ICPC for any number of teams. teams: team_id -> PlayerInput (id = team_id).
    members: player_id -> team_id."""
    return score_icpc(start, list(teams.values()), team_subs(subs, members, member_end))


def score_team_lockout(start: int, teams: dict, members: dict, points: dict[str, int], subs: list[Sub],
                       member_end: dict | None = None) -> list[Row]:
    """Team Lockout for any number of teams: the first team to solve claims the problem."""
    return score_lockout(start, list(teams.values()), points, team_subs(subs, members, member_end))


def team_input(team_id, end_time: int, problems: tuple[str, ...], penalty_bonus: int = 0,
               forfeited: bool = False) -> PlayerInput:
    return PlayerInput(id=team_id, end_time=end_time, problems=problems, penalty_bonus=penalty_bonus,
                       forfeited=forfeited)


def solvers(subs: list[Sub], members: dict, member_end: dict | None = None, start: int | None = None) -> dict:
    """(team_id, problem) -> player_id of the member whose OK counted first for the team."""
    out = {}
    for s in sorted(subs, key=lambda s: (s.time, s.id)):
        if s.verdict != OK or s.player not in members:
            continue
        if start is not None and s.time < start:
            continue
        if member_end is not None and s.time >= member_end.get(s.player, s.time + 1):
            continue
        out.setdefault((members[s.player], s.problem), s.player)
    return out


# ---------- names ----------

class NameError_(ValueError):
    pass


def clean_name(raw, idx: int) -> str:
    """Trim, drop control characters, collapse spaces. Empty -> the default name."""
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = re.sub(r"\s", " ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default_name(idx)
    if len(text) > config.TEAM_NAME_MAX:
        raise NameError_(f"Team names are at most {config.TEAM_NAME_MAX} characters.")
    return text


def check_unique(names: list[str]) -> None:
    seen = set()
    for n in names:
        k = n.casefold()
        if k in seen:
            raise NameError_(f"Two teams can't both be called “{n}”.")
        seen.add(k)


# ---------- lobby helpers ----------

def sizes(team_of: dict, team_count: int) -> list[int]:
    return [sum(1 for t in team_of.values() if t == k) for k in range(team_count)]


def can_start(team_of: dict, team_size: int, team_count: int = 2, names=None, need_full: bool = True,
              allow_uneven: bool = False) -> str | None:
    """None when the teams are ready to play, else a message saying why not.

    team_of: player_id -> team index (-1 or None = not on a team yet).
    need_full: every team must have team_size players (friend duels). Hosted
    contests start with whoever is placed, so they only need even teams.
    """
    names = names or [default_name(i) for i in range(team_count)]
    counts = sizes(team_of, team_count)
    unplaced = sum(1 for t in team_of.values() if t is None or not 0 <= t < team_count)
    if need_full:
        need = team_count * team_size
        if counts == [team_size] * team_count:
            return None
        if len(team_of) < need:
            missing = need - len(team_of)
            return f"Waiting for {missing} more {'player' if missing == 1 else 'players'} ({len(team_of)}/{need})."
        if unplaced:
            return f"{unplaced} {'player has' if unplaced == 1 else 'players have'} no team yet."
    if not allow_uneven and len(set(counts)) > 1:
        if team_count == 2:
            return (f"Teams must be even: {names[0]} has {counts[0]}, {names[1]} has {counts[1]}. "
                    "Someone has to switch sides.")
        detail = ", ".join(f"{names[i]} {c}" for i, c in enumerate(counts))
        return f"Teams must be even ({detail}). Move someone, or allow uneven teams."
    if sum(1 for c in counts if c) < 2:
        return "At least two teams need players."
    return None


def _spread(sums: list[int]) -> tuple:
    mean = sum(sums) / len(sums)
    return (max(sums) - min(sums), sum((x - mean) ** 2 for x in sums))


def balance(ratings: dict, team_size: int, team_count: int = 2) -> dict:
    """Split players into team_count teams with close total ratings. ratings: player_id -> rating.

    A snake draft by rating (A B C C B A ...), then pairwise swaps while they
    shrink the gap between the strongest and weakest team. Sizes are as even as
    possible and never above team_size. Equal ratings keep input order, so the
    result is deterministic.
    """
    ids = list(ratings)
    n = len(ids)
    if n == 0:
        return {}
    if n > team_size * team_count:
        raise ValueError("too many players for these teams")
    order = sorted(range(n), key=lambda i: (-ratings[ids[i]], i))
    teams: list[list[int]] = [[] for _ in range(team_count)]
    forward = True
    pos = 0
    for i in order:
        # snake: 0..k-1 then k-1..0, skipping full teams
        while True:
            t = pos if forward else team_count - 1 - pos
            pos += 1
            if pos == team_count:
                pos, forward = 0, not forward
            if len(teams[t]) < team_size:
                break
        teams[t].append(i)

    def sums():
        return [sum(ratings[ids[i]] for i in tm) for tm in teams]

    best = _spread(sums())
    improved = True
    rounds = 0
    while improved and rounds < 200:
        improved = False
        rounds += 1
        for a in range(team_count):
            for b in range(a + 1, team_count):
                for x in range(len(teams[a])):
                    for y in range(len(teams[b])):
                        teams[a][x], teams[b][y] = teams[b][y], teams[a][x]
                        cand = _spread(sums())
                        if cand < best:
                            best, improved = cand, True
                        else:
                            teams[a][x], teams[b][y] = teams[b][y], teams[a][x]
    return {ids[i]: t for t, tm in enumerate(teams) for i in tm}


def snake_order(team_count: int, picks: int) -> list[int]:
    """Which team picks at each turn of a captains' draft: 0 1 2 2 1 0 0 1 2 ..."""
    out = []
    forward = True
    while len(out) < picks:
        rnd = list(range(team_count)) if forward else list(range(team_count - 1, -1, -1))
        out.extend(rnd)
        forward = not forward
    return out[:picks]


def smallest_team(counts: list[int], team_size: int | None = None) -> int | None:
    """Index of the team with the fewest players (lowest index on ties); None if all are full."""
    best = None
    for i, c in enumerate(counts):
        if team_size is not None and c >= team_size:
            continue
        if best is None or c < counts[best]:
            best = i
    return best


def shuffle(ids: list, team_size: int, rng: random.Random, team_count: int = 2) -> dict:
    ids = list(ids)
    rng.shuffle(ids)
    return {pid: i % team_count for i, pid in enumerate(ids)}


def gap(ratings: dict, team_of: dict, team_count: int | None = None) -> int:
    k = team_count or (max(team_of.values()) + 1 if team_of else 2)
    sums = [0] * max(k, 2)
    for pid, t in team_of.items():
        sums[t] += ratings.get(pid, 0)
    used = [s for i, s in enumerate(sums) if any(t == i for t in team_of.values())] or [0]
    return max(used) - min(used)
