"""Team matches (pure).

Two teams play as if each were one player in a 1v1:

- ICPC: a problem is solved for the team when any member gets OK. Rejected
  submissions from ALL members before the team's first OK add penalty.
- Lockout: a problem goes to the team whose member solved it first. Ties in
  the same Codeforces second split the points, like 1v1 Lockout.

A member's submissions only count inside that member's own window, so a member
who forfeits stops counting while their teammates keep playing.
"""

import itertools
import random

from app.core.icpc import score_icpc
from app.core.lockout import score_lockout
from app.models import OK, PlayerInput, Row, Sub

TEAM_NAMES = ("Team A", "Team B")


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
    """Team ICPC. teams: team_id -> PlayerInput (id = team_id). members: player_id -> team_id."""
    return score_icpc(start, list(teams.values()), team_subs(subs, members, member_end))


def score_team_lockout(start: int, teams: dict, members: dict, points: dict[str, int], subs: list[Sub],
                       member_end: dict | None = None) -> list[Row]:
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


# ---------- lobby helpers ----------

def can_start(team_of: dict, team_size: int) -> str | None:
    """None when both teams are full, else a message saying why the match can't start."""
    sizes = [sum(1 for t in team_of.values() if t == k) for k in (0, 1)]
    if sizes == [team_size, team_size]:
        return None
    need = 2 * team_size
    if len(team_of) < need:
        missing = need - len(team_of)
        return f"Waiting for {missing} more {'player' if missing == 1 else 'players'} ({len(team_of)}/{need})."
    return (f"Teams must be even: {TEAM_NAMES[0]} has {sizes[0]}, {TEAM_NAMES[1]} has {sizes[1]}. "
            "Someone has to switch sides.")


def balance(ratings: dict, team_size: int) -> dict:
    """Split players to minimise the gap in total rating. ratings: player_id -> rating.

    Team sizes are as even as possible (never above team_size). Ties keep the
    split that changes the input order least, so the result is deterministic.
    """
    ids = list(ratings)
    n = len(ids)
    if n == 0:
        return {}
    size_a = min(team_size, (n + 1) // 2)
    if n - size_a > team_size:
        raise ValueError("too many players for these teams")
    total = sum(ratings.values())
    best, best_key = None, None
    for combo in itertools.combinations(range(n), size_a):
        if 0 not in combo:
            continue  # the first player is always on Team A: halves the search, no mirror duplicates
        a = sum(ratings[ids[i]] for i in combo)
        key = (abs(total - 2 * a), combo)
        if best_key is None or key < best_key:
            best, best_key = combo, key
    chosen = set(best)
    return {pid: 0 if i in chosen else 1 for i, pid in enumerate(ids)}


def shuffle(ids: list, team_size: int, rng: random.Random) -> dict:
    ids = list(ids)
    rng.shuffle(ids)
    size_a = min(team_size, (len(ids) + 1) // 2)
    return {pid: 0 if i < size_a else 1 for i, pid in enumerate(ids)}


def gap(ratings: dict, team_of: dict) -> int:
    sums = [0, 0]
    for pid, t in team_of.items():
        sums[t] += ratings.get(pid, 0)
    return abs(sums[0] - sums[1])
