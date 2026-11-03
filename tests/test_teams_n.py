"""Team matches beyond 2v2: 3+ teams in the lobby and end to end, names, invite links, captains' draft."""

import asyncio

import pytest

from app import db
from app.auth import users
from app.matches import draft, lifecycle, service, store, view
from app.poller.poller import poller

# ---------- service ----------

T0 = 1_750_000_000
HANDLES = [f"p{i}" for i in range(12)]


@pytest.fixture
def world(cache_filled, stub_cf, frozen_now):
    poller.watches.clear()
    lifecycle._sent.clear()
    lifecycle.setup()
    frozen_now.t = T0
    ratings = {h: 1200 + 100 * i for i, h in enumerate(HANDLES)}
    for h in HANDLES:
        users.create(h, ratings[h], ratings[h])
    db.execute("UPDATE users SET history_synced = 1")
    subs = {h: [] for h in HANDLES}
    stub_cf.on("user.info", lambda p: [{"handle": h, "rating": ratings.get(h, 1500), "maxRating": 2500}
                                       for h in p["handles"].split(";")])
    stub_cf.on("user.status", lambda p: list(reversed(subs[p["handle"]])))

    class W:
        pass
    w = W()
    w.u = {h: users.by_handle(h) for h in HANDLES}
    w.subs, w.cf, w.now = subs, stub_cf, frozen_now
    return w


def sides(match_id):
    return {p["handle"]: p["team"] for p in store.players(match_id)}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def raw(i, key, verdict, tm):
    return {"id": i, "creationTimeSeconds": tm, "verdict": verdict,
            "problem": {"contestId": int(key[:-1]), "index": key[-1]}}


async def test_three_teams_join_smallest_invite_link_rename(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 2, "team_count": 3, "count": 3,
                                                      "rating_min": 1400, "rating_max": 1800})
    match = store.match_by_code(code)
    assert store.team_names(match) == ["Team A", "Team B", "Team C"]
    service.join(w.u["p1"], code)
    service.join(w.u["p2"], code)
    assert sides(match["id"]) == {"p0": 0, "p1": 1, "p2": 2}      # each join fills the smallest team
    service.join(w.u["p3"], code, 2)                               # per-team invite link: straight to Team C
    assert sides(match["id"])["p3"] == 2
    with pytest.raises(service.MatchError, match="Team C is full"):
        service.join(w.u["p4"], code, 2)
    assert service.rename_team(w.u["p0"], code, 2, "  Night   Owls ") == "Night Owls"
    with pytest.raises(service.MatchError, match="Only the host"):
        service.rename_team(w.u["p1"], code, 0, "Mine")
    with pytest.raises(service.MatchError, match="Two teams"):
        service.rename_team(w.u["p0"], code, 0, "night owls")
    with pytest.raises(service.MatchError, match="24"):
        service.rename_team(w.u["p0"], code, 0, "y" * 30)
    service.rename_team(w.u["p0"], code, 0, "<b>Ants</b>")         # stored as text; the page renders it as text
    st = view.build(store.match_by_code(code), None)
    assert [t["name"] for t in st["teams"]] == ["<b>Ants</b>", "Team B", "Night Owls"]
    assert st["team_count"] == 3 and "1 more" not in (st["start_blocked"] or "")
    # creator moves a player; full teams refuse
    service.join(w.u["p4"], code)
    assert sides(match["id"])["p4"] == 0
    service.move_player(w.u["p0"], code, w.u["p4"]["id"], 1)
    assert sides(match["id"])["p4"] == 1
    with pytest.raises(service.MatchError, match="full"):
        service.move_player(w.u["p0"], code, w.u["p1"]["id"], 2)
    with pytest.raises(service.MatchError, match="creator"):
        service.move_player(w.u["p1"], code, w.u["p4"]["id"], 0)


async def test_three_team_duel_end_to_end_with_names(world):
    w = world
    code = service.create(w.u["p0"], "team", "lockout", {"team_size": 2, "team_count": 3, "count": 3,
                                                         "rating_min": 1400, "rating_max": 1800})
    for h in HANDLES[1:6]:
        service.join(w.u[h], code)
    service.rename_team(w.u["p0"], code, 1, "Owls")
    for h in HANDLES[:6]:
        service.set_ready(w.u[h], code, True)
    await settle()
    match = store.match_by_code(code)
    assert match["status"] == "running"
    k = [p["problem_key"] for p in store.problems(match["id"])]
    start = match["start_time"]
    s = sides(match["id"])
    owls = [h for h, t in s.items() if t == 1]
    first_a = [h for h, t in s.items() if t == 0][0]
    w.subs[owls[0]] += [raw(1, k[2], "OK", start + 60)]
    w.subs[first_a] += [raw(2, k[1], "OK", start + 70)]
    w.subs[owls[1]] += [raw(3, k[0], "OK", start + 80)]
    w.now.t = start + 100
    for h in HANDLES[:6]:
        await poller.check(w.u[h]["id"])
    for _ in range(4):
        await settle()
    m = store.match_by_id(match["id"])
    assert m["end_reason"] == "clinched" and m["result_status"] == "final"   # 400 vs 200 vs 0, nothing open
    payload = view.result_payload(m)
    assert [r["name"] for r in payload["rows"]][0] == "Owls"
    assert [r["place"] for r in payload["rows"]] == [1, 2, 3]
    from app.matches import stats_data
    recent = stats_data.recent_matches(w.u[owls[0]]["id"])[0]
    assert recent["team_name"] == "Owls" and recent["players"] == 3 and "result" not in recent


