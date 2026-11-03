"""Team duels (2v2 / 3v3) end to end: lobby sides, start rules, scoring, freeze, forfeit, history."""

import asyncio
import random

import pytest

from app import db
from app.auth import users
from app.matches import lifecycle, service, stats_data, store, view
from app.poller.poller import poller

T0 = 1_750_000_000
HANDLES = ("alice", "bob", "carol", "dave")
RATINGS = {"alice": 2400, "bob": 1200, "carol": 1900, "dave": 1700}


@pytest.fixture
def world(cache_filled, stub_cf, frozen_now):
    poller.watches.clear()
    poller.interactive_due.clear()
    poller.last_hard_hint.clear()
    lifecycle._sent.clear()
    lifecycle._final_next.clear()
    lifecycle._final_busy.clear()
    lifecycle._clinch_busy.clear()
    lifecycle.setup()
    frozen_now.t = T0
    for h in HANDLES:
        users.create(h, RATINGS[h], RATINGS[h])
    db.execute("UPDATE users SET history_synced = 1")
    subs = {h: [] for h in HANDLES}
    stub_cf.on("user.info", lambda p: [{"handle": h, "rating": RATINGS.get(h, 1500), "maxRating": 2500}
                                       for h in p["handles"].split(";")])
    stub_cf.on("user.status", lambda p: list(reversed(subs[p["handle"]])))

    class W:
        pass
    w = W()
    w.u = {h: users.by_handle(h) for h in HANDLES}
    w.subs, w.cf, w.now = subs, stub_cf, frozen_now
    return w


def raw(i, key, verdict, t):
    return {"id": i, "creationTimeSeconds": t, "verdict": verdict,
            "problem": {"contestId": int(key[:-1]), "index": key[-1]}}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def finish(w, match):
    for _ in range(10):
        await lifecycle.tick()
        await settle()
        if store.match_by_id(match["id"])["result_status"] == "final":
            break
    return store.match_by_id(match["id"])


def sides(match_id):
    return {p["handle"]: p["team"] for p in store.players(match_id)}


def lobby(w, rules="icpc", settings=None):
    s = {"count": 3, "rating_min": 1400, "rating_max": 1800, "duration_min": 60, "team_size": 2}
    s.update(settings or {})
    code = service.create(w.u["alice"], "team", rules, s)
    for h in ("bob", "carol", "dave"):
        service.join(w.u[h], code)
    return code


async def start_2v2(w, rules="icpc", settings=None, teams=None):
    """alice+bob vs carol+dave unless `teams` says otherwise."""
    code = lobby(w, rules, settings)
    match = store.match_by_code(code)
    teams = teams or {"alice": 0, "bob": 0, "carol": 1, "dave": 1}
    for h, t in teams.items():
        db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?",
                   (t, match["id"], w.u[h]["id"]))
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    assert await service.start(match["id"], random.Random(1))
    return store.match_by_code(code)


async def poll_all(w):
    for h in HANDLES:
        await poller.check(w.u[h]["id"])
    await settle()


# ---------- lobby ----------

async def test_lobby_sides_switch_and_full(world):
    w = world
    code = service.create(w.u["alice"], "team", "icpc", {"team_size": 2})
    match = store.match_by_code(code)
    assert sides(match["id"]) == {"alice": 0}
    service.join(w.u["bob"], code)
    service.join(w.u["carol"], code)
    assert sides(match["id"]) == {"alice": 0, "bob": 1, "carol": 0}  # joins fill the smaller side
    service.switch_team(w.u["carol"], code, 1)
    assert sides(match["id"])["carol"] == 1
    with pytest.raises(service.MatchError, match="Team B is full"):
        service.switch_team(w.u["alice"], code, 1)
    service.join(w.u["dave"], code)
    assert sides(match["id"])["dave"] == 0
    extra = users.by_id(users.create("erin", 1500, 1500))
    with pytest.raises(service.MatchError, match="full"):
        service.join(extra, code)
    with pytest.raises(service.MatchError, match="handicap"):
        service.update_settings(w.u["alice"], code, {"team_size": 3, "handicap": {"type": "extra_time", "minutes": 5}})
    service.update_settings(w.u["alice"], code, {"team_size": 3})
    assert service.max_players(store.match_by_code(code)) == 6
    service.join(extra, code)                   # room again at 3v3
    with pytest.raises(service.MatchError, match="already has 3"):
        service.update_settings(w.u["alice"], code, {"team_size": 2})


