"""Every tunable setting lives here. Secrets come from environment variables."""

import os

# ---------- server ----------
DEFAULT_SECRET_KEY = "dev-secret-change-me"
SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
DB_PATH = os.environ.get("DB_PATH", "clashforces.db")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
SESSION_MAX_AGE_S = 30 * 24 * 3600
# Comma-separated origins allowed to open /ws, e.g. "https://duel.example.com".
# Unset: only the request's own host is accepted.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
BACKGROUND_TASKS = os.environ.get("BACKGROUND_TASKS", "1") == "1"  # tests turn this off
# Local development only: sign every localhost visitor in as this handle (must already
# have an account in the local database). Refused when COOKIE_SECURE=1.
DEV_AUTO_LOGIN = os.environ.get("DEV_AUTO_LOGIN", "").strip()


def parse_admin_handles(raw: str | None) -> frozenset[str]:
    """Comma-separated Codeforces handles, compared case-insensitively.
    Unset: the site owner. Set but empty: nobody is an admin."""
    if raw is None:
        raw = "GandalfTheGay"
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


# Who can open /admin. Example: ADMIN_HANDLES="GandalfTheGay,tourist"
ADMIN_HANDLES = parse_admin_handles(os.environ.get("ADMIN_HANDLES"))
ADMIN_ACTIVE_WINDOW_S = 5 * 60   # "active now" on the admin page = seen this recently
ADMIN_PAGE_SIZE = 50

# ---------- Codeforces API client ----------
CF_API_BASE = os.environ.get("CF_API_BASE", "https://codeforces.com/api")
CF_MIN_INTERVAL_S = 2.0          # at most one request every 2 seconds
CF_TIMEOUT_S = 15.0
CF_LONG_TIMEOUT_S = 60.0         # full user.status history
CF_BACKOFF_S = [2, 4, 8, 16, 32] # retry delays, then keep using the cap below
CF_BACKOFF_CAP_S = 60
CF_MAX_ATTEMPTS = 6              # per call; after that the call raises CFUnavailable
CF_DOWN_AFTER_FAILURES = 3       # consecutive failures before we call the API "down"
CF_POLL_MIN_SHARE = 3            # POLL lane gets at least 1 of every N slots when due

# ---------- problem cache ----------
PROBLEMSET_REFRESH_S = 24 * 3600
PROBLEMSET_URGENT_AFTER_S = 26 * 3600
CONTEST_LIST_REFRESH_S = 3600

# ---------- verification ----------
VERIFY_WINDOW_S = 120
VERIFY_GRACE_S = 60              # keep checking this long after expiry
VERIFY_CHECK_EVERY_S = 6
VERIFY_PROBLEM_RATING = 800
UNRATED_START = 1200             # duel rating / ranked rating for CF-unrated users

# ---------- poller ----------
POLL_COUNT = 10                  # user.status?from=1&count=10
POLL_PAGE_COUNT = 50             # follow-up pages when a burst is detected
HOT_WINDOW_S = 5 * 60
WARM_WINDOW_S = 20 * 60
WEIGHT = {"HOT": 4, "WARM": 2, "COLD": 1}
MIN_INTERVAL_S = {"HOT": 15, "WARM": 45, "COLD": 120}
TESTING_RECHECK_S = 10
HARD_HINT_DELAY_S = 8
HARD_HINT_COOLDOWN_S = 20
SOFT_HINT_MIN_AWAY_S = 30
FINAL_CHECK_DELAY_S = 10
FINAL_RETRY_S = 10
TESTING_GIVE_UP_S = 3600          # a verdict stuck in TESTING this long after the end is ignored
FINALIZE_RETRY_S = 60
UPSOLVE_SWEEP_EVERY_S = 6 * 3600
UPSOLVE_REFRESH_COOLDOWN_S = 5 * 60

# ---------- matches ----------
DURATION_MIN, DURATION_MAX = 15, 300
RATING_MIN, RATING_MAX = 800, 3500
PROBLEM_COUNT_MIN, PROBLEM_COUNT_MAX = 1, 10
LOCKOUT_POINT_STEP = 100
COUNTDOWN_S = 5
WARNING_MINUTES_LEFT = 5
LOBBY_IDLE_S = 2 * 3600
ROOM_MIN_PLAYERS, ROOM_MAX_PLAYERS = 3, 10
TEAM_SIZE_MIN, TEAM_SIZE_MAX = 2, 6     # players per team
TEAM_COUNT_MIN, TEAM_COUNT_MAX = 2, 6   # teams per match
TEAM_NAME_MAX = 24
DRAFT_PICK_S = 45                 # captains' draft: seconds per pick before an auto-pick
CODE_LENGTH = 6
PER_PLAYER_RANGE_BELOW, PER_PLAYER_RANGE_ABOVE = 100, 200  # 1300 -> 1200-1500
ICPC_WRONG_PENALTY = 20

# ---------- hosted contests ----------
CONTEST_MAX_PLAYERS = 40          # solo contests
CONTEST_PROBLEMS_MAX = 15
CONTEST_TITLE_MAX = 60
CONTEST_SCHEDULE_MAX_DAYS = 60    # how far ahead a start can be scheduled
CONTEST_PREFETCH_S = 5 * 60       # re-sync missing histories this long before the start
CONTEST_RETRY_START_S = 30        # auto-start retry when Codeforces isn't answering
CONTEST_STALE_S = 2 * 3600        # a lobby this long past its start time is cancelled
CONTEST_UNSCHEDULED_IDLE_S = 7 * 24 * 3600
EXTEND_MAX_MIN = 120
ANNOUNCEMENT_MAX = 500
REVEAL_AUTO_S = 1.5               # freeze reveal: seconds per step on auto-play

# ---------- team chat ----------
SINCE_YEAR_MIN = 2010             # "problems from YEAR on": earliest year offered
CHAT_MAX = 500                    # characters per message
CHAT_HISTORY = 200                # messages kept on screen per team
CHAT_MIN_GAP_S = 1                # at most one message per second per user
CHAT_PER_MIN = 20                 # and at most this many per minute

# ---------- ranked ----------
RANKED_COUNT = 3
RANKED_DURATION_MIN = 60
RANKED_RANGE_HALF = 200
ELO_K = 32
QUEUE_START_WINDOW = 100
QUEUE_WIDEN_BY = 100
QUEUE_WIDEN_EVERY_S = 30
QUEUE_MAX_WINDOW = 400
RANKED_READY_TIMEOUT_S = 60


def check_production_settings() -> None:
    """Refuse to run with secure cookies (i.e. in production) and the default secret key,
    or with the local-only DEV_AUTO_LOGIN."""
    if COOKIE_SECURE and DEV_AUTO_LOGIN:
        raise RuntimeError("DEV_AUTO_LOGIN is for local development only; unset it when COOKIE_SECURE=1.")
    if COOKIE_SECURE and SECRET_KEY == DEFAULT_SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is still the default value but COOKIE_SECURE=1. Set SECRET_KEY to a long "
            "random string (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)."
        )
