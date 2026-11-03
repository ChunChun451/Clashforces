from app.core.icpc import score_icpc
from app.core.lockout import decided, open_points, score_lockout
from app.core.team import score_team, team_input
from app.models import PlayerInput, Sub

START = 18 * 3600  # "18:00"
END = START + 3600
PROBS = ("A", "B", "C")


def t(hh, mm, ss=0):
    return hh * 3600 + mm * 60 + ss


def player(pid, problems=PROBS, end=END, bonus=0, forfeited=False):
    return PlayerInput(pid, end, problems, bonus, forfeited)


def sub(i, pid, prob, verdict, time):
    return Sub(i, pid, prob, verdict, time)


def by_player(rows):
    return {r.player: r for r in rows}


# ---------------- ICPC ----------------

def test_icpc_example_62_vs_30():
    subs = [
        sub(1, "p1", "A", "WRONG_ANSWER", t(18, 10)),
        sub(2, "p1", "A", "WRONG_ANSWER", t(18, 15)),
        sub(3, "p1", "A", "OK", t(18, 22)),
        sub(4, "p2", "A", "OK", t(18, 30)),
    ]
    rows = score_icpc(START, [player("p1"), player("p2")], subs)
    r = by_player(rows)
    assert r["p1"].penalty == 62 and r["p2"].penalty == 30
    assert r["p2"].place == 1 and r["p1"].place == 2
    assert rows[0].player == "p2"


def test_icpc_more_solved_beats_lower_penalty():
    subs = [sub(1, "p1", "A", "OK", t(18, 50)), sub(2, "p1", "B", "OK", t(18, 55)),
            sub(3, "p2", "A", "OK", t(18, 1))]
    r = by_player(score_icpc(START, [player("p1"), player("p2")], subs))
    assert r["p1"].place == 1 and r["p2"].place == 2


def test_icpc_compilation_error_no_penalty():
    subs = [sub(1, "p1", "A", "COMPILATION_ERROR", t(18, 5)), sub(2, "p1", "A", "OK", t(18, 10))]
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].penalty == 10 and r["p1"].cells["A"].tries == 0


def test_icpc_all_rejected_verdicts_count():
    verdicts = ["WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "RUNTIME_ERROR", "MEMORY_LIMIT_EXCEEDED",
                "IDLENESS_LIMIT_EXCEEDED", "CHALLENGED", "SKIPPED"]
    subs = [sub(i, "p1", "A", v, t(18, 1, i)) for i, v in enumerate(verdicts)]
    subs.append(sub(99, "p1", "A", "OK", t(18, 3)))
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].penalty == 3 + 20 * len(verdicts)


def test_icpc_submissions_outside_window_ignored():
    subs = [
        sub(1, "p1", "A", "WRONG_ANSWER", START - 1),   # before start
        sub(2, "p1", "A", "OK", START),                  # exactly at start counts
        sub(3, "p1", "B", "OK", END),                    # exactly at end does not
        sub(4, "p1", "C", "OK", END + 100),
        sub(5, "p1", "Z", "OK", t(18, 5)),               # not a match problem
    ]
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].solved == 1 and r["p1"].penalty == 0


def test_icpc_wrong_after_ok_ignored():
    subs = [sub(1, "p1", "A", "OK", t(18, 10)), sub(2, "p1", "A", "WRONG_ANSWER", t(18, 11)),
            sub(3, "p1", "A", "OK", t(18, 12))]
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].penalty == 10 and r["p1"].cells["A"].solved_at == t(18, 10)


def test_icpc_unsolved_adds_no_penalty_but_shows_tries():
    subs = [sub(1, "p1", "B", "WRONG_ANSWER", t(18, 10)), sub(2, "p1", "A", "OK", t(18, 20))]
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].penalty == 20 and r["p1"].cells["B"].tries == 1


def test_icpc_tie_broken_by_earlier_last_ok():
    # both: 2 solved, penalty 40. p1's last OK is earlier.
    subs = [sub(1, "p1", "A", "OK", t(18, 5)), sub(2, "p1", "B", "OK", t(18, 35)),
            sub(3, "p2", "A", "OK", t(18, 20)), sub(4, "p2", "B", "OK", t(18, 20, 30)),
            sub(5, "p2", "C", "COMPILATION_ERROR", t(18, 50))]
    r = by_player(score_icpc(START, [player("p1"), player("p2")], subs))
    assert r["p1"].penalty == r["p2"].penalty == 40
    assert r["p2"].place == 1 and r["p1"].place == 2  # p2 last OK 18:20:30 < p1 18:35