async def test_start_blocked_until_teams_full_and_even(world):
    w = world
    code = service.create(w.u["alice"], "team", "icpc", {"team_size": 2})
    match = store.match_by_code(code)
    for h in ("bob", "carol"):
        service.join(w.u[h], code)
    for h in ("alice", "bob", "carol"):
        service.set_ready(w.u[h], code, True)
    await settle()
    assert store.match_by_id(match["id"])["status"] == "lobby"
    st = view.build(store.match_by_code(code), w.u["alice"]["id"])
    assert "1 more player" in st["start_blocked"]
    service.join(w.u["dave"], code)
    assert view.build(store.match_by_code(code), None)["start_blocked"] is None
    # Force an uneven split directly and check the explanation.
    db.execute("UPDATE match_players SET team = 0 WHERE match_id = ?", (match["id"],))
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    st = view.build(store.match_by_code(code), None)
    assert "even" in st["start_blocked"]
    assert not await service.start(match["id"], random.Random(1))
    assert store.match_by_id(match["id"])["status"] == "lobby"


async def test_switch_resets_ready(world):
    w = world
    code = service.create(w.u["alice"], "team", "icpc", {"team_size": 2})
    match = store.match_by_code(code)
    service.join(w.u["bob"], code)
    service.join(w.u["carol"], code)     # alice, carol on A; bob on B
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))
    service.switch_team(w.u["carol"], code, 1)
    assert sides(match["id"]) == {"alice": 0, "bob": 1, "carol": 1}
    assert all(not p["ready"] for p in store.players(match["id"]))


async def test_balance_and_shuffle(world):
    w = world
    code = lobby(w)
    match = store.match_by_code(code)
    with pytest.raises(service.MatchError, match="creator"):
        service.arrange_teams(w.u["bob"], code, "balance")
    service.arrange_teams(w.u["alice"], code, "balance")
    s = sides(match["id"])
    assert s["alice"] == s["bob"] and s["carol"] == s["dave"] and s["alice"] != s["carol"]  # 3600 vs 3600
    service.arrange_teams(w.u["alice"], code, "shuffle", random.Random(5))
    assert sorted(sides(match["id"]).values()) == [0, 0, 1, 1]


async def test_team_settings_validation(world):
    w = world
    with pytest.raises(service.MatchError, match="2–6 players"):
        service.create(w.u["alice"], "team", "icpc", {"team_size": 7})
    with pytest.raises(service.MatchError, match="2–6 teams"):
        service.create(w.u["alice"], "team", "icpc", {"team_count": 7})
    with pytest.raises(service.MatchError, match="handicap"):
        service.create(w.u["alice"], "team", "icpc", {"handicap": {"type": "extra_time", "user_id": 1, "minutes": 5}})


# ---------- a full 2v2 ----------

