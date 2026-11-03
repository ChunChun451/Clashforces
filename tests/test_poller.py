from collections import Counter

from app import config, db
from app.auth import users
from app.core import schedule
from app.core.schedule import Watch
from app.poller import history, ingest
from app.poller.poller import Poller

NOW = 1_750_000_000


def w(uid, last_activity=None, added=NOW - 3600, last_checked=0):
    return Watch(uid, f"u{uid}", NOW - 3600, NOW + 3600, added_at=added,
                 last_checked=last_checked, last_activity=last_activity)


# ---------- schedule (pure) ----------

def test_tiers():
    assert schedule.tier(w(1, last_activity=NOW - 60), NOW) == "HOT"
    assert schedule.tier(w(1, last_activity=NOW - 600), NOW) == "WARM"
    assert schedule.tier(w(1, last_activity=NOW - 3000), NOW) == "COLD"
    assert schedule.tier(w(1), NOW) == "COLD"
    assert schedule.tier(w(1, added=NOW - 60), NOW) == "WARM"  # young match


def test_min_interval_floors():
    hot = w(1, last_activity=NOW - 10, last_checked=NOW - 10)
    assert not schedule.is_due(hot, NOW)
    hot.last_checked = NOW - config.MIN_INTERVAL_S["HOT"]
    assert schedule.is_due(hot, NOW)


def test_weighted_share_under_overload():
    """Simulate 1 poll per 2s over an hour for 130 players; HOT ~4x COLD."""
    watches = []
    for i in range(130):
        if i < 40:
            watches.append(w(i, last_activity=NOW))       # stays HOT: activity refreshed below
        elif i < 85:
            watches.append(w(i, last_activity=NOW - 600))
        else:
            watches.append(w(i))
    counts = Counter()
    t = NOW
    for _ in range(1800):
        for x in watches[:40]:
            x.last_activity = t
        for x in watches[40:85]:
            x.last_activity = t - 600
        picked = schedule.pick(watches, t)
        if picked:
            picked.last_checked = t
            counts[picked.user_id] += 1
        t += 2
    hot = sum(counts[i] for i in range(40)) / 40
    warm = sum(counts[i] for i in range(40, 85)) / 45
    cold = sum(counts[i] for i in range(85, 130)) / 45
    assert all(counts[i] > 0 for i in range(130))  # nobody starves
    assert 3.0 < hot / cold < 5.0
    assert 1.5 < warm / cold < 2.5


def test_floors_when_light_load():
    watches = [w(1, last_activity=NOW)]
    t, checks = NOW, 0
    for _ in range(300):  # 10 minutes
        watches[0].last_activity = t
        p = schedule.pick(watches, t)
        if p:
            p.last_checked = t
            checks += 1
        t += 2
    assert checks <= 600 / config.MIN_INTERVAL_S["HOT"] + 1


# ---------- ingest / history / poller with a stub CF ----------

def raw_sub(i, key, verdict, t):
    d = {"id": i, "creationTimeSeconds": t, "problem": {"contestId": int(key[:-1]), "index": key[-1]}}
    if verdict:
        d["verdict"] = verdict
    return d


def test_ingest_touched_and_upsolve_done(cache_filled, frozen_now):
    uid = users.create("alice", 1500, 1500)
    db.execute("INSERT INTO matches(id, code, mode, rules, status, settings, created_at) VALUES (1,'AAAAAA','friend','icpc','ended','{}',0)")
    db.execute("INSERT INTO upsolve(user_id, problem_key, match_id, added_at) VALUES (?, '2001B', 1, 0)", (uid,))
    from app.cf.parse import parse_submissions
    subs = parse_submissions([raw_sub(10, "2000D", "OK", NOW)], "alice")  # Div.2 copy of 2001B
    changed = ingest.store(uid, subs)
    assert len(changed) == 1
    assert ingest.store(uid, subs) == []  # unchanged second time
    assert db.scalar("SELECT done_at FROM upsolve WHERE user_id = ?", (uid,)) is not None
    assert "2000D" in ingest.touched_groups(uid)


async def test_history_full_then_incremental(cache_filled, stub_cf):
    uid = users.create("alice", 1500, 1500)
    stub_cf.on("user.status", lambda p: [raw_sub(5, "2010A", "OK", NOW - 100), raw_sub(4, "1950B", "WRONG_ANSWER", NOW - 200)])
    await history.sync(uid)
    assert db.scalar("SELECT history_synced FROM users WHERE id = ?", (uid,)) == 1
    assert {"2010A", "1950B"} <= ingest.touched_groups(uid)
    stub_cf.on("user.status", lambda p: [raw_sub(7, "1960A", "WRONG_ANSWER", NOW), raw_sub(5, "2010A", "OK", NOW - 100)])
    await history.sync(uid)
    assert "1960A" in ingest.touched_groups(uid)
    assert stub_cf.calls[-1][1]["from"] == 1  # incremental call is paged


