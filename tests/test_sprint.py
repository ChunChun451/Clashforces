"""Sprint: Lockout with one problem open at a time."""

import pytest

from app.core import sprint
from app.matches import lifecycle, settings as settings_mod, store, view
from app.models import OK, Sub
from app.poller.poller import poller
from tests.test_match_flow import raw, settle, start_duel, world  # noqa: F401  (world is a fixture)


def ok(i, key, t, who=1):
    return Sub(i, who, key, OK, t)


# ---------- the pure schedule ----------

def test_claim_opens_the_next_problem():
    steps = sprint.schedule(0, ["A", "B", "C"], [ok(1, "B", 10), ok(2, "A", 50), ok(3, "B", 90)], None, now=100)
    assert [(s.key, s.revealed_at, s.closed_at, s.claimed) for s in steps] == [
        ("A", 0, 50, True), ("B", 50, 90, True), ("C", 90, None, False)]
    # B solved before it opened doesn't count
    assert [s.id for s in sprint.in_window(steps, [ok(1, "B", 10), ok(2, "A", 50), ok(3, "B", 90)])] == [2, 3]


def test_time_limit_skips_and_waits_for_the_clock():
    assert len(sprint.schedule(0, ["A", "B"], [], 300, now=299)) == 1
    steps = sprint.schedule(0, ["A", "B"], [ok(1, "A", 300)], 300, now=301)  # too late for A
    assert [(s.key, s.closed_at, s.claimed) for s in steps] == [("A", 300, False), ("B", None, False)]
    assert sprint.skipped(steps) == {"A"}
    done = sprint.schedule(0, ["A", "B"], [ok(2, "B", 400)], 300, now=1000)
    assert sprint.finished(done, ["A", "B"])


def test_same_second_solves_both_count():
    subs = [ok(1, "A", 30, who=1), ok(2, "A", 30, who=2)]
    steps = sprint.schedule(0, ["A", "B"], subs, None, now=60)
    assert len(sprint.in_window(steps, subs)) == 2


# ---------- settings ----------

def test_sprint_settings_rules():
    base = {"count": 3, "rating_min": 1400, "rating_max": 1800}
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.clean({**base, "sprint": True}, "icpc", "friend")
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.clean({**base, "sprint": True, "sprint_limit_min": 7}, "lockout", "friend")
    with pytest.raises(settings_mod.SettingsError):
        settings_mod.clean({**base, "sprint": True, "handicap": {"type": "extra_time", "user_id": 1, "minutes": 5}},
                           "lockout", "friend")


# ---------- a whole match ----------

@pytest.fixture
def sworld(world):  # noqa: F811
    lifecycle._sprint_seen.clear()
    lifecycle._clinch_busy.clear()
    return world


async def test_sprint_duel(sworld):
    w = sworld
    match = await start_duel(w, rules="lockout", settings={"sprint": True, "sprint_limit_min": 10})
    probs = sorted(store.problems(match["id"]), key=lambda p: p["label"])
    start = match["start_time"]
    w.now.t = start + 5

    st = view.build(store.match_by_id(match["id"]), w.a["id"])
    assert [p["label"] for p in st["problems"]] == ["A"]          # only the open one
    assert st["sprint"]["current"] == "A" and st["sprint"]["deadline"] == start + 600
    assert st["settings"]["sprint"] is True
    assert all(len(r["cells"]) == 1 for r in st["standings"])

    # bob solves C early (before it opened): no points. alice claims A.
    w.subs["Bob_77"].append(raw(1, probs[2]["problem_key"], "OK", start + 30))
    w.subs["alice"].append(raw(2, probs[0]["problem_key"], "OK", start + 60))
    w.now.t = start + 70
    await poller.check(w.a["id"])
    await poller.check(w.b["id"])
    await settle()
    m = store.match_by_id(match["id"])
    st = view.build(m, w.b["id"])
    assert [p["label"] for p in st["problems"]] == ["A", "B"]
    assert st["sprint"]["current"] == "B" and st["sprint"]["opened_at"] == start + 60
    rows = {r["handle"]: r for r in st["standings"]}
    assert rows["alice"]["points"] == 100 and rows["Bob_77"]["points"] == 0

    # nobody solves B in 10 minutes: skipped, C opens; bob's early C still doesn't count
    w.now.t = start + 60 + 600
    lifecycle._tick_running(m, w.now.t)
    st = view.build(store.match_by_id(match["id"]), w.b["id"])
    assert st["sprint"]["current"] == "C"
    assert [s["outcome"] for s in st["sprint"]["steps"]] == ["claimed", "skipped", "open"]

    # bob claims C (300): the last problem is closed, so the match is decided and ends early
    w.subs["Bob_77"].append(raw(3, probs[2]["problem_key"], "OK", start + 700))
    w.now.t = start + 710
    await poller.check(w.b["id"])
    await settle()
    m = store.match_by_id(match["id"])
    assert m["status"] == "ended" and m["end_reason"] == "clinched"
    st = view.build(m, w.a["id"])
    assert len(st["problems"]) == 3                               # all shown after the end
    rows = {r["handle"]: r for r in st["standings"]}
    assert rows["Bob_77"]["points"] == 300 and rows["alice"]["points"] == 100
    assert rows["Bob_77"]["place"] == 1


async def test_sprint_without_time_limit(sworld):
    w = sworld
    match = await start_duel(w, rules="lockout", settings={"sprint": True, "sprint_limit_min": 0})
    assert store.is_sprint(match) and store.sprint_limit_s(match) is None
    w.now.t = match["start_time"] + 3000                          # A stays open however long it takes
    st = view.build(store.match_by_id(match["id"]), w.a["id"])
    assert st["sprint"]["current"] == "A" and st["sprint"]["deadline"] is None
    assert len(st["problems"]) == 1