def test_icpc_full_tie_is_draw_and_zero_zero_is_draw():
    subs = [sub(1, "p1", "A", "OK", t(18, 5)), sub(2, "p2", "A", "OK", t(18, 5))]
    rows = score_icpc(START, [player("p1"), player("p2")], subs)
    assert [r.place for r in rows] == [1, 1]
    rows = score_icpc(START, [player("p1"), player("p2", bonus=-40)], [])
    assert [r.place for r in rows] == [1, 1]


def test_icpc_n_players_shared_ranks():
    subs = [sub(1, "a", "A", "OK", t(18, 10)), sub(2, "b", "A", "OK", t(18, 10)),
            sub(3, "c", "A", "OK", t(18, 30)), sub(4, "d", "B", "WRONG_ANSWER", t(18, 2))]
    rows = score_icpc(START, [player(x) for x in "abcde"], subs)
    places = {r.player: r.place for r in rows}
    assert places == {"a": 1, "b": 1, "c": 3, "d": 4, "e": 4}


def test_icpc_testing_marks_pending():
    subs = [sub(1, "p1", "A", "TESTING", t(18, 10))]
    r = by_player(score_icpc(START, [player("p1")], subs))
    assert r["p1"].cells["A"].pending and r["p1"].solved == 0


def test_icpc_penalty_bonus_handicap():
    subs = [sub(1, "p1", "A", "OK", t(18, 30)), sub(2, "p2", "A", "OK", t(18, 10))]
    r = by_player(score_icpc(START, [player("p1", bonus=-40), player("p2")], subs))
    assert r["p1"].penalty == -10 and r["p1"].place == 1


def test_icpc_extra_time_per_player_end():
    subs = [sub(1, "p1", "A", "OK", END + 300), sub(2, "p2", "A", "OK", END + 300)]
    r = by_player(score_icpc(START, [player("p1", end=END + 600), player("p2")], subs))
    assert r["p1"].solved == 1 and r["p2"].solved == 0


def test_icpc_per_player_problem_sets():
    subs = [sub(1, "p1", "X1", "OK", t(18, 10)), sub(2, "p2", "Y1", "OK", t(18, 20)),
            sub(3, "p2", "X1", "OK", t(18, 5))]  # p2 solving p1's problem does not count
    rows = score_icpc(START, [player("p1", ("X1", "X2")), player("p2", ("Y1", "Y2"))], subs)
    r = by_player(rows)
    assert r["p1"].place == 1 and r["p2"].penalty == 20


def test_icpc_forfeit_ranks_last():
    subs = [sub(1, "p1", "A", "OK", t(18, 10))]
    r = by_player(score_icpc(START, [player("p1", forfeited=True), player("p2")], subs))
    assert r["p2"].place == 1 and r["p1"].place == 2


# ---------------- Lockout ----------------

POINTS = {"A": 100, "B": 200, "C": 300, "D": 400, "E": 500}


def lp(pid, end=END):
    return PlayerInput(pid, end, tuple(POINTS))


def test_lockout_first_solver_gets_points():
    subs = [sub(1, "p1", "A", "OK", t(18, 10)), sub(2, "p2", "A", "OK", t(18, 5)),
            sub(3, "p1", "B", "OK", t(18, 20))]
    r = by_player(score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs))
    assert r["p2"].points == 100 and r["p1"].points == 200
    assert r["p1"].cells["A"].solved and not r["p1"].cells["A"].first
    assert r["p1"].place == 1


def test_lockout_same_second_split():
    subs = [sub(1, "p1", "C", "OK", t(18, 10)), sub(2, "p2", "C", "OK", t(18, 10))]
    rows = score_lockout(START, [lp("p1"), lp("p2"), lp("p3")], POINTS, subs)
    r = by_player(rows)
    assert r["p1"].points == r["p2"].points == 150
    assert r["p1"].place == r["p2"].place == 1 and r["p3"].place == 3


def test_lockout_wrong_answers_cost_nothing():
    subs = [sub(1, "p1", "A", "WRONG_ANSWER", t(18, 1)), sub(2, "p1", "A", "OK", t(18, 2)),
            sub(3, "p2", "B", "OK", t(18, 30))]
    r = by_player(score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs))
    assert r["p1"].points == 100 and r["p2"].points == 200 and r["p2"].place == 1