async def test_balance_three_teams_in_lobby(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 3, "team_count": 3})
    for h in HANDLES[1:9]:
        service.join(w.u[h], code)
    teams = service.arrange_teams(w.u["p0"], code, "balance")
    assert sorted(list(teams.values()).count(k) for k in range(3)) == [3, 3, 3]
    assert teams == service.arrange_teams(w.u["p0"], code, "balance")          # same input, same split


async def test_settings_change_shrinks_teams_and_reseats(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 2, "team_count": 3})
    for h in HANDLES[1:4]:
        service.join(w.u[h], code)
    match = store.match_by_code(code)
    assert sides(match["id"]) == {"p0": 0, "p1": 1, "p2": 2, "p3": 0}
    service.update_settings(w.u["p0"], code, {"team_size": 2, "team_count": 2})
    assert sorted(sides(match["id"]).values()) == [0, 0, 1, 1]                  # p2 moved off the removed team
    assert store.team_names(store.match_by_code(code)) == ["Team A", "Team B"]


async def test_uneven_allowed_starts_without_full_teams(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 3, "team_count": 2, "uneven_ok": True,
                                                      "count": 3, "rating_min": 1400, "rating_max": 1800})
    for h in ("p1", "p2"):
        service.join(w.u[h], code)
    for h in ("p0", "p1", "p2"):
        service.set_ready(w.u[h], code, True)
    await settle()
    assert store.match_by_code(code)["status"] == "running"


# ---------- captains' draft ----------

async def test_captains_draft_turns_timeout_and_leftovers(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 2, "team_count": 3, "fill": "draft"})
    for h in HANDLES[1:6]:
        service.join(w.u[h], code)
    match = store.match_by_code(code)
    assert all(t is None for t in sides(match["id"]).values())     # everyone waits in the pool
    with pytest.raises(service.MatchError, match="captain"):
        draft.start(w.u["p0"], code)
    with pytest.raises(service.MatchError, match="captains"):
        service.switch_team(w.u["p1"], code, 0)
    for team, h in enumerate(("p0", "p1", "p2")):
        draft.set_captain(w.u["p0"], code, team, w.u[h]["id"])
    draft.start(w.u["p0"], code)
    st = view.build(store.match_by_code(code), None)["draft"]
    assert st["active"] and st["team"] == 0 and st["deadline"] == w.now.t + 45
    with pytest.raises(service.MatchError, match="Team A's turn"):
        draft.pick(w.u["p1"], code, w.u["p3"]["id"])
    draft.pick(w.u["p0"], code, w.u["p3"]["id"])                    # A picks p3
    st = view.build(store.match_by_code(code), None)["draft"]
    assert st["team"] == 1
    with pytest.raises(service.MatchError, match="already been picked"):
        draft.pick(w.u["p1"], code, w.u["p3"]["id"])
    # B's captain sleeps: the timer runs out, the highest-rated player left (p5) is picked
    w.now.t += 46
    await lifecycle.tick()
    assert sides(match["id"])["p5"] == 1
    # only Team C has room now: nothing to choose, so the last player goes there and the draft ends
    st = view.build(store.match_by_code(code), None)["draft"]
    assert not st["active"] and st["done"]
    s = sides(match["id"])
    assert s["p4"] == 2 and [list(s.values()).count(k) for k in range(3)] == [2, 2, 2]


async def test_draft_leftovers_go_to_smallest_team(world):
    w = world
    code = service.create(w.u["p0"], "team", "icpc", {"team_size": 3, "team_count": 2, "fill": "draft"})
    for h in HANDLES[1:4]:
        service.join(w.u[h], code)
    draft.set_captain(w.u["p0"], code, 0, w.u["p0"]["id"])
    draft.set_captain(w.u["p0"], code, 1, w.u["p1"]["id"])
    draft.start(w.u["p0"], code)
    draft.pick(w.u["p0"], code, w.u["p2"]["id"])
    draft.pick(w.u["p1"], code, w.u["p3"]["id"])                    # pool empty: the draft ends
    match = store.match_by_code(code)
    assert sorted(sides(match["id"]).values()) == [0, 0, 1, 1]
    assert view.build(match, None)["draft"]["done"]
    # a late joiner after the draft goes to the smallest team
    service.join(w.u["p4"], code)
    assert sides(match["id"])["p4"] in (0, 1)
