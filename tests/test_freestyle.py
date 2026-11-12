"""Freestyle: a random problem for everyone, plus one problem set by each opponent."""

import random

import pytest

from app import db
from app.auth import users
from app.cf import cache
from app.matches import service, settings as settings_mod, store, view
from app.poller.poller import poller
from tests.test_match_flow import finish, raw, settle, world  # noqa: F401  (world is a fixture)


def test_freestyle_settings_rules():
    base = {"count": 1, "rating_min": 1100, "rating_max": 1500, "freestyle": True}
    assert settings_mod.clean(base, "icpc", "group")["freestyle"] is True
    for rules, mode, extra in [("lockout", "group", {}), ("icpc", "team", {}), ("icpc", "group", {"custom_problems": "2043C"})]:
        with pytest.raises(settings_mod.SettingsError):
            settings_mod.clean({**base, **extra}, rules, mode)


def _picks(n):
    """n distinct problems from the cache, outside the random range so they can't collide with it."""
    seen, out = set(), []
    for p in cache.problems():
        if p.rating and p.rating >= 2000 and p.group not in seen:
            seen.add(p.group)
            out.append(p.key)
        if len(out) == n:
            return out


async def test_freestyle_room(world):
    w = world
    carl = users.by_id(users.create("carl", 1500, 1500))
    db.execute("UPDATE users SET history_synced = 1")
    w.subs["carl"] = []
    code = service.create(w.a, "group", "icpc", {"count": 1, "rating_min": 1100, "rating_max": 1500, "freestyle": True})
    service.join(w.b, code)
    service.join(carl, code)
    match = store.match_by_code(code)

    with pytest.raises(service.MatchError):
        service.set_ready(w.a, code, True)                     # no problem set yet
    pa, pb, pc = _picks(3)
    with pytest.raises(service.MatchError):
        service.set_pick(w.a, code, "2043C 2051E")            # one problem only
    assert service.set_pick(w.a, code, f"https://codeforces.com/problemset/problem/{pa[:-1]}/{pa[-1]}")["pick"]["key"] == pa
    service.set_pick(w.b, code, pb)

    # before the start, others only see that a pick is set, never which one
    st = view.build(store.match_by_id(match["id"]), w.b["id"])
    assert st["me"]["pick"]["key"] == pb
    assert [p["picked"] for p in st["players"]] == [True, True, False]
    assert pa not in str(st)
    assert service.start_blocker(match, store.players(match["id"]))

    # same problem as alice: refused at the start, back to the lobby
    service.set_pick(carl, code, pa)
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    assert not await service.start(match["id"], random.Random(1))
    service.set_pick(carl, code, pc)
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    assert await service.start(match["id"], random.Random(1))

    match = store.match_by_id(match["id"])
    rows = store.problems(match["id"])
    sets = {uid: store.player_problem_keys(rows, uid) for uid in (w.a["id"], w.b["id"], carl["id"])}
    common = sets[w.a["id"]][0]
    assert sets[w.a["id"]] == (common, pb, pc)
    assert sets[w.b["id"]] == (common, pa, pc)
    assert sets[carl["id"]] == (common, pa, pb)
    assert common not in (pa, pb, pc) and 1100 <= cache.get(common).rating <= 1500

    # alice solves her own pick (doesn't count) and bob's; carl solves the common one
    start = match["start_time"]
    w.subs["alice"] += [raw(1, pa, "OK", start + 60), raw(2, pb, "OK", start + 120)]
    w.subs["carl"] += [raw(3, common, "OK", start + 300)]
    w.now.t = start + 400
    for u in (w.a, w.b, carl):
        await poller.check(u["id"])
    await settle()
    st = view.build(store.match_by_id(match["id"]), None)
    by = {r["handle"]: r for r in st["standings"]}
    assert by["alice"]["solved"] == 1 and by["carl"]["solved"] == 1 and by["Bob_77"]["solved"] == 0
    assert [c["label"] for c in by["alice"]["cells"]] == ["A", "B", "C"]
    assert {p["key"]: p["set_by"] for p in st["problems"]}[pb] == "Bob_77"

    w.now.t = match["end_time"] + 30
    await finish(w, match)
    places = {p["handle"]: p["place"] for p in store.players(match["id"])}
    assert places["Bob_77"] == 3


async def test_fixed_common_until_cutoff(world, monkeypatch):
    w = world
    carl = users.by_id(users.create("carl", 1500, 1500))
    db.execute("UPDATE users SET history_synced = 1")
    fixed = [p.key for p in cache.problems() if p.rating == 1500][0]
    monkeypatch.setattr(service, "FIXED_COMMON", {"bob_77": fixed})
    monkeypatch.setattr(service, "FIXED_COMMON_UNTIL", w.now.t + 100)
    pa, pb, pc = _picks(3)
    for cutoff_passed in (False, True):
        if cutoff_passed:
            w.now.t += 200
        code = service.create(w.a, "group", "icpc", {"count": 1, "rating_min": 1100, "rating_max": 1500, "freestyle": True})
        service.join(w.b, code)
        service.join(carl, code)
        for u, k in ((w.a, pa), (w.b, pb), (carl, pc)):
            service.set_pick(u, code, k)
        match = store.match_by_code(code)
        rows = service.choose_problems(match, store.players(match["id"]), random.Random(1))
        assert (rows[0][2] == fixed) is not cutoff_passed
        for u in (w.a, w.b, carl):
            service.leave(u, code)
