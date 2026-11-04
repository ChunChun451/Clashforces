"""Gym and mashup problems in hosted contests and custom lists."""

from app import db
from app.cf import cache
from app.cf.parse import contest_url, problem_url, submission_url
from app.core import custom_list
from app.matches import contest, service, store, view
from app.poller.poller import poller
from tests.test_contest import HANDLES, form, world  # noqa: F401  (fixture)

GYM = "https://codeforces.com/gym/106608/problem/A 106608B 2000C"


def gym_raw(i, key, verdict, t, name):
    return {"id": i, "creationTimeSeconds": t, "verdict": verdict,
            "problem": {"contestId": int(key[:-1]), "index": key[-1], "name": name}}


def test_urls_point_to_gym_for_gym_ids():
    assert problem_url(2043, "C") == "https://codeforces.com/contest/2043/problem/C"
    assert problem_url(106608, "A") == "https://codeforces.com/gym/106608/problem/A"
    assert contest_url(106608) == "https://codeforces.com/gym/106608"
    assert submission_url(106608, 5) == "https://codeforces.com/gym/106608/submission/5"
    assert problem_url(None, "A") is None


def test_parser_accepts_gym_links_and_ids():
    keys, rest = custom_list.parse(GYM)
    assert keys == ["106608A", "106608B", "2000C"] and rest == []
    res = custom_list.check("106608A 106608A 99999Z", {}, set(), {})
    assert [p.key for p in res.problems] == ["106608A"]
    assert any("twice" in e for e in res.errors)          # duplicates still caught
    assert any("99999Z" in e for e in res.errors)          # below the gym range: still must exist


async def test_contest_with_gym_problems_scores_and_learns_names(world):  # noqa: F811
    w = world
    code = contest.create(w.u["host"], form(format="solo", max_players=12, freeze_min=0, scheduled_at=None,
                                            problems=GYM))
    for h in HANDLES[:2]:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    start = m["start_time"]
    w.now.t = start + 1   # problems show once the countdown is over

    probs = {p["key"]: p for p in view.build(m, w.u["c0"]["id"])["problems"]}
    assert probs["106608A"]["url"] == "https://codeforces.com/gym/106608/problem/A"
    assert probs["106608A"]["name"] == "106608A"            # unknown until someone submits

    w.subs["c0"] += [gym_raw(1, "106608A", "OK", start + 60, "Divide it all again")]
    w.now.t = start + 120
    await poller.check(w.u["c0"]["id"])
    st = view.build(m, None)
    assert st["standings"][0]["handle"] == "c0"
    probs = {p["key"]: p for p in view.build(m, w.u["c0"]["id"])["problems"]}
    assert probs["106608A"]["name"] == "Divide it all again"


async def test_problemset_refresh_keeps_gym_rows(world):  # noqa: F811
    cache.add_gym(custom_list.check("106608C", {}, set(), {}).problems)
    assert cache.get("106608C") is not None
    cache.store_problems([p for p in cache.problems() if p.contest_id < 100000])
    assert cache.get("106608C") is not None
    assert db.scalar("SELECT COUNT(*) FROM problems WHERE contest_id < 100000") > 0


def test_random_selection_never_picks_gym(world):  # noqa: F811
    from app.core.selection import base_pool
    cache.add_gym(custom_list.check("106608C", {}, set(), {}).problems)
    assert all(p.contest_id < 100000 for p in base_pool(cache.problems(), set()))
