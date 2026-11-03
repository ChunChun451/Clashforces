import pytest

from app import db
from app.auth import handles, users, verify
from app.cf.client import HandleNotFound
from app.matches import locks
from tests.conftest import load

ALICE = {"handle": "alice", "rating": 1650, "maxRating": 1720}


def ce(sub_id, problem_key, t, verdict="COMPILATION_ERROR"):
    contest, idx = problem_key[:-1], problem_key[-1]
    return {"id": sub_id, "creationTimeSeconds": t, "verdict": verdict,
            "problem": {"contestId": int(contest), "index": idx}}


async def start_task(stub_cf, frozen_now):
    stub_cf.on("user.info", lambda p: [ALICE])
    view = await verify.start("alice")
    assert view["problem"]["key"] in {"2000A", "2010A"}
    assert view["expires_at"] == frozen_now.t + 120
    return view


async def test_verification_ce_inside_window_creates_user(cache_filled, stub_cf, frozen_now):
    view = await start_task(stub_cf, frozen_now)
    key, issued = view["problem"]["key"], view["issued_at"]
    stub_cf.on("user.status", lambda p: [ce(1, key, issued + 30)])
    frozen_now.t += 40
    view2, uid = await verify.check(view["task_id"])
    assert view2["status"] == "verified" and uid is not None
    u = users.by_id(uid)
    assert u["handle"] == "alice" and u["duel_rating"] == 1650


async def test_verification_rejects_old_ce_wrong_problem_and_wrong_verdict(cache_filled, stub_cf, frozen_now):
    view = await start_task(stub_cf, frozen_now)
    key, issued = view["problem"]["key"], view["issued_at"]
    other = "2000A" if key != "2000A" else "2010A"
    stub_cf.on("user.status", lambda p: [
        ce(1, key, issued - 5),                       # before the task was given
        ce(2, other, issued + 10),                    # wrong problem
        ce(3, key, issued + 10, "WRONG_ANSWER"),      # wrong verdict
        ce(4, key, issued + 200),                     # after the deadline
    ])
    frozen_now.t += 10
    view2, uid = await verify.check(view["task_id"])
    assert view2["status"] == "pending" and uid is None


async def test_verification_expires(cache_filled, stub_cf, frozen_now):
    view = await start_task(stub_cf, frozen_now)
    frozen_now.t += 120 + 61
    view2, _ = await verify.check(view["task_id"])
    assert view2["status"] == "expired"


async def test_check_is_throttled(cache_filled, stub_cf, frozen_now):
    view = await start_task(stub_cf, frozen_now)
    stub_cf.on("user.status", lambda p: [])
    frozen_now.t += 10
    await verify.check(view["task_id"])
    await verify.check(view["task_id"])  # same second: no second API call
    assert sum(1 for c in stub_cf.calls if c[0] == "user.status") == 1


async def test_unknown_handle_rejected(cache_filled, stub_cf, frozen_now):
    stub_cf.on("user.info", lambda p: HandleNotFound("nobody"))
    with pytest.raises(verify.VerifyError):
        await verify.start("nobody")


async def test_handle_change_moves_handle_and_old_owner_must_reverify(cache_filled, stub_cf, frozen_now):
    old = users.create("alice", 1500, 1500)
    me = users.create("bob", 1400, 1400)
    stub_cf.on("user.info", lambda p: [ALICE])
    view = await verify.start("alice", user_id=me)
    key, issued = view["problem"]["key"], view["issued_at"]
    stub_cf.on("user.status", lambda p: [ce(9, key, issued + 5)])
    frozen_now.t += 10
    _, uid = await verify.check(view["task_id"])
    assert uid == me
    assert users.by_id(me)["handle"] == "alice"
    assert users.by_id(old)["handle"] is None and users.by_id(old)["needs_reverify"] == 1


async def test_handle_change_blocked_while_in_match(cache_filled, stub_cf, frozen_now):
    me = users.create("bob", 1400, 1400)
    locks.acquire(me, "queue")
    stub_cf.on("user.info", lambda p: [ALICE])
    with pytest.raises(verify.VerifyError):
        await verify.start("alice", user_id=me)


async def test_duel_start_check_finds_renamed_handles(fresh_db, stub_cf):
    a = users.create("alice", 1600, 1600)
    b = users.create("Bob_77", 1300, 1300)
    g = users.create("ghost_user", 1200, 1200)

    def user_info(params):
        handles = params["handles"].split(";")
        if "ghost_user" in handles:
            return HandleNotFound("ghost_user")
        return load("user_info_ok.json")["result"]

    stub_cf.on("user.info", user_info)
    missing = await handles.check_players([a, b, g])
    assert missing == ["ghost_user"]
    assert users.by_id(g)["needs_reverify"] == 1
    assert users.by_id(a)["cf_rating"] == 1650  # refreshed
    assert await handles.check_players([a, b]) == []


def test_locks_one_active_thing(fresh_db):
    u = users.create("alice", 1600, 1600)
    db.execute("INSERT INTO matches(id, code, mode, rules, status, settings, created_at) VALUES (1,'ABCDEF','friend','icpc','lobby','{}',0)")
    locks.acquire(u, "match", 1)
    locks.acquire(u, "match", 1)  # same lock again is fine
    with pytest.raises(locks.LockError):
        locks.acquire(u, "queue")
    locks.release(u)
    locks.acquire(u, "queue")
