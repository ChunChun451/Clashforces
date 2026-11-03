from app.core.freeze import hidden_counts, reveal_steps, split_visible
from app.core.icpc import score_icpc
from app.models import PlayerInput, Sub

START, END, FS = 0, 3600, 3000


def players():
    return [PlayerInput("a", END, ("A", "B")), PlayerInput("b", END, ("A", "B"))]


SUBS = [
    Sub(1, "a", "A", "OK", 600),
    Sub(2, "b", "A", "WRONG_ANSWER", 3100),
    Sub(3, "b", "A", "OK", 3200),
    Sub(4, "a", "B", "OK", 3300),
]


def test_viewer_sees_own_and_pre_freeze():
    vis, hid = split_visible(SUBS, "b", FS)
    assert {s.id for s in vis} == {1, 2, 3}
    assert {s.id for s in hid} == {4}
    vis, hid = split_visible(SUBS, None, FS)  # spectator
    assert {s.id for s in vis} == {1}
    assert hidden_counts(hid)[("b", "A")] == 2


def test_no_freeze_shows_everything():
    vis, hid = split_visible(SUBS, None, None)
    assert len(vis) == 4 and hid == []


def test_reveal_order_lowest_rank_first():
    score = lambda subs: score_icpc(START, players(), subs)  # noqa: E731
    order = {"a": ("A", "B"), "b": ("A", "B")}
    steps = reveal_steps(score, order, SUBS, FS)
    # b is last in the frozen standings, so b's hidden A is revealed first.
    assert [(s["player"], s["problem"]) for s in steps] == [("b", "A"), ("a", "B")]
    final = {r.player: r for r in steps[-1]["rows"]}
    assert final["a"].solved == 2 and final["a"].place == 1
