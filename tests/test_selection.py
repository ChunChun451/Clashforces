import random
from dataclasses import replace

import pytest

from app.cf.parse import parse_contests, parse_problems
from app.core import custom_list
from app.core.dedup import dup_groups
from app.core.selection import Filters, SelectionError, select, select_per_player, targets
from tests.conftest import load

RUNNING = {2050}


@pytest.fixture(scope="module")
def problems():
    probs = parse_problems(load("problemset.json")["result"])
    groups = dup_groups(probs, parse_contests(load("contest_list.json")["result"]))
    return [replace(p, dup_group=groups[p.key]) for p in probs]


def keys(ps):
    return [p.key for p in ps]


def test_targets_spread():
    assert targets(1400, 2000, 4) == [1400, 1600, 1800, 2000]
    assert targets(800, 1000, 3) == [800, 900, 1000]
    assert targets(1400, 2000, 1) == [1700]
    assert targets(1500, 1500, 2) == [1500, 1500]


def test_spread_picks_close_to_targets_in_order(problems):
    f = Filters(4, 1400, 2000)
    for seed in range(20):
        chosen = select(problems, RUNNING, set(), f, random.Random(seed))
        assert [p.rating for p in chosen] == [1400, 1600, 1800, 2000]
        assert len({p.group for p in chosen}) == 4


def test_never_picks_unrated_special_running(problems):
    f = Filters(10, 800, 3500)
    for seed in range(20):
        chosen = keys(select(problems, RUNNING, set(), f, random.Random(seed)))
        assert "1950F" not in chosen and "1950G" not in chosen  # unrated
        assert "1951A" not in chosen                            # *special
        assert "2050A" not in chosen and "2050B" not in chosen  # running contest


def test_div1_div2_copy_never_twice(problems):
    f = Filters(2, 1800, 1800)  # only 2000D, 2001B (same problem) and 1960C are 1800
    for seed in range(20):
        chosen = keys(select(problems, RUNNING, set(), f, random.Random(seed)))
        assert "1960C" in chosen
        assert len({"2000D", "2001B"} & set(chosen)) == 1


def test_touched_copy_excludes_both(problems):
    # The player touched Div.1 B; the Div.2 D copy must also be excluded.
    by_key = {p.key: p for p in problems}
    touched = {by_key["2001B"].group}
    f = Filters(1, 1800, 1800)
    for seed in range(20):
        assert keys(select(problems, RUNNING, touched, f, random.Random(seed))) == ["1960C"]
    with pytest.raises(SelectionError) as e:
        select(problems, RUNNING, touched | {by_key["1960C"].group}, f)
    assert e.value.stage == "touched"


def test_tags_include_and_avoid(problems):
    f = Filters(3, 1400, 2100, tags_include=("dp",), tags_avoid=("greedy",))
    for seed in range(10):
        chosen = select(problems, RUNNING, set(), f, random.Random(seed))
        for p in chosen:
            assert "dp" in p.tags and "greedy" not in p.tags


def test_avoid_interactive(problems):
    f = Filters(4, 1500, 1700, avoid_interactive=True)
    for seed in range(10):
        chosen = select(problems, RUNNING, set(), f, random.Random(seed))
        assert all("interactive" not in p.tags for p in chosen)


@pytest.mark.parametrize("filters,stage", [
    (Filters(5, 2500, 3500), "rating"),
    (Filters(3, 800, 3500, tags_include=("flows",)), "tags_include"),
    (Filters(10, 1400, 2100, tags_avoid=("dp", "greedy", "graphs")), "tags_avoid"),
    (Filters(5, 1500, 1700, avoid_interactive=True), "interactive"),
])
def test_error_names_filter_to_relax(problems, filters, stage):
    with pytest.raises(SelectionError) as e:
        select(problems, RUNNING, set(), filters)
    assert e.value.stage == stage
    assert e.value.message


def test_bad_settings_rejected(problems):
    for f in [Filters(0, 800, 900), Filters(2, 850, 900), Filters(2, 1000, 900),
              Filters(2, 800, 3600), Filters(2, 800, 900, ("dp",), ("dp",))]:
        with pytest.raises(SelectionError) as e:
            select(problems, RUNNING, set(), f)
        assert e.value.stage == "settings"


def test_per_player_ranges_disjoint(problems):
    players = [("p1", "alice", 1300, 1500, set()), ("p2", "bob", 1400, 1600, set())]
    out = select_per_player(problems, RUNNING, players, Filters(2, 0, 0), random.Random(1))
    g1 = {p.group for p in out["p1"]}
    g2 = {p.group for p in out["p2"]}
    assert not g1 & g2
    assert all(1300 <= p.rating <= 1500 for p in out["p1"])
    assert all(1400 <= p.rating <= 1600 for p in out["p2"])


# ---------- custom list ----------

def test_custom_parse_formats():
    text = "2043C, 2051 e\n2060/D https://codeforces.com/contest/1950/problem/B2 https://codeforces.com/problemset/problem/2000/A"
    ks, bad = custom_list.parse(text)
    assert ks == ["2043C", "2051E", "2060D", "1950B2", "2000A"]
    assert bad == []
    _, bad = custom_list.parse("2043C, hello, 12")
    assert bad == ["hello", "12"]


def test_custom_check(problems):
    by_key = {p.key: p for p in problems}
    touched = {"alice": {by_key["2010A"].group}}
    res = custom_list.check("2010A 2000D 2001B 2050A 9999Z", by_key, RUNNING, touched)
    assert keys(res.problems) == ["2010A", "2000D"]
    assert res.duplicates == ["2001B"]
    assert res.running == ["2050A"]
    assert res.unknown == ["9999Z"]
    assert res.warnings == [("alice", "2010A")]  # warning only, still allowed
    assert len(res.errors) == 3


def test_requiring_interactive_while_avoiding_it_is_rejected():
    from app.core.selection import filter_errors
    clash = Filters(3, 1200, 1600, ("interactive",), (), True)
    assert any("interactive" in e for e in filter_errors(clash))
    assert filter_errors(Filters(3, 1200, 1600, ("interactive",), (), False)) == []
    assert filter_errors(Filters(3, 1200, 1600, ("dp",), (), True)) == []
