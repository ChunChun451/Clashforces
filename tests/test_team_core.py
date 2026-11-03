"""N-team scoring (ICPC and Lockout, clinch with 3+ teams, ties), snake balance, team names (pure)."""

import pytest

from app.core import team as team_core
from app.core.lockout import decided
from app.core.team import balance, can_start, score_team, score_team_lockout, snake_order, team_input
from app.models import Sub

START = 18 * 3600
END = START + 3600
PROBS = ("A", "B", "C")


def t(mm, ss=0):
    return START + mm * 60 + ss


def three_teams(members=None):
    teams = {k: team_input(k, END, PROBS) for k in range(3)}
    members = members or {"a1": 0, "a2": 0, "b1": 1, "b2": 1, "c1": 2, "c2": 2}
    return teams, members


def rows_by_team(rows):
    return {r.player: r for r in rows}


# ---------- pure scoring ----------

def test_n_team_icpc_places_and_penalty():
    teams, members = three_teams()
    subs = [
        Sub(1, "a1", "A", "WRONG_ANSWER", t(5)), Sub(2, "a2", "A", "OK", t(10)),     # A: 1 solved, 30
        Sub(3, "b1", "A", "OK", t(8)), Sub(4, "b2", "B", "OK", t(20)),               # B: 2 solved, 28
        Sub(5, "c1", "C", "OK", t(9)), Sub(6, "c2", "B", "WRONG_ANSWER", t(11)),
        Sub(7, "c2", "B", "OK", t(19)),                                              # C: 2 solved, 9+19+20=48
    ]
    r = rows_by_team(score_team(START, teams, members, subs))
    assert (r[1].place, r[2].place, r[0].place) == (1, 2, 3)
    assert r[0].penalty == 30 and r[1].penalty == 28 and r[2].penalty == 48


def test_n_team_icpc_ties_share_a_place():
    teams = {k: team_input(k, END, PROBS) for k in range(4)}
    members = {"a": 0, "b": 1, "c": 2, "d": 3}
    subs = [Sub(1, "a", "A", "OK", t(10)), Sub(2, "b", "A", "OK", t(10)), Sub(3, "c", "B", "OK", t(5))]
    r = rows_by_team(score_team(START, teams, members, subs))
    assert r[2].place == 1 and r[0].place == r[1].place == 2 and r[3].place == 4


def test_n_team_lockout_first_team_claims_and_clinch_three_teams():
    teams, members = three_teams()
    pts = {"A": 100, "B": 200, "C": 300}
    subs = [Sub(1, "b1", "C", "OK", t(5)), Sub(2, "a1", "C", "OK", t(6))]   # B claims C, A is late
    rows = score_team_lockout(START, teams, members, pts, subs)
    r = rows_by_team(rows)
    assert r[1].points == 300 and r[0].points == 0 and r[1].cells["C"].first and not r[0].cells["C"].first
    # 300 open: A and C can still reach 300
    assert not decided(rows, pts, {0, 1, 2})
    subs.append(Sub(3, "c2", "A", "OK", t(7)))                             # C: 100, open 200
    rows = score_team_lockout(START, teams, members, pts, subs)
    assert not decided(rows, pts, {0, 1, 2})                               # C can reach 300 = tie
    subs.append(Sub(4, "b2", "B", "OK", t(8)))                             # B: 500, nothing open
    rows = score_team_lockout(START, teams, members, pts, subs)
    assert decided(rows, pts, {0, 1, 2}) and rows_by_team(rows)[1].place == 1
    # third place exists too: C 100 ahead of A 0
    r = rows_by_team(rows)
    assert r[2].place == 2 and r[0].place == 3


def test_n_team_lockout_clinch_ignores_teams_that_cannot_score():
    teams, members = three_teams()
    pts = {"A": 100, "B": 200, "C": 300}
    rows = score_team_lockout(START, teams, members, pts, [Sub(1, "a1", "C", "OK", t(5))])
    assert not decided(rows, pts, {0, 1, 2})          # B and C could still take 300
    assert not decided(rows, pts, {0, 2})             # C alone can still tie
    assert decided(rows, pts, {0})                    # nobody else can score: A wins


def test_n_team_lockout_same_second_split_between_three():
    teams, members = three_teams()
    pts = {"A": 300}
    subs = [Sub(1, "a1", "A", "OK", t(5)), Sub(2, "b1", "A", "OK", t(5)), Sub(3, "c1", "A", "OK", t(5))]
    r = rows_by_team(score_team_lockout(START, teams, members, pts, subs))
    assert r[0].points == r[1].points == r[2].points == 100 and r[0].place == r[1].place == r[2].place == 1


# ---------- balance, snake, can_start ----------

def test_snake_balance_n_teams_is_deterministic_and_even():
    ratings = {f"p{i}": 1000 + 100 * i for i in range(12)}
    a = balance(ratings, 4, 3)
    assert a == balance(dict(ratings), 4, 3)                                  # deterministic
    sizes = [list(a.values()).count(k) for k in range(3)]
    assert sizes == [4, 4, 4]
    sums = [sum(ratings[p] for p, k in a.items() if k == t) for t in range(3)]
    assert max(sums) - min(sums) <= 100
    # fewer players than slots: sizes as even as possible, never above team_size
    b = balance({f"q{i}": 1500 for i in range(7)}, 3, 3)
    assert sorted(list(b.values()).count(k) for k in range(3)) == [2, 2, 3]
    with pytest.raises(ValueError):
        balance({f"r{i}": 1500 for i in range(10)}, 3, 3)
    assert snake_order(3, 7) == [0, 1, 2, 2, 1, 0, 0]


def test_can_start_n_teams():
    names = ["Red", "Blue", "Green"]
    assert can_start({"a": 0, "b": 1, "c": 2}, 1, 3, names) is None
    msg = can_start({"a": 0, "b": 0, "c": 1, "d": 2}, 2, 3, names, need_full=False)
    assert "even" in msg and "Red 2" in msg
    assert can_start({"a": 0, "b": 0, "c": 1, "d": 2}, 2, 3, names, need_full=False, allow_uneven=True) is None
    assert "two teams" in can_start({"a": 0, "b": 0}, 2, 3, names, need_full=False, allow_uneven=True)


def test_team_names_are_cleaned_and_unique():
    assert team_core.clean_name("  The\tBest  \x07Team ", 0) == "The Best Team"
    assert team_core.clean_name("", 2) == "Team C"
    with pytest.raises(team_core.NameError_):
        team_core.clean_name("x" * 25, 0)
    with pytest.raises(team_core.NameError_):
        team_core.check_unique(["Owls", "owls"])
