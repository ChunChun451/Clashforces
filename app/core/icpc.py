"""ICPC scoring for N players (pure).

- A submission counts if start <= time < player's end_time, on that player's problems.
- Penalty for a solved problem = whole minutes from start to the first OK
  + 20 per rejected submission before it. Rejected = anything except
  OK, COMPILATION_ERROR and TESTING. Submissions after the first OK are ignored.
- penalty_bonus (handicap, e.g. -40) is added once the player has solved something.
- Rank: more solved, then lower penalty, then earlier last OK, else shared place.
  Forfeited players rank below everyone who did not forfeit.
"""

from collections import defaultdict

from app import config
from app.models import COMPILATION_ERROR, OK, TESTING, Cell, PlayerInput, Row, Sub

NOT_REJECTED = {OK, COMPILATION_ERROR, TESTING}


def window_subs(start: int, players: list[PlayerInput], subs: list[Sub]) -> dict:
    """Group in-window submissions by (player, problem), sorted by (time, id)."""
    by_id = {p.id: p for p in players}
    out = defaultdict(list)
    for s in subs:
        p = by_id.get(s.player)
        if p is None or s.problem not in p.problems:
            continue
        if start <= s.time < p.end_time:
            out[(s.player, s.problem)].append(s)
    for lst in out.values():
        lst.sort(key=lambda s: (s.time, s.id))
    return out


def icpc_cell(problem: str, subs: list[Sub], start: int) -> Cell:
    cell = Cell(problem)
    for s in subs:
        if s.verdict == OK:
            cell.solved, cell.solved_at = True, s.time
            cell.minute = (s.time - start) // 60
            break
        if s.verdict == TESTING:
            cell.pending = True
        elif s.verdict != COMPILATION_ERROR:
            cell.tries += 1
    return cell


def score_icpc(start: int, players: list[PlayerInput], subs: list[Sub],
               wrong_penalty: int = config.ICPC_WRONG_PENALTY) -> list[Row]:
    grouped = window_subs(start, players, subs)
    rows = []
    for p in players:
        row = Row(player=p.id, forfeited=p.forfeited)
        for prob in p.problems:
            cell = icpc_cell(prob, grouped.get((p.id, prob), []), start)
            row.cells[prob] = cell
            if cell.solved:
                row.solved += 1
                row.penalty += cell.minute + wrong_penalty * cell.tries
                row.last_ok = max(row.last_ok or 0, cell.solved_at)
        if row.solved:
            row.penalty += p.penalty_bonus
        rows.append(row)
    return assign_places(rows, lambda r: (r.forfeited, -r.solved, r.penalty, r.last_ok or 0))


def assign_places(rows: list[Row], key) -> list[Row]:
    """Sort by key and give equal keys the same place (1, 1, 3)."""
    rows = sorted(rows, key=key)
    for i, row in enumerate(rows):
        if i > 0 and key(rows[i - 1]) == key(row):
            row.place = rows[i - 1].place
        else:
            row.place = i + 1
    return rows
