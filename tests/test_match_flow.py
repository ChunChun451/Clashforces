import asyncio
import random

import pytest

from app import db
from app.auth import users
from app.cf.client import CFUnavailable, HandleNotFound
from app.matches import lifecycle, locks, service, store, view
from app.poller.poller import poller

T0 = 1_750_000_000


@pytest.fixture
def world(cache_filled, stub_cf, frozen_now):
    poller.watches.clear()
    poller.interactive_due.clear()
    lifecycle._sent.clear()
    lifecycle._final_next.clear()
    lifecycle._final_busy.clear()
    lifecycle.setup()
    frozen_now.t = T0
    a = users.create("alice", 1650, 1700)
    b = users.create("Bob_77", 1320, 1400)
    db.execute("UPDATE users SET history_synced = 1")
    subs = {"alice": [], "Bob_77": []}
    stub_cf.on("user.info", lambda p: [{"handle": h, "rating": 1500, "maxRating": 1500}
                                       for h in p["handles"].split(";")])
    stub_cf.on("user.status", lambda p: list(reversed(subs[p["handle"]])))

    class W:
        pass
    w = W()
    w.a, w.b, w.subs, w.cf, w.now = users.by_id(a), users.by_id(b), subs, stub_cf, frozen_now
    return w


def raw(i, key, verdict, t):
    return {"id": i, "creationTimeSeconds": t, "verdict": verdict,
            "problem": {"contestId": int(key[:-1]), "index": key[-1]}}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def start_duel(w, rules="icpc", settings=None, seed=1):
    s = {"count": 3, "rating_min": 1400, "rating_max": 1800, "duration_min": 60}
    s.update(settings or {})
    code = service.create(w.a, "friend", rules, s)
    service.join(w.b, code)
    match = store.match_by_code(code)
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    assert await service.start(match["id"], random.Random(seed))
    return store.match_by_code(code)


async def finish(w, match):
    """Run the ticker past the end until the result is final."""
    for _ in range(10):
        await lifecycle.tick()
        await settle()
        if store.match_by_id(match["id"])["result_status"] == "final":
            break
    return store.match_by_id(match["id"])


async def test_friend_duel_icpc_end_to_end(world):
    w = world
    match = await start_duel(w)
    assert match["status"] == "running"
    assert match["start_time"] == T0 + 5 and match["end_time"] == T0 + 5 + 3600
    probs = store.problems(match["id"])
    assert [p["label"] for p in probs] == ["A", "B", "C"]
    a_key = probs[0]["problem_key"]
    start = match["start_time"]
    with pytest.raises(service.MatchError):
        service.join(w.a, "ZZZZZZ")

    # alice: WA at 10, WA at 15, OK at 22 -> 62. bob: OK at 30 -> 30.
    w.subs["alice"] += [raw(1, a_key, "WRONG_ANSWER", start + 600), raw(2, a_key, "WRONG_ANSWER", start + 900),
                        raw(3, a_key, "OK", start + 22 * 60)]
    w.subs["Bob_77"] += [raw(4, a_key, "OK", start + 30 * 60),
                         raw(5, a_key, "OK", start - 100)]  # before start: ignored
    w.now.t = start + 31 * 60
    await poller.check(w.a["id"])
    await poller.check(w.b["id"])
    st = view.build(store.match_by_id(match["id"]), w.a["id"])
    rows = {r["handle"]: r for r in st["standings"]}
    assert rows["alice"]["penalty"] == 62 and rows["Bob_77"]["penalty"] == 30
    assert rows["Bob_77"]["place"] == 1

    # the clock runs out; results come only from Codeforces times
    w.now.t = match["end_time"] + 30
    final = await finish(w, match)
    assert final["status"] == "ended" and final["end_reason"] == "time"
    assert final["result_status"] == "final"
    pr = {p["user_id"]: p for p in store.players(match["id"])}
    assert pr[w.b["id"]]["place"] == 1 and pr[w.a["id"]]["place"] == 2
    assert locks.current(w.a["id"]) is None  # free to play again
    up = {r["problem_key"] for r in db.all("SELECT problem_key FROM upsolve WHERE user_id = ?", (w.a["id"],))}
    assert up == {p["problem_key"] for p in probs[1:]}


async def test_result_pending_while_api_down(world):
    w = world
    match = await start_duel(w)
    w.now.t = match["end_time"] + 30
    w.cf.on("user.status", lambda p: CFUnavailable("down"))
    m = await finish(w, match)
    assert m["status"] == "ended" and m["result_status"] == "pending"
    # the API comes back later: result is finalised from in-window submissions
    a_key = store.problems(match["id"])[0]["problem_key"]
    w.subs["alice"].append(raw(9, a_key, "OK", match["start_time"] + 120))
    w.cf.on("user.status", lambda p: list(reversed(w.subs[p["handle"]])))
    w.now.t += 120
    m = await finish(w, match)
    assert m["result_status"] == "final"
    assert {p["user_id"]: p["place"] for p in store.players(match["id"])}[w.a["id"]] == 1