async def test_2v2_icpc_end_to_end(world):
    w = world
    match = await start_2v2(w)
    assert match["status"] == "running"
    probs = store.problems(match["id"])
    k = [p["problem_key"] for p in probs]
    start = match["start_time"]
    # every member is watched, and every member can press "I submitted"
    assert set(poller.watches) == {w.u[h]["id"] for h in HANDLES}
    for h in HANDLES:
        assert service.hint(w.u[h], match["code"]) is True

    # Team A: alice WA at 5, bob OK at 20 -> 20 + 20 = 40; carol (Team B) OK on B at 10.
    w.subs["alice"] += [raw(1, k[0], "WRONG_ANSWER", start + 5 * 60)]
    w.subs["bob"] += [raw(2, k[0], "OK", start + 20 * 60)]
    w.subs["carol"] += [raw(3, k[1], "OK", start + 10 * 60)]
    w.subs["dave"] += [raw(4, k[1], "WRONG_ANSWER", start + 2 * 60), raw(5, k[2], "OK", start + 30 * 60)]
    w.now.t = start + 31 * 60
    await poll_all(w)

    for viewer in (w.u["alice"]["id"], w.u["carol"]["id"], None):  # both teams and a spectator
        st = view.build(store.match_by_id(match["id"]), viewer)
        rows = {r["name"]: r for r in st["standings"]}
        assert rows["Team A"]["solved"] == 1 and rows["Team A"]["penalty"] == 40
        assert rows["Team B"]["solved"] == 2 and rows["Team B"]["penalty"] == 10 + 20 + 30
        assert rows["Team B"]["place"] == 1
        a_cell = rows["Team A"]["cells"][0]
        assert a_cell["state"] == "ok" and a_cell["by_handle"] == "bob"
        members = {m["handle"]: m for m in rows["Team A"]["members"]}
        assert members["bob"]["solved"] == 1 and members["alice"]["cells"][0]["state"] == "wrong"
    st = view.build(store.match_by_id(match["id"]), w.u["alice"]["id"])
    assert st["my_team"] == 0 and [t["name"] for t in st["teams"]] == ["Team A", "Team B"]

    w.now.t = match["end_time"] + 30
    m = await finish(w, match)
    assert m["status"] == "ended" and m["result_status"] == "final"
    places = {p["handle"]: p["place"] for p in store.players(match["id"])}
    assert places == {"carol": 1, "dave": 1, "alice": 2, "bob": 2}
    # unrated: no Elo
    assert all(p["rating_after"] is None for p in store.players(match["id"]))
    # upsolve goes by each member's own solves
    up = {r["problem_key"] for r in db.all("SELECT problem_key FROM upsolve WHERE user_id = ?", (w.u["bob"]["id"],))}
    assert up == {k[1], k[2]}
    # history: a team win is a win for each member, not a shared first place
    assert stats_data.user_stats(w.u["carol"]["id"])["record"]["overall"] == {"win": 1, "loss": 0, "draw": 0}
    assert stats_data.user_stats(w.u["bob"]["id"])["record"]["by_mode"]["team"] == {"win": 0, "loss": 1, "draw": 0}
    recent = stats_data.recent_matches(w.u["alice"]["id"])[0]
    assert recent["result"] == "loss" and [t["handle"] for t in recent["teammates"]] == ["bob"]
    assert {o["handle"] for o in recent["opponents"]} == {"carol", "dave"}
    # head-to-head counts opposing sides only
    assert stats_data.h2h(w.u["carol"]["id"], w.u["alice"]["id"])["win"] == 1
    same_side = stats_data.h2h(w.u["alice"]["id"], w.u["bob"]["id"])
    assert (same_side["win"], same_side["loss"], same_side["draw"]) == (0, 0, 0)
    payload = view.result_payload(m)
    assert [r["name"] for r in payload["rows"]] == ["Team B", "Team A"]
    # rematch keeps the same teams
    new = service.rematch(w.u["carol"], m["code"])
    for h in ("alice", "bob", "dave"):
        service.join(w.u[h], new)
    rm = store.match_by_code(new)
    assert sides(rm["id"]) == {"alice": 0, "bob": 0, "carol": 1, "dave": 1}
    assert store.settings(rm)["team_size"] == 2 and rm["mode"] == "team"


async def test_2v2_lockout_clinch(world):
    w = world
    match = await start_2v2(w, rules="lockout")
    probs = store.problems(match["id"])
    assert [p["points"] for p in probs] == [100, 200, 300]
    start = match["start_time"]
    k = [p["problem_key"] for p in probs]
    w.subs["carol"] += [raw(1, k[0], "OK", start + 60)]                 # Team B: 100
    w.subs["alice"] += [raw(2, k[2], "OK", start + 90)]                 # Team A: 300
    w.now.t = start + 100
    await poll_all(w)
    assert store.match_by_id(match["id"])["status"] == "running"        # B can still reach 300 with B's 200
    w.subs["bob"] += [raw(3, k[1], "OK", start + 120)]                  # Team A: 500, nothing open
    w.now.t = start + 130
    await poll_all(w)
    for _ in range(3):
        await settle()
    m = store.match_by_id(match["id"])
    assert m["status"] == "ended" and m["end_reason"] == "clinched" and m["result_status"] == "final"
    places = {p["handle"]: p["place"] for p in store.players(match["id"])}
    assert places == {"alice": 1, "bob": 1, "carol": 2, "dave": 2}
    st = view.build(m, None)
    a = next(r for r in st["standings"] if r["team"] == 0)
    assert a["points"] == 500 and {c["by_handle"] for c in a["cells"] if c.get("first")} == {"alice", "bob"}