def test_lockout_tie_break_earlier_last_scoring_ok():
    subs = [sub(1, "p1", "C", "OK", t(18, 40)), sub(2, "p2", "A", "OK", t(18, 5)),
            sub(3, "p2", "B", "OK", t(18, 30))]
    r = by_player(score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs))
    assert r["p1"].points == r["p2"].points == 300
    assert r["p2"].place == 1


def test_lockout_draw():
    subs = [sub(1, "p1", "A", "OK", t(18, 5)), sub(2, "p2", "A", "OK", t(18, 5))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs)
    assert [r.place for r in rows] == [1, 1]


def test_lockout_early_finish_with_default_points():
    # 1500 total. p1 takes D+A+C = 800, 700 still open: 0 + 700 < 800 -> decided.
    subs = [sub(1, "p1", "D", "OK", t(18, 10)), sub(2, "p1", "A", "OK", t(18, 11)),
            sub(3, "p1", "C", "OK", t(18, 12))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs)
    assert open_points(rows, POINTS) == 700
    assert decided(rows, POINTS, {"p1", "p2"})


def test_lockout_not_finished_while_catchable():
    # p1 has C+D = 700, 800 open: p2 could still reach 800.
    subs = [sub(1, "p1", "D", "OK", t(18, 10)), sub(2, "p1", "C", "OK", t(18, 11))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs)
    assert not decided(rows, POINTS, {"p1", "p2"})


def test_lockout_exactly_801_wins_immediately():
    pts = {"A": 100, "B": 200, "C": 301, "D": 400, "E": 499}  # 1500 total, 801 reachable
    subs = [sub(1, "p1", "A", "OK", t(18, 1)), sub(2, "p1", "C", "OK", t(18, 2)),
            sub(3, "p1", "D", "OK", t(18, 3))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], pts, subs)
    assert by_player(rows)["p1"].points == 801
    assert open_points(rows, pts) == 699
    assert decided(rows, pts, {"p1", "p2"})


def test_lockout_not_decided_when_testing_pending():
    subs = [sub(1, "p1", "E", "OK", t(18, 10)), sub(2, "p1", "D", "OK", t(18, 11)),
            sub(3, "p2", "C", "TESTING", t(18, 12))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs)
    assert by_player(rows)["p1"].points == 900
    assert not decided(rows, POINTS, {"p1", "p2"})


def test_lockout_player_out_of_time_cannot_catch_up():
    subs = [sub(1, "p1", "A", "OK", t(18, 10))]
    rows = score_lockout(START, [lp("p1"), lp("p2")], POINTS, subs)
    assert not decided(rows, POINTS, {"p1", "p2"})
    assert decided(rows, POINTS, {"p1"})


def test_lockout_n_players():
    subs = [sub(1, "a", "E", "OK", t(18, 5)), sub(2, "b", "D", "OK", t(18, 6)),
            sub(3, "c", "C", "OK", t(18, 7)), sub(4, "d", "C", "OK", t(18, 8))]
    rows = score_lockout(START, [lp(x) for x in "abcd"], POINTS, subs)
    assert [(r.player, r.points, r.place) for r in rows] == [
        ("a", 500, 1), ("b", 400, 2), ("c", 300, 3), ("d", 0, 4)]


def test_lockout_all_taken_is_decided_even_if_tied():
    pts = {"A": 100, "B": 100}
    subs = [sub(1, "p1", "A", "OK", t(18, 5)), sub(2, "p2", "B", "OK", t(18, 6))]
    rows = score_lockout(START, [PlayerInput("p1", END, ("A", "B")), PlayerInput("p2", END, ("A", "B"))], pts, subs)
    assert decided(rows, pts, {"p1", "p2"})
    assert by_player(rows)["p1"].place == 1  # earlier last scoring OK


# ---------------- Team ----------------

def test_team_penalty_across_members():
    teams = {"T1": team_input("T1", END, PROBS), "T2": team_input("T2", END, PROBS)}
    members = {"m1": "T1", "m2": "T1", "x1": "T2"}
    subs = [sub(1, "m1", "A", "WRONG_ANSWER", t(18, 20)), sub(2, "m2", "A", "OK", t(18, 35)),
            sub(3, "m1", "A", "WRONG_ANSWER", t(18, 40)),   # after the team's OK: ignored
            sub(4, "x1", "A", "OK", t(18, 50))]
    r = by_player(score_team(START, teams, members, subs))
    assert r["T1"].penalty == 55 and r["T1"].solved == 1
    assert r["T2"].penalty == 50
    assert r["T2"].place == 1
