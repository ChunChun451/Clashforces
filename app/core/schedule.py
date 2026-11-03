"""Who the poller checks next (pure).

Each watched player has a tier from their recent activity:
  HOT  - in-window submission or soft hint in the last HOT_WINDOW_S
  WARM - activity in the last WARM_WINDOW_S, or the match is young
  COLD - idle longer
Score = seconds since last check * tier weight. The highest score whose tier
minimum interval has passed goes next. Under overload every player slows down
by the same factor, and HOT players stay WEIGHT[HOT]/WEIGHT[COLD] times fresher.
"""

from dataclasses import dataclass

from app import config


@dataclass
class Watch:
    user_id: int
    handle: str
    window_start: int           # match start (unix seconds)
    window_end: int             # this player's end time
    added_at: int               # when watching began
    last_checked: int = 0
    last_activity: int | None = None  # CF time of newest in-window submission, or soft hint time
    testing: bool = False       # an in-window submission is still being judged


def tier(w: Watch, now: int) -> str:
    since = None if w.last_activity is None else now - w.last_activity
    if since is not None and since < config.HOT_WINDOW_S:
        return "HOT"
    if (since is not None and since < config.WARM_WINDOW_S) or now - w.added_at < config.WARM_WINDOW_S:
        return "WARM"
    return "COLD"


def score(w: Watch, now: int) -> float:
    return (now - w.last_checked) * config.WEIGHT[tier(w, now)]


def is_due(w: Watch, now: int) -> bool:
    return now - w.last_checked >= config.MIN_INTERVAL_S[tier(w, now)]


def pick(watches, now: int) -> Watch | None:
    due = [w for w in watches if is_due(w, now)]
    if not due:
        return None
    return max(due, key=lambda w: (score(w, now), -w.last_checked, -w.user_id))