async def test_2v2_lockout_clinch_before_all_taken(world):
    """Team A leads by more than the open points once Team B can't score: decided early."""
    w = world
    match = await start_2v2(w, rules="lockout")
    k = [p["problem_key"] for p in store.problems(match["id"])]
    start = match["start_time"]
    w.subs["alice"] += [raw(1, k[2], "OK", start + 60)]                 # 300
    w.subs["bob"] += [raw(2, k[1], "OK", start + 70)]                   # 500 vs 0, 100 open
    w.now.t = start + 100
    await poll_all(w)
    for _ in range(3):
        await settle()
    m = store.match_by_id(match["id"])
    assert m["end_reason"] == "clinched"


# ---------- forfeit ----------

async def test_partial_forfeit_team_keeps_playing(world):
    w = world
    match = await start_2v2(w)
    k = [p["problem_key"] for p in store.problems(match["id"])]
    start = match["start_time"]
    w.now.t = start + 60
    service.forfeit(w.u["alice"], match["code"])
    assert store.match_by_id(match["id"])["status"] == "running"       # bob plays on
    w.subs["alice"] += [raw(1, k[0], "OK", start + 120)]                # after her forfeit: doesn't count
    w.subs["bob"] += [raw(2, k[1], "OK", start + 300)]
    w.now.t = start + 400
    await poll_all(w)
    st = view.build(store.match_by_id(match["id"]), None)
    a = next(r for r in st["standings"] if r["team"] == 0)
    assert a["solved"] == 1 and not a["forfeited"]
    assert next(m for m in a["members"] if m["handle"] == "alice")["forfeited"]
    service.forfeit(w.u["bob"], match["code"])                          # whole team out: match over
    m = store.match_by_id(match["id"])
    assert m["status"] == "ended" and m["end_reason"] == "forfeit"
    w.now.t += 60
    m = await finish(w, match)
    places = {p["handle"]: p["place"] for p in store.players(match["id"])}
    assert places == {"carol": 1, "dave": 1, "alice": 2, "bob": 2}


# ---------- freeze ----------

async def test_freeze_view_per_team(world):
    w = world
    match = await start_2v2(w, settings={"freeze_min": 10})
    k = [p["problem_key"] for p in store.problems(match["id"])]
    start, end = match["start_time"], match["end_time"]
    w.subs["bob"] += [raw(1, k[0], "OK", end - 300)]           # Team A, inside the freeze
    w.subs["carol"] += [raw(2, k[1], "OK", start + 60)]        # Team B, before the freeze
    w.subs["dave"] += [raw(3, k[2], "WRONG_ANSWER", end - 200)]  # Team B, inside the freeze
    w.now.t = end - 100
    await poll_all(w)
    m = store.match_by_id(match["id"])

    def team_rows(viewer):
        st = view.build(m, viewer)
        assert st["frozen"]
        return {r["team"]: r for r in st["standings"]}

    alice = team_rows(w.u["alice"]["id"])       # sees her teammate's live OK
    assert alice[0]["solved"] == 1 and alice[0]["cells"][0]["by_handle"] == "bob"
    assert alice[1]["solved"] == 1 and alice[1]["cells"][2]["state"] == "frozen"
    carol = team_rows(w.u["carol"]["id"])       # sees Team A frozen, her own team live
    assert carol[0]["solved"] == 0 and carol[0]["cells"][0]["state"] == "frozen"
    assert carol[1]["cells"][2]["state"] == "wrong"
    spect = team_rows(None)
    assert spect[0]["solved"] == 0 and spect[1]["cells"][2]["state"] == "frozen"
    bob_member = next(x for x in carol[0]["members"] if x["handle"] == "bob")
    assert bob_member["cells"][0]["state"] == "frozen"  # members of the other team are frozen too

    w.now.t = end + 30
    m = await finish(w, match)
    payload = view.result_payload(m)
    assert payload["reveal"] and {s["user_id"] for s in payload["reveal"]["steps"]} == {"t0", "t1"}
    assert {r["team"]: r["solved"] for r in payload["rows"]} == {0: 1, 1: 1}
