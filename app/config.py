"""Every tunable setting lives here. Secrets come from environment variables."""

import os

# ---------- server ----------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DB_PATH = os.environ.get("DB_PATH", "clashforces.db")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
SESSION_MAX_AGE_S = 30 * 24 * 3600
BACKGROUND_TASKS = os.environ.get("BACKGROUND_TASKS", "1") == "1"  # tests turn this off

# ---------- Codeforces API client ----------
CF_API_BASE = "https://codeforces.com/api"
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
CODE_LENGTH = 6
PER_PLAYER_RANGE_BELOW, PER_PLAYER_RANGE_ABOVE = 100, 200  # 1300 -> 1200-1500
ICPC_WRONG_PENALTY = 20

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

# ---------- misc ----------
ONLINE_GRACE_S = 120
DISCORD_PREFIX = "https://discord.com/api/webhooks/"
