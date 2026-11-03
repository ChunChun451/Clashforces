from app.core.stats import MatchOutcome, Solve, head_to_head, record, solve_times, tag_win_rates


def test_record_overall_and_per_mode():
    os = [MatchOutcome(1, "ranked", 1, False), MatchOutcome(2, "ranked", 2, False),
          MatchOutcome(3, "friend", 1, True), MatchOutcome(4, "group", 1, False)]
    r = record(os)
    assert r["overall"] == {"win": 2, "loss": 1, "draw": 1}
    assert r["by_mode"]["ranked"] == {"win": 1, "loss": 1, "draw": 0}
    assert r["by_mode"]["friend"]["draw"] == 1


def test_solve_times_by_rating_and_tag():
    s = [Solve(1600, ("dp",), 30), Solve(1600, ("dp", "greedy"), 32), Solve(1600, ("greedy",), 10),
         Solve(None, ("dp",), 5), Solve(1600, ("*special",), 1)]
    out = {(x["rating"], x["tag"]): x for x in solve_times(s)}
    assert out[(1600, "dp")]["avg_minutes"] == 31 and out[(1600, "dp")]["count"] == 2
    assert out[(1600, "greedy")]["avg_minutes"] == 21
    assert (1600, "*special") not in out


def test_tag_win_rates():
    os = [MatchOutcome(1, "ranked", 1, False, frozenset({"dp", "math"})),
          MatchOutcome(2, "ranked", 2, False, frozenset({"dp"})),
          MatchOutcome(3, "ranked", 1, True, frozenset({"dp"}))]
    out = {x["tag"]: x for x in tag_win_rates(os)}
    assert out["dp"]["matches"] == 3 and out["dp"]["win_rate"] == 0.5
    assert out["math"]["win_rate"] == 1.0


def test_head_to_head():
    assert head_to_head({1: 1, 2: 2, 3: 1, 9: 1}, {1: 2, 2: 1, 3: 1, 8: 2}) == {"win": 1, "loss": 1, "draw": 1}
