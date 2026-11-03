"""Validate match settings coming from the API."""

from app import config
from app.core.selection import Filters, filter_errors


class SettingsError(Exception):
    pass


HANDICAPS = ("penalty", "extra_time", "per_player_range")


def clean(raw: dict, rules: str, mode: str) -> dict:
    if rules not in ("icpc", "lockout"):
        raise SettingsError("Rules must be ICPC or Lockout.")
    s = {
        "count": int(raw.get("count", 3)),
        "rating_min": int(raw.get("rating_min", 1200)),
        "rating_max": int(raw.get("rating_max", 1600)),
        "tags_include": sorted({str(t) for t in raw.get("tags_include") or []}),
        "tags_avoid": sorted({str(t) for t in raw.get("tags_avoid") or []}),
        "avoid_interactive": bool(raw.get("avoid_interactive", False)),
        "custom_problems": (str(raw.get("custom_problems") or "").strip() or None),
        "duration_min": int(raw.get("duration_min", 60)),
        "freeze_min": int(raw.get("freeze_min") or 0),
        "handicap": raw.get("handicap") or None,
        "exclude": list(raw.get("exclude") or []),
    }
    if not config.DURATION_MIN <= s["duration_min"] <= config.DURATION_MAX:
        raise SettingsError(f"Duration must be {config.DURATION_MIN}–{config.DURATION_MAX} minutes.")
    if rules != "icpc":
        s["freeze_min"] = 0  # freeze only exists for ICPC rules
    if not 0 <= s["freeze_min"] < s["duration_min"]:
        raise SettingsError("The freeze must be shorter than the match.")
    if s["custom_problems"] is None:
        errors = filter_errors(filters(s))
        if errors:
            raise SettingsError(errors[0])
    s["handicap"] = clean_handicap(s["handicap"], rules, s, mode)
    return s


def clean_handicap(h, rules: str, s: dict, mode: str):
    if not h:
        return None
    if mode == "ranked":
        raise SettingsError("Ranked matches have no handicap.")
    kind = h.get("type")
    if kind not in HANDICAPS:
        raise SettingsError("Unknown handicap.")
    if kind == "penalty":
        if rules != "icpc":
            raise SettingsError("The penalty handicap only works with ICPC rules.")
        minutes = int(h.get("minutes", 0))
        if not 1 <= minutes <= 300:
            raise SettingsError("Penalty bonus must be 1–300 minutes.")
        return {"type": kind, "user_id": _uid(h), "minutes": minutes}
    if kind == "extra_time":
        minutes = int(h.get("minutes", 0))
        if not 1 <= minutes <= 120:
            raise SettingsError("Extra time must be 1–120 minutes.")
        return {"type": kind, "user_id": _uid(h), "minutes": minutes}
    # per_player_range
    if rules != "icpc":
        raise SettingsError("Per-player rating ranges only work with ICPC rules.")
    if s["custom_problems"]:
        raise SettingsError("Per-player rating ranges can't be used with a custom problem list.")
    ranges = {}
    for uid, rng in (h.get("ranges") or {}).items():
        lo, hi = int(rng[0]), int(rng[1])
        if lo % 100 or hi % 100 or not config.RATING_MIN <= lo <= hi <= config.RATING_MAX:
            raise SettingsError("Each player's range must be multiples of 100 within 800–3500.")
        ranges[str(int(uid))] = [lo, hi]
    return {"type": kind, "ranges": ranges}


def _uid(h) -> int | None:
    uid = h.get("user_id")
    return None if uid in (None, "") else int(uid)


def filters(s: dict) -> Filters:
    return Filters(s["count"], s["rating_min"], s["rating_max"], tuple(s["tags_include"]),
                   tuple(s["tags_avoid"]), s["avoid_interactive"])


def default_range(cf_rating: int) -> list[int]:
    """1300 -> 1200-1500, 1900 -> 1800-2100."""
    r = cf_rating if cf_rating > 0 else config.UNRATED_START
    base = int(round(r / 100.0)) * 100
    lo = max(config.RATING_MIN, base - config.PER_PLAYER_RANGE_BELOW)
    hi = min(config.RATING_MAX, base + config.PER_PLAYER_RANGE_ABOVE)
    return [lo, max(lo, hi)]
