"""Elo rating (pure)."""

from app import config


def expected(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def update(ra: int, rb: int, score_a: float, k: int = config.ELO_K) -> tuple[int, int]:
    """score_a: 1 win, 0.5 draw, 0 loss. Returns the new (ra, rb)."""
    ea = expected(ra, rb)
    delta = k * (score_a - ea)
    return round(ra + delta), round(rb - delta)
