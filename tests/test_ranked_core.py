from app.core.elo import expected, update
from app.core.matchmaking import Entry, pair, ranked_range, window


def test_elo_basics():
    assert expected(1500, 1500) == 0.5
    assert update(1500, 1500, 1) == (1516, 1484)
    assert update(1500, 1500, 0.5) == (1500, 1500)
    a, b = update(1400, 1800, 1)
    assert a - 1400 > 16 and a + b == 3200  # upset gains more; zero-sum


def test_window_widens():
    assert [window(s) for s in (0, 29, 30, 60, 90, 120, 1000)] == [100, 100, 200, 300, 400, 400, 400]


def test_pairs_within_window_only():
    now = 1000
    es = [Entry(1, 1500, now), Entry(2, 1650, now)]
    assert pair(es, now) == []                     # 150 > 100
    assert len(pair(es, now + 30)) == 1           # windows widened to 200


def test_closest_and_oldest_first():
    now = 1000
    es = [Entry(1, 1500, now - 100), Entry(2, 1580, now), Entry(3, 1520, now), Entry(4, 2600, now)]
    p = pair(es, now)
    assert [(a.user_id, b.user_id) for a, b in p] == [(1, 3)]


def test_ranked_range():
    assert ranked_range(1300, 1900) == (1400, 1800)
    assert ranked_range(0, 0) == (1000, 1400)
    assert ranked_range(3500, 3400) == (3300, 3500)
