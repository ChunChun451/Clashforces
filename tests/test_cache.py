from app.cf import cache
from app.cf.parse import parse_contests, parse_problems, parse_submissions
from tests.conftest import load


def fill_cache():
    cache.store_contests(parse_contests(load("contest_list.json")["result"]))
    cache.store_problems(parse_problems(load("problemset.json")["result"]))


def test_cache_stores_problems_and_dup_groups(fresh_db):
    fill_cache()
    assert cache.get("2000D").group == cache.get("2001B").group == "2000D"
    assert cache.get("2000E").group == cache.get("2001C").group
    assert cache.get("2000C").group == "2000C"  # different name, no merge
    assert cache.get("2010C").group == "2010C"
    assert 2050 in cache.running_contest_ids()
    assert 2000 not in cache.running_contest_ids()
    assert "dp" in cache.all_tags()


def test_parse_submissions_missing_verdict_is_testing():
    subs = parse_submissions(load("user_status_alice.json")["result"], "alice")
    assert subs[0].verdict == "TESTING"
    assert subs[0].problem_key == "2010D"
    assert subs[2].verdict == "OK" and subs[2].time == 1750000100
