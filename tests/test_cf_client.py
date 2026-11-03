import asyncio

import httpx
import pytest

from app import config
from app.cf.client import CFClient, CFUnavailable, HandleNotFound, Lane
from tests.conftest import FakeCF, FakeClock, load


def make(fake: FakeCF, clock: FakeClock) -> CFClient:
    return CFClient(http=fake.http(), clock=clock, sleep=clock.sleep)


async def drain(client: CFClient, *tasks, limit: int = 100):
    """Run the worker step by step until all tasks finish."""
    for _ in range(limit):
        await asyncio.sleep(0)
        if all(t.done() for t in tasks):
            break
        await client.process_one()
    return [t.result() for t in tasks]


async def test_spacing_two_seconds_between_requests():
    clock, fake = FakeClock(), FakeCF()
    fake.add("user.info", load("user_info_ok.json"))
    client = make(fake, clock)
    tasks = [asyncio.create_task(client.call("user.info", {"handles": "alice"})) for _ in range(3)]
    await asyncio.sleep(0)
    sent_at = []
    for _ in range(3):
        await client.process_one()
        sent_at.append(clock())
    await drain(client, *tasks)
    assert len(fake.calls) == 3
    assert sent_at[1] - sent_at[0] >= config.CF_MIN_INTERVAL_S
    assert sent_at[2] - sent_at[1] >= config.CF_MIN_INTERVAL_S


async def test_lanes_priority_order():
    clock, fake = FakeClock(), FakeCF()
    fake.add("m", {"status": "OK", "result": 1})
    client = make(fake, clock)
    order = []

    async def call(lane, name):
        await client.call("m", {"tag": name}, lane=lane)
        order.append(name)

    tasks = [
        asyncio.create_task(call(Lane.BACKGROUND, "bg")),
        asyncio.create_task(call(Lane.INTERACTIVE, "inter")),
        asyncio.create_task(call(Lane.URGENT, "urgent")),
    ]
    await drain(client, *tasks)
    assert [p["tag"] for _, p in fake.calls] == ["urgent", "inter", "bg"]


async def test_poll_lane_not_starved():
    clock, fake = FakeClock(), FakeCF()
    fake.add("m", {"status": "OK", "result": 1})
    client = make(fake, clock)
    tasks = [asyncio.create_task(client.call("m", {"tag": f"i{i}"}, lane=Lane.INTERACTIVE)) for i in range(6)]
    tasks.append(asyncio.create_task(client.call("m", {"tag": "poll"}, lane=Lane.POLL)))
    await drain(client, *tasks)
    tags = [p["tag"] for _, p in fake.calls]
    assert tags.index("poll") == config.CF_POLL_MIN_SHARE - 1


async def test_retries_failed_then_succeeds_with_backoff():
    clock, fake = FakeClock(), FakeCF()
    fake.add("user.info", load("failed_limit.json"), httpx.Response(502, text="bad gateway"),
             load("user_info_ok.json"))
    client = make(fake, clock)
    task = asyncio.create_task(client.call("user.info", {"handles": "alice"}))
    (result,) = await drain(client, task)
    assert result[0]["handle"] == "alice"
    assert len(fake.calls) == 3
    assert clock.now - 1000.0 >= 2 + 4  # backoff delays were respected


async def test_handle_not_found_not_retried():
    clock, fake = FakeClock(), FakeCF()
    fake.add("user.info", load("user_info_not_found.json"))
    client = make(fake, clock)
    task = asyncio.create_task(client.call("user.info", {"handles": "ghost_user"}))
    for _ in range(5):
        await asyncio.sleep(0)
        if task.done():
            break
        await client.process_one()
    with pytest.raises(HandleNotFound) as e:
        task.result()
    assert e.value.handle == "ghost_user"
    assert len(fake.calls) == 1


async def test_gives_up_and_marks_down_then_recovers():
    clock, fake = FakeClock(), FakeCF()
    fake.add("m", httpx.ConnectError("boom"))
    client = make(fake, clock)
    changes = []
    client.on_health_change.append(changes.append)
    task = asyncio.create_task(client.call("m"))
    for _ in range(50):
        await asyncio.sleep(0)
        if task.done():
            break
        await client.process_one()
    with pytest.raises(CFUnavailable):
        task.result()
    assert len(fake.calls) == config.CF_MAX_ATTEMPTS
    assert client.down and changes == [True]

    fake.routes["m"] = [{"status": "OK", "result": 5}]
    task = asyncio.create_task(client.call("m"))
    (result,) = await drain(client, task)
    assert result == 5 and not client.down and changes == [True, False]


async def test_prepare_returning_none_skips_without_request():
    clock, fake = FakeClock(), FakeCF()
    client = make(fake, clock)
    task = asyncio.create_task(client.call("user.status", lane=Lane.POLL, prepare=lambda: None))
    (result,) = await drain(client, task)
    assert result is None and fake.calls == []
