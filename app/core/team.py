"""Team ICPC (pure): a problem is solved for the team when any member gets OK.

Rejected submissions from ALL members before the team's first OK add penalty.
"""

from app.core.icpc import score_icpc
from app.models import PlayerInput, Row, Sub


def score_team(start: int, teams: dict, members: dict, subs: list[Sub]) -> list[Row]:
    """teams: team_id -> PlayerInput (id = team_id). members: player_id -> team_id."""
    team_subs = [Sub(s.id, members[s.player], s.problem, s.verdict, s.time) for s in subs if s.player in members]
    return score_icpc(start, list(teams.values()), team_subs)


def team_input(team_id, end_time: int, problems: tuple[str, ...], penalty_bonus: int = 0) -> PlayerInput:
    return PlayerInput(id=team_id, end_time=end_time, problems=problems, penalty_bonus=penalty_bonus)