async def test_check_detects_new_submission_and_testing(cache_filled, stub_cf, frozen_now):
    uid = users.create("alice", 1500, 1500)
    p = Poller()
    seen = []
    p.listeners.append(lambda u, ch: seen.append((u, [s.verdict for s in ch])))
    p.watch(uid, "alice", NOW - 600, NOW + 3000)
    frozen_now.t = NOW
    stub_cf.on("user.status", lambda q: [raw_sub(20, "2010D", None, NOW - 30)])
    await p.check(uid)
    assert seen == [(uid, ["TESTING"])]
    assert uid in p.interactive_due  # TESTING => re-check soon
    stub_cf.on("user.status", lambda q: [raw_sub(20, "2010D", "OK", NOW - 30)])
    await p.check(uid)
    assert seen[-1] == (uid, ["OK"])
    assert p.watches[uid].last_activity == NOW - 30  # Codeforces time, not ours


async def test_burst_pages_back(cache_filled, stub_cf, frozen_now):
    uid = users.create("alice", 1500, 1500)
    p = Poller()
    p.watch(uid, "alice", NOW - 3600, NOW + 3000)

    def status(q):
        start, count = int(q["from"]), int(q["count"])
        allsubs = [raw_sub(100 - i, "2010A", "WRONG_ANSWER", NOW - 10 * i) for i in range(30)]
        return allsubs[start - 1:start - 1 + count]

    stub_cf.on("user.status", status)
    changed = await p.check(uid)
    assert len(changed) == 30


async def test_final_check_reports_testing(cache_filled, stub_cf, frozen_now):
    uid = users.create("alice", 1500, 1500)
    p = Poller()
    stub_cf.on("user.status", lambda q: [raw_sub(30, "2010A", "TESTING", NOW - 10)])
    assert await p.final_check(uid, "alice", NOW - 600, NOW) is False
    stub_cf.on("user.status", lambda q: [raw_sub(30, "2010A", "OK", NOW - 10)])
    assert await p.final_check(uid, "alice", NOW - 600, NOW) is True
    assert stub_cf.calls[-1][2].name == "URGENT"


def test_hints(fresh_db, frozen_now):
    p = Poller()
    p.watch(1, "alice", NOW - 600, NOW + 3000)
    frozen_now.t = NOW
    assert p.hard_hint(1)
    assert not p.hard_hint(1)  # cooldown
    assert p.interactive_due[1] == NOW + config.HARD_HINT_DELAY_S
    assert not p.hard_hint(2)  # not watched
    p.soft_hint(1, away_s=5)
    p.soft_hint(1, away_s=60)
    assert p.watches[1].last_activity == NOW


async def test_poll_loop_checks_each_player_once_then_waits(cache_filled, stub_cf, frozen_now):
    import asyncio
    a = users.create("alice", 1500, 1500)
    b = users.create("bob", 1500, 1500)
    p = Poller()
    frozen_now.t = NOW
    p.watch(a, "alice", NOW - 600, NOW + 3000)
    p.watch(b, "bob", NOW - 600, NOW + 3000)
    stub_cf.on("user.status", lambda q: [])
    task = asyncio.create_task(p._poll_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    handles = [c[1]["handle"] for c in stub_cf.calls]
    assert sorted(handles) == ["alice", "bob"]  # min-interval floor stops repeats


async def test_upsolve_refresh_marks_done_and_cooldown(cache_filled, stub_cf, frozen_now):
    from app.poller import upsolve
    uid = users.create("alice", 1500, 1500)
    db.execute("UPDATE users SET history_synced = 1, last_sub_id = 1 WHERE id = ?", (uid,))
    db.execute("INSERT INTO matches(id, code, mode, rules, status, settings, created_at) VALUES (1,'AAAAAA','friend','icpc','ended','{}',0)")
    db.execute("INSERT INTO upsolve(user_id, problem_key, match_id, added_at) VALUES (?, '2010D', 1, 0)", (uid,))
    stub_cf.on("user.status", lambda q: [raw_sub(50, "2010D", "OK", NOW)])
    assert await upsolve.refresh(uid)
    assert upsolve.items(uid)[0]["done_at"] is not None
    assert not await upsolve.refresh(uid)  # cooldown