async def test_testing_verdict_delays_final(world):
    w = world
    match = await start_duel(w)
    a_key = store.problems(match["id"])[0]["problem_key"]
    w.subs["alice"].append({"id": 7, "creationTimeSeconds": match["end_time"] - 5,
                            "problem": {"contestId": int(a_key[:-1]), "index": a_key[-1]}})  # no verdict yet
    w.now.t = match["end_time"] + 30
    m = await finish(w, match)
    assert m["result_status"] == "pending"
    w.subs["alice"][-1]["verdict"] = "OK"
    w.now.t += 30
    m = await finish(w, match)
    assert m["result_status"] == "final"


async def test_lockout_clinch_ends_early(world):
    w = world
    match = await start_duel(w, rules="lockout")
    probs = store.problems(match["id"])
    assert [p["points"] for p in probs] == [100, 200, 300]
    start = match["start_time"]
    w.subs["alice"] += [raw(1, probs[2]["problem_key"], "OK", start + 60),
                        raw(2, probs[1]["problem_key"], "OK", start + 120)]
    w.now.t = start + 200
    await poller.check(w.a["id"])
    await settle()
    m = store.match_by_id(match["id"])
    assert m["status"] == "ended" and m["end_reason"] == "clinched"
    assert m["result_status"] == "final"


async def test_forfeit_ends_duel(world):
    w = world
    match = await start_duel(w)
    w.now.t = match["start_time"] + 100
    service.forfeit(w.a, match["code"])
    m = store.match_by_id(match["id"])
    assert m["status"] == "ended" and m["end_reason"] == "forfeit"
    w.now.t += 60
    m = await finish(w, match)
    assert {p["user_id"]: p["place"] for p in store.players(match["id"])} == {w.b["id"]: 1, w.a["id"]: 2}


async def test_renamed_handle_blocks_start(world):
    w = world
    code = service.create(w.a, "friend", "icpc", {"count": 2, "rating_min": 1400, "rating_max": 1600})
    service.join(w.b, code)
    match = store.match_by_code(code)
    w.cf.on("user.info", lambda p: HandleNotFound("Bob_77"))
    assert not await service.start(match["id"])
    m = store.match_by_id(match["id"])
    assert m["status"] == "lobby"
    assert users.by_id(w.b["id"])["needs_reverify"] == 1
    with pytest.raises(service.MatchError):
        service.join(users.by_id(w.b["id"]), code)  # must re-verify first


async def test_selection_failure_explains(world):
    w = world
    code = service.create(w.a, "friend", "icpc", {"count": 5, "rating_min": 2500, "rating_max": 3500})
    service.join(w.b, code)
    match = store.match_by_code(code)
    assert not await service.start(match["id"])
    assert store.match_by_id(match["id"])["status"] == "lobby"


async def test_one_active_match(world):
    w = world
    service.create(w.a, "friend", "icpc", {})
    with pytest.raises(service.MatchError):
        service.create(w.a, "friend", "icpc", {})


async def test_extra_time_and_penalty_handicap(world):
    w = world
    match = await start_duel(w, settings={"handicap": {"type": "extra_time", "user_id": w.b["id"], "minutes": 15}})
    ends = {p["user_id"]: p["end_time"] for p in store.players(match["id"])}
    assert ends[w.b["id"]] - ends[w.a["id"]] == 15 * 60
    service.forfeit(w.a, match["code"])


async def test_rematch_new_problems(world):
    w = world
    match = await start_duel(w)
    w.now.t = match["end_time"] + 30
    await finish(w, match)
    code = service.rematch(w.a, match["code"])
    service.join(w.b, code)
    new = store.match_by_code(code)
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (new["id"],))
    assert await service.start(new["id"], random.Random(2))
    old_keys = {p["problem_key"] for p in store.problems(match["id"])}
    new_keys = {p["problem_key"] for p in store.problems(new["id"])}
    assert not old_keys & new_keys


async def test_freeze_hides_others_then_reveals(world):
    w = world
    match = await start_duel(w, settings={"freeze_min": 10})
    probs = store.problems(match["id"])
    start, end = match["start_time"], match["end_time"]
    w.subs["alice"].append(raw(1, probs[0]["problem_key"], "OK", end - 300))   # inside the freeze
    w.subs["Bob_77"].append(raw(2, probs[1]["problem_key"], "OK", start + 60))  # before the freeze
    w.now.t = end - 200
    await poller.check(w.a["id"])
    await poller.check(w.b["id"])
    m = store.match_by_id(match["id"])
    bob_view = {r["handle"]: r for r in view.build(m, w.b["id"])["standings"]}
    assert bob_view["alice"]["solved"] == 0
    assert bob_view["alice"]["cells"][0]["state"] == "frozen"
    alice_view = {r["handle"]: r for r in view.build(m, w.a["id"])["standings"]}
    assert alice_view["alice"]["solved"] == 1  # you always see yourself
    spect = {r["handle"]: r for r in view.build(m, None)["standings"]}
    assert spect["alice"]["solved"] == 0
    w.now.t = end + 30
    m = await finish(w, match)
    payload = view.result_payload(m)
    assert payload["reveal"] and payload["reveal"]["steps"][0]["label"] == "A"
    assert {r["handle"]: r["solved"] for r in payload["rows"]} == {"alice": 1, "Bob_77": 1}
