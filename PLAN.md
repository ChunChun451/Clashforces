# Clashforces — Plan

A duel platform for Codeforces. Players solve real Codeforces problems against a
clock. Submissions happen on Codeforces. We pick problems, watch submissions
through the official API, and compute results.

This document covers: decisions, module structure, database schema, API
endpoints, WebSocket messages, the poller design (with the load math), and the
build order. Nothing is built until this plan is approved.

---

## 1. Key decisions

| Topic | Decision |
|---|---|
| Runtime | Python 3.12, FastAPI, Uvicorn, **one process, one worker**. The rate limiter, poller and WebSocket hub live in memory, so running more workers would break the 1-request-per-2s limit. |
| Database | SQLite in WAL mode (`journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`). We use stdlib `sqlite3`. Queries are tiny and run on the event loop. The one large write (a user's first full history import) runs in `asyncio.to_thread`. |
| HTTP client | `httpx.AsyncClient`, used only inside `cf/client.py` (and `notify/discord.py` for webhooks). |
| Sessions | Starlette `SessionMiddleware` (cookie signed with itsdangerous). The cookie holds `user_id` and `session_version`. Raising `session_version` logs out every device. The cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, and lasts 30 days. |
| Time | Every time value is a unix second. Match windows are compared only with Codeforces `creationTimeSeconds`. The server clock must be NTP-synced (it is by default on AWS). |
| Frontend | Static HTML pages, one CSS design system, and one small JS file per page. No build step. Pages fetch JSON from `/api/...` and open one WebSocket. All user text is rendered with `textContent` (no innerHTML with user data). |
| Config | `app/config.py` holds every tunable (intervals, weights, K, limits, points, queue widening). Secrets come from env vars (`SECRET_KEY`, `DB_PATH`). |
| Tests | pytest + pytest-asyncio. Codeforces responses come from JSON fixtures in `tests/fixtures/` that have the exact shape of real API responses. The CF client is tested with `httpx.MockTransport` and a fake clock. No test touches the network. |

---

## 2. Module structure

```
Clashforces/
├── PLAN.md  README.md  requirements.txt  Dockerfile  Caddyfile.example
├── app/
│   ├── main.py              # builds the FastAPI app, starts/stops background tasks
│   ├── config.py            # ALL settings in one place
│   ├── db.py                # connection, PRAGMAs, runs schema.sql, small query helpers
│   ├── schema.sql
│   ├── models.py            # plain dataclasses: Problem, Submission, PlayerInput, ...
│   │
│   ├── cf/                  # everything that talks to Codeforces
│   │   ├── client.py        # THE rate-limited client: lanes, 2s spacing, retries, health
│   │   ├── parse.py         # API JSON -> dataclasses
│   │   └── cache.py         # problemset.problems + contest.list refresh, dup groups
│   │
│   ├── core/                # PURE functions. No I/O, no DB, no clock reads.
│   │   ├── icpc.py          # ICPC scoring + ranking (N players, per-player windows)
│   │   ├── lockout.py       # Lockout scoring, ties, early-finish check
│   │   ├── team.py          # Team ICPC (V2, small wrapper over icpc.py)
│   │   ├── selection.py     # problem filtering, dedup, rating spread, "relax X" errors
│   │   ├── custom_list.py   # parse "2043C, 2051E" etc. and validate it
│   │   ├── elo.py
│   │   ├── matchmaking.py   # pair players from the queue snapshot
│   │   ├── schedule.py      # poller priority: state -> weight, choose next player
│   │   ├── freeze.py        # what a given viewer is allowed to see
│   │   └── stats.py         # solve-time/tag aggregates, win rates, head-to-head
│   │
│   ├── auth/
│   │   ├── session.py       # current_user dependency, login/logout helpers
│   │   ├── verify.py        # issue a CE task, check it
│   │   └── handles.py       # user.info checks at duel start, handle change
│   │
│   ├── poller/
│   │   ├── poller.py        # the polling loop: feeds the client's POLL lane
│   │   ├── history.py       # full + incremental user.status sync -> touched set
│   │   └── ingest.py        # store submissions, update touched/upsolve, emit events
│   │
│   ├── matches/
│   │   ├── service.py       # create, join, leave, ready, start, forfeit, rematch
│   │   ├── lifecycle.py     # 1s ticker: start, 5-min warning, freeze, ends, final checks
│   │   ├── finalize.py      # final scoring, Elo, upsolve entries, webhooks; retries if API is down
│   │   ├── view.py          # builds the scoreboard JSON for a viewer (uses core/freeze.py)
│   │   ├── locks.py         # "one active thing per user" rule
│   │   └── queue.py         # ranked queue worker (calls core/matchmaking.py)
│   │
│   ├── ws/
│   │   └── hub.py           # sockets per match / per user, presence, broadcast
│   ├── notify/
│   │   └── discord.py       # post a final result to a webhook
│   └── routes/
│       ├── pages.py         # serves the HTML files
│       ├── auth.py  matches.py  queue.py  users.py  friends.py  problems.py  ws.py
│
├── static/
│   ├── css/
│   │   ├── tokens.css       # colors, spacing, fonts, both themes
│   │   ├── base.css         # reset, typography, focus states, layout
│   │   └── components.css   # buttons, inputs, cards, tables, verdict cells, timer
│   ├── js/
│   │   ├── api.js  ws.js  theme.js  ratings.js  notify.js  time.js
│   │   ├── page-home.js  page-login.js  page-new.js  page-match.js
│   │   ├── page-queue.js  page-profile.js  page-friends.js  page-upsolve.js  page-settings.js
│   │   └── chart.js         # tiny SVG line chart for the rating graph
│   └── *.html               # index, login, new, match, queue, profile, friends, upsolve, settings
│
└── tests/
    ├── fixtures/            # problemset.json, contest_list.json, user_status_*.json, user_info_*.json
    ├── test_cf_client.py    # spacing, retries, FAILED handling, lanes
    ├── test_icpc.py  test_lockout.py  test_team.py
    ├── test_selection.py  test_custom_list.py
    ├── test_schedule.py  test_freeze.py  test_elo.py  test_matchmaking.py  test_stats.py
    └── test_match_flow.py   # create→join→ready→submissions→result, with a fake CF client
```

Rule: `core/` imports nothing from `cf/`, `db`, `poller/` or `matches/`. All
non-trivial logic (scoring, selection, scheduling, freeze visibility, Elo,
matchmaking, stats) lives there and gets unit tests.

---

## 3. Database schema

`app/schema.sql`. `PRAGMA user_version` records the schema version so later
migrations stay simple.

```sql
-- ---------- accounts ----------
CREATE TABLE users (
  id               INTEGER PRIMARY KEY,
  handle           TEXT UNIQUE COLLATE NOCASE,   -- NULL if someone else verified this handle later
  cf_rating        INTEGER NOT NULL DEFAULT 0,   -- refreshed from user.info
  cf_max_rating    INTEGER NOT NULL DEFAULT 0,
  duel_rating      INTEGER NOT NULL,             -- starts at CF rating (UNRATED_START if 0)
  needs_reverify   INTEGER NOT NULL DEFAULT 0,   -- handle vanished (renamed on CF)
  session_version  INTEGER NOT NULL DEFAULT 1,
  discord_webhook  TEXT,
  history_synced   INTEGER NOT NULL DEFAULT 0,   -- full user.status import done
  last_sub_id      INTEGER NOT NULL DEFAULT 0,   -- newest CF submission id we have seen
  created_at       INTEGER NOT NULL,
  last_seen_at     INTEGER
);

CREATE TABLE verifications (
  id               TEXT PRIMARY KEY,             -- random token
  handle           TEXT NOT NULL,
  user_id          INTEGER REFERENCES users(id), -- set when changing an existing account's handle
  problem_key      TEXT NOT NULL,                -- e.g. "4A"
  issued_at        INTEGER NOT NULL,
  expires_at       INTEGER NOT NULL,             -- issued_at + 120
  status           TEXT NOT NULL,                -- pending | verified | expired
  last_checked_at  INTEGER
);

-- ---------- Codeforces cache ----------
CREATE TABLE contests (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  phase       TEXT NOT NULL,                     -- BEFORE | CODING | ... | FINISHED
  start_time  INTEGER,
  duration    INTEGER
);

CREATE TABLE problems (
  key         TEXT PRIMARY KEY,                  -- "2043C"
  contest_id  INTEGER NOT NULL,
  idx         TEXT NOT NULL,                     -- "C", "B1"
  name        TEXT NOT NULL,
  rating      INTEGER,                           -- NULL = unrated
  tags        TEXT NOT NULL,                     -- JSON array
  dup_group   TEXT NOT NULL                      -- same value for Div.1/Div.2 copies
);
CREATE INDEX problems_rating ON problems(rating);

CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);  -- e.g. problemset_refreshed_at

-- ---------- per-user Codeforces data ----------
CREATE TABLE touched (                           -- any submission, any verdict
  user_id     INTEGER NOT NULL REFERENCES users(id),
  problem_key TEXT NOT NULL,
  solved      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, problem_key)
);

CREATE TABLE submissions (                       -- only submissions seen while polling matches
  id            INTEGER PRIMARY KEY,             -- Codeforces submission id
  user_id       INTEGER NOT NULL REFERENCES users(id),
  handle        TEXT NOT NULL,
  problem_key   TEXT NOT NULL,
  verdict       TEXT NOT NULL,                   -- missing verdict stored as TESTING
  passed_tests  INTEGER NOT NULL DEFAULT 0,
  creation_time INTEGER NOT NULL,                -- Codeforces time, the only time we use
  language      TEXT,
  first_seen_at INTEGER NOT NULL                 -- our time, for debugging only
);
CREATE INDEX submissions_user_time ON submissions(user_id, creation_time);

-- ---------- matches ----------
CREATE TABLE matches (
  id            INTEGER PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,            -- 6 chars, share link /m/{code}
  mode          TEXT NOT NULL,                   -- friend | group | ranked   (V2: team | practice | ghost | tournament)
  rules         TEXT NOT NULL,                   -- icpc | lockout
  status        TEXT NOT NULL,                   -- lobby | starting | running | ended | cancelled
  result_status TEXT NOT NULL DEFAULT 'none',    -- none | pending | final
  settings      TEXT NOT NULL,                   -- JSON, see below
  creator_id    INTEGER REFERENCES users(id),
  rematch_of    INTEGER REFERENCES matches(id),
  created_at    INTEGER NOT NULL,
  start_time    INTEGER,                         -- problems revealed, timer starts
  end_time      INTEGER,                         -- base end (start + duration)
  ended_at      INTEGER,
  end_reason    TEXT,                            -- time | clinched | forfeit | cancelled
  result        TEXT                             -- JSON standings snapshot once final
);
CREATE INDEX matches_status ON matches(status);

CREATE TABLE match_players (
  match_id        INTEGER NOT NULL REFERENCES matches(id),
  user_id         INTEGER NOT NULL REFERENCES users(id),
  handle          TEXT NOT NULL,                 -- handle at start (confirmed by user.info)
  cf_rating       INTEGER,                       -- at start
  joined_at       INTEGER NOT NULL,
  ready           INTEGER NOT NULL DEFAULT 0,
  team            INTEGER,                       -- V2
  end_time        INTEGER,                       -- per player (extra-time handicap)
  penalty_bonus   INTEGER NOT NULL DEFAULT 0,    -- minutes, e.g. -40
  rating_min      INTEGER,                       -- per-player range handicap
  rating_max      INTEGER,
  forfeited_at    INTEGER,
  final_checked   INTEGER NOT NULL DEFAULT 0,
  place           INTEGER, solved INTEGER, penalty INTEGER, points REAL,
  rating_before   INTEGER, rating_after INTEGER, -- ranked only
  PRIMARY KEY (match_id, user_id)
);

CREATE TABLE match_problems (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  user_id     INTEGER REFERENCES users(id),      -- NULL = shared; set = this player's own set
  label       TEXT NOT NULL,                     -- A, B, C...
  problem_key TEXT NOT NULL,
  points      INTEGER NOT NULL DEFAULT 0         -- lockout
);

-- one active lobby/match/queue entry per user (the insert fails if one exists)
CREATE TABLE active_locks (
  user_id    INTEGER PRIMARY KEY REFERENCES users(id),
  kind       TEXT NOT NULL,                      -- match | queue
  match_id   INTEGER REFERENCES matches(id),
  created_at INTEGER NOT NULL
);

CREATE TABLE queue (
  user_id     INTEGER PRIMARY KEY REFERENCES users(id),
  duel_rating INTEGER NOT NULL,
  joined_at   INTEGER NOT NULL
);

-- ---------- history / social ----------
CREATE TABLE rating_history (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  match_id   INTEGER NOT NULL REFERENCES matches(id),
  before     INTEGER NOT NULL,
  after      INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE upsolve (
  user_id     INTEGER NOT NULL REFERENCES users(id),
  problem_key TEXT NOT NULL,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  added_at    INTEGER NOT NULL,
  done_at     INTEGER,                           -- set when an OK is seen later
  PRIMARY KEY (user_id, problem_key)
);

CREATE TABLE friends (                           -- one-directional "follow"
  user_id    INTEGER NOT NULL REFERENCES users(id),
  friend_id  INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, friend_id)
);

-- ---------- V2 (created later, listed so the design fits) ----------
-- tournaments(id, size, status, settings, creator_id, created_at)
-- tournament_slots(tournament_id, round, slot, user_id, match_id, winner_id)
-- ghost/practice reuse matches + match_players (mode = 'ghost' / 'practice').
```

`matches.settings` JSON:

```json
{
  "count": 4, "rating_min": 1400, "rating_max": 2000,
  "tags_include": ["dp"], "tags_avoid": ["geometry"], "avoid_interactive": true,
  "custom_problems": null,
  "duration_min": 90,
  "freeze_min": 15,
  "points": [100, 200, 300, 400],
  "handicap": {"type": "penalty", "user_id": 7, "minutes": -40}
           // or {"type": "extra_time", "user_id": 7, "minutes": 15}
           // or {"type": "per_player_range"}   (ranges stored on match_players)
  ,"discord_webhook": null
}
```

Scheduler state (when each player was last checked, their tier, hints) lives
**in memory only**. After a restart it is rebuilt from `matches` and
`match_players`: everyone starts as WARM, and one catch-up check runs per
player. The submissions table is the source of truth, so nothing is lost.

---

## 4. Codeforces API client (`cf/client.py`)

One `CFClient` object owns the only `httpx.AsyncClient` that talks to
`codeforces.com/api`. Every caller goes through `await cf.call(method, params, lane)`.

**Spacing.** A single worker task sends requests one at a time. It keeps the
next request at least `CF_MIN_INTERVAL = 2.0s` after the *previous request
finished*. Measuring from the finish adds a little safety margin.

**Lanes** (strict priority, highest first):

| Lane | What goes in it |
|---|---|
| 1 URGENT | final checks at a player's end time, confirmation checks before a Lockout early finish, `user.info` at duel start |
| 2 INTERACTIVE | verification checks, "I submitted" hint checks, re-checks of TESTING submissions, history top-up when a player joins a lobby |
| 3 POLL | the priority scheduler for players in running matches (section 6) |
| 4 BACKGROUND | `problemset.problems` / `contest.list` refresh, upsolve sweeps |

There is a starvation guard: when POLL has a player due, POLL gets at least 1 of
every 3 slots. BACKGROUND runs only when nothing else is due. The one exception
is a problemset cache older than 26 hours, which gets promoted to lane 2.

**Retries.** These are retried with backoff 2, 4, 8, 16, 32s (capped at 60s):
network errors, timeouts, HTTP 5xx and 429, `status: FAILED` with "Call limit
exceeded", and invalid or unexpected JSON. Retries still respect the 2s
spacing. A FAILED response saying a handle was not found is **not** retried; it
raises `HandleNotFound(handle)`. Default timeout is 15s; a full history import
gets 60s.

**Health.** After 3 failures in a row the client sets `api_down = True` and
broadcasts `api.status`. The first success clears it.

**Problem cache (`cf/cache.py`).** `problemset.problems` refreshes every 24h.
`contest.list` refreshes every hour, because it decides which contests are
"running now". A refresh rebuilds `dup_group` for every problem: problems whose
contests have the **same start time** and share the **same name** get one group
id (the smallest problem key in the group). Problems without a known contest
start time form a group of one.

---

## 5. Accounts, verification, handles

**Sign up / log in** (one flow; there is no password anywhere):
1. `POST /api/auth/start {handle}` → the server calls `user.info` to get the
   canonical spelling and confirm the handle exists. It then picks a random
   problem with rating 800 that is not interactive and not `*special`, and
   stores a verification with `expires_at = now + 120`.
2. The page shows the problem link and a 2:00 countdown: "Submit any code that
   fails to compile."
3. The page calls `POST /api/auth/check` every 5s. The server does at most one
   Codeforces check per task every 6s (lane 2), using
   `user.status?handle=X&from=1&count=10`. It looks for a submission with
   verdict `COMPILATION_ERROR` on that problem and
   `issued_at <= creationTimeSeconds <= expires_at`. Checks continue for 60s
   after expiry so a late judge still counts.
4. On success: create the user, or log in if the handle already belongs to an
   account, then set the session cookie. `duel_rating` starts at `cf_rating`
   (or `UNRATED_START = 1200` if Codeforces shows 0).
5. If the handle is attached to a **different** account (for example, the old
   owner renamed and someone took the name), the old account's handle is set to
   NULL and marked `needs_reverify`. Proof of ownership wins.

A new device means doing the 2-minute check again. Cookies last 30 days.

**Duel start check.** When all players are ready, one batched call
`user.info?handles=a;b;c` (lane 1) runs. It also refreshes `cf_rating`. If
Codeforces answers "User with handle X not found", the start is blocked, X is
marked `needs_reverify`, and the lobby shows "X must re-verify their new
handle". Other players stay in the lobby.

**Handle change.** `POST /api/me/handle/start {new_handle}` uses the same
verification flow with `user_id` set. It is refused while the user holds an
`active_locks` row. On success `users.handle` is updated. Old submissions keep
their `user_id`, so history survives the rename.

**One active thing per user.** Joining or creating a lobby, or joining the
queue, inserts into `active_locks`, and that insert fails if a row already
exists. The row is deleted when the user leaves a lobby, leaves the queue, or
when the match ends or is cancelled. Idle lobbies are cancelled after 2h.

---

## 6. Submission poller (the most important part)

### 6.1 What a check is
A check is `user.status?handle=X&from=1&count=10`. If all 10 results are new to
us (id > last seen) and the oldest is still inside a live window, we fetch the
next page (`from=11&count=50`) so a burst of submissions is never missed. Each
result is upserted into `submissions` by id, so verdict changes, including
rejudges, are picked up. `touched` is updated, `users.last_sub_id` moves
forward, and upsolve items get `done_at` on OK.

### 6.2 Which submissions count
A submission counts for a player when:
`match.start_time <= creationTimeSeconds < player.end_time`, the problem is in
that player's problem set, and the player did not forfeit before it. A missing
verdict or `TESTING` means "not judged yet". The scoring functions treat it as
"no result yet", and the scheduler re-checks soon.

### 6.3 Player tiers and weights (all in config)

Codeforces time is used for all "time since last activity" values.

| Tier | When | Weight | Shortest allowed interval |
|---|---|---|---|
| TESTING | has an in-window submission still in TESTING | goes to lane 2, re-checked every 10s | 10s |
| HOT | in-window submission or soft hint in the last 5 min | 4 | 15s |
| WARM | last activity 5–20 min ago, or first 20 min of the match | 2 | 45s |
| COLD | idle more than 20 min | 1 | 120s |
| DONE | player ended / forfeited / solved every problem (ICPC) | not polled (one final check only) | — |

**Choosing who is next (POLL lane).** Each player's score is
`(now - last_checked) * weight`. The scheduler picks the player with the
highest score whose shortest interval has passed. This is a priority queue
ordered by "how overdue, weighted by tier". When the system is overloaded,
everyone slows down by the same factor. Nobody starves, and HOT players stay 4×
fresher than COLD ones.

**Hints from the match page** (rate-limited per player):
- **Hard hint**: the player presses **"I submitted"** (shortcut `S`). This
  queues a lane-2 check 8s later, which gives the judge time to finish. Allowed
  once every 20s per player.
- **Soft hint**: the player returns to our tab after at least 30s away. This
  only moves them to HOT and costs no API call. Players flip tabs constantly,
  so checking on every return would waste the budget.

**Final checks (lane 1).** At `player.end_time + 10s` we fetch back until the
oldest result is before `start_time`, usually in one call. We repeat every 10s
while any in-window submission is TESTING. `final_checked = 1` only when
nothing in the window is pending.

**Lockout early finish.** Before we declare a winner early, every player in the
match gets a lane-1 confirmation check. This makes sure no earlier, unseen OK
changes who owns a problem (see 7.2).

### 6.4 The math: 50 duels + 3 rooms of 10 = 130 players

**Budget:** 1 call / 2s = **30 calls/min** in total. No design can beat this
number, so the job is to spend it well.

**Assumptions** (realistic for 60–90 min matches):
- each player submits about 4 times per hour, so 130 × 4 / 60 ≈ **8.7 submissions/min**
- half of the submissions come with a pressed "I submitted" button
- at any moment: 40 HOT, 45 WARM, 45 COLD (soft hints keep more players HOT)
- about 50 matches end per hour (about 1 final check per minute per 2 players), and about 1 duel starts per minute

**Calls per minute, by lane:**

| Lane | Work | Calls/min |
|---|---|---|
| 1 URGENT | final checks (~2.2) + `user.info` at start (~1) | ~3 |
| 2 INTERACTIVE | hard hints 4.3 + TESTING re-checks (~0.5 per submission) 4.3 + lobby top-ups ~1 + verification ~0.5 | ~10 |
| 3 POLL | whatever is left | **~17** |

**POLL lane:** total weight W = 40×4 + 45×2 + 45×1 = **295**. One unit of
weight gets 17 / 295 checks per minute, so a weight-1 (COLD) player is checked
every 295 / 17 ≈ 17 min.

| Tier | Checked every |
|---|---|
| HOT | **~4.3 min** |
| WARM | **~8.7 min** |
| COLD | **~17 min** |
| after "I submitted" | **~8–12s** (8s delay + a short lane-2 wait) |
| final check at the end | within ~10–15s of the player's end time |

**The same formula at smaller loads** (the shortest-interval floors kick in):

| Active players | POLL budget | HOT | WARM | COLD |
|---|---|---|---|---|
| 20 (10 duels) | ~27/min | ~25s | ~50s | ~2 min (floor) |
| 50 (25 duels) | ~24/min | ~1.1 min | ~2.3 min | ~4.6 min |
| 130 (target case) | ~17/min | ~4.3 min | ~8.7 min | ~17 min |

**What this means:**
1. **Results are always exact.** Scoring uses `creationTimeSeconds` only, and
   every player gets a full final check, so detection delay never changes who
   wins.
2. **Live feel at 130 players depends on the "I submitted" button.** With the
   button, a verdict appears in about 10s. Without it, a HOT player's verdict
   can take about 4 minutes to show. This is the hard limit of
   one-call-per-player at 30 calls/min, not a tuning problem.
3. Weights, floors and tier windows are all in `config.py`. A steeper
   weighting (for example HOT 8) makes HOT faster and COLD slower.

### 6.5 Codeforces API down during a match
- Timers run on our clock, so the match ends on time regardless.
- At the end the status becomes `ended`, `result_status = pending`, and the page
  shows "Waiting for Codeforces to confirm results".
- `finalize.py` retries the missing final checks every 60s with backoff. Once
  they succeed, it scores using submissions inside each window, sets
  `result_status = final`, applies Elo, and sends notifications.
- A Lockout early finish is never declared while the API is down.

### 6.6 Touched-problem history (`poller/history.py`)
- First time a user joins any lobby: one full `user.status?handle=X` call
  (lane 2, 60s timeout). It fills `touched`, and `history_synced` is set to 1.
- After that: incremental top-ups (`from=1&count=50`, more pages until we reach
  `last_sub_id`) when the user joins a lobby. Every poll also adds to `touched`.
- Upsolve sweep (lane 4, only spare slots): users with open upsolve items who
  are not in a match get checked at most every 6h. Opening the upsolve page
  triggers a lane-2 check, at most once per 5 min.

---

## 7. Scoring (pure, `core/`)

Shared input types (in `models.py`):

```python
Sub(id, player_id, problem_key, verdict, time)          # time = creationTimeSeconds
PlayerInput(id, end_time, penalty_bonus=0, problems=[...], forfeited=False)
```

### 7.1 ICPC (`core/icpc.py`)
`score_icpc(start, players, subs) -> list[Row]`. A Row holds place, solved,
penalty, last_ok_time, and a cell per problem (tries, solved_at_min, pending).

- Keep only submissions in the window `start <= t < player.end_time` for that
  player's own problems. Sort by `(time, id)`.
- Per problem: walk the submissions until the first OK. Count *rejected* ones
  before it. Rejected = any verdict except OK, COMPILATION_ERROR, TESTING.
  Ignore everything after the first OK.
- Penalty for a solved problem = `(ok_time - start) // 60 + 20 * rejected`.
  Unsolved problems add 0. The total also includes `penalty_bonus` (for example −40).
- Ranking: solved desc → penalty asc → last OK time asc → otherwise shared
  place (a draw for 2 players). Standard competition ranking (1, 1, 3).
- A TESTING submission marks the cell `pending`. It is only resolved by the
  final check.
- Example test: start 18:00, WA 18:10, WA 18:15, OK 18:22 → 22 + 40 = **62**.
  Player 2 OK 18:30 with no wrong tries → 30, so player 2 ranks first.

### 7.2 Lockout (`core/lockout.py`)
`score_lockout(start, players, problems_with_points, subs) -> list[Row]`
- For each problem, find the earliest in-window OK time across all players. All
  players with an OK at exactly that second split the points evenly (stored as
  a float). Nobody else scores on it.
- Wrong submissions cost nothing.
- Ranking: points desc → time of last scoring OK asc → draw / shared place.
- `clinched(rows, open_points) -> bool`: the leader is clinched when every
  other player has `points + open_points < leader_points`. Open points = the
  points of problems nobody has solved yet. An OK still in TESTING blocks
  clinching. Example: 5 problems, 1500 total, leader 801, other 0 → open = 699,
  and 0 + 699 < 801, so the match ends.
- Default points: 100, 200, 300… in rating order (config `LOCKOUT_POINT_STEP`).

### 7.3 Team (V2, `core/team.py`)
Merge all members' submissions under the team id, then run ICPC per team.
Rejected tries from every member before the team's first OK count. Example:
member 1 WA at 20 min, member 2 OK at 35 min → 35 + 20 = **55**.

### 7.4 Elo (`core/elo.py`)
`expected = 1 / (1 + 10 ** ((Rb - Ra) / 400))`, `new = Ra + K * (score - expected)`
with score 1 / 0.5 / 0, `K = 32`. A forfeit counts as a loss. Only ranked
matches change duel rating.

---

## 8. Problem selection (pure, `core/selection.py`)

`select(problems, contests_running, touched_groups_per_player, filters, rng) -> Selection | SelectionError`

1. **Base pool:** rated problems, no `*special` tag, contest not running now
   (phase not FINISHED and not BEFORE), not gym.
2. **Filters, in this order. We count after each one:** rating range → tags
   include (at least one) → tags avoid (none) → avoid interactive → not touched
   by any player. "Touched" means the problem's `dup_group` is in any player's
   touched groups, so touching one Div.1/Div.2 copy excludes both.
3. **Dedupe** by `dup_group`. At most one problem per group goes into the pool.
4. **Spread:** targets `t_i = lo + i*(hi-lo)/(k-1)`, rounded to the nearest 100
   (k = 1 → midpoint). For each target, take the available problems whose rating
   is closest to it, pick one at random (seeded `rng` in tests), and remove its
   group. Output is sorted by rating ascending, labeled A, B, C…
   Example: 4 problems in 1400–2000 → targets 1400, 1600, 1800, 2000.
5. **Not enough problems:** return `SelectionError` naming the first filter
   that dropped the pool below k, with numbers. For example: "Only 2 unsolved
   problems match tags [dp, geometry] in 2400–2600. Widen the rating range or
   remove a tag." It never fails silently. `/api/problems/preview` runs this
   dry-run while the creator edits settings, so the creator sees it early.
6. **Per-player range handicap:** run `select` once per player with their own
   range and only their own touched set, and make sure no problem appears in
   two players' sets.

**Custom list (`core/custom_list.py`):** accepts `2043C`, `2043 C`, `2043/C`,
and problem URLs, separated by commas, spaces or newlines. Each problem must
exist in the cache. Unknown ones are listed by name, along with how old the
cache is. Problems from a running contest are rejected. If a player has touched
a problem, the lobby shows a warning but the problem stays. Order = the order
given. Points = 100, 200… by position.

**Tests:** fake problem list fixtures covering: unrated, `*special`, running
contest, the Div.1 B = Div.2 D duplicate (same start time + name), spread
targets, each "relax X" message, tag include/avoid, interactive, custom list
parsing and validation.

---

## 9. Match flow

```
lobby ──all ready──▶ starting ──checks ok──▶ running ──all ended──▶ ended ──final checks──▶ final
  │                    │ (user.info, select problems, 5s countdown)   │ (result pending if API down)
  └─cancel/idle 2h     └─blocked (re-verify) → back to lobby          └─forfeit / lockout clinched
```

- **Create** (friend or group): validate settings, create the lobby with a code,
  and lock the creator. **Join** by link or code: take the lock and top up
  history. A settings change by the creator resets all ready flags. Handicap
  targets are chosen in the lobby once the players are present.
- **Ready → start:** batched `user.info` → select problems → `start_time = now + 5`
  (3-2-1 countdown) → per-player `end_time` (+ extra time) → broadcast.
- **Running:** a 1s lifecycle ticker sends the 5-minute warning, starts the
  freeze, ends each player's window, and schedules final checks.
- **Forfeit:** the player's `end_time` becomes now. In a 2-player match the
  match ends with the other player winning. In a room, the forfeiting player is
  placed last and the room continues.
- **Rematch:** creates a new lobby with the same settings and new problems (the
  previous match's problems are excluded). The person who pressed it joins
  automatically. The others get a `rematch.offer` message.
- **Freeze** (ICPC rules only; last X min of the base duration): for each viewer, other players'
  in-window submissions made after `freeze_start` show only as "? tries" with
  no verdict. Viewers always see their own results. Spectators see everything
  frozen. With extra time, that player stays frozen until their own end. At the
  end, `match.result` carries `reveal_steps` (frozen cells in reverse standings
  order, like ICPC), and the page animates them one at a time.
- **Spectators:** anyone opening `/m/{code}` who is not a player, logged in or
  not. Read-only.
- **After the match:** the same page switches to detail mode. It shows the
  timeline of every in-window submission from every player, links to each
  accepted submission (`/contest/{cid}/submission/{id}`), the editorial link
  (see open question 4), and the result. Every unsolved problem goes into each
  player's upsolve list.

**Ranked queue:** fixed at 3 problems, 60 min, ICPC rules, range = the two
players' average CF rating (rounded to 100) ± 200, kept within 800–3500.
Matchmaking runs every 1s. A player's window is
`100 + 100 * floor(wait / 30s)`, capped at 400. Two players pair if their
rating gap is within the larger of their two windows. The oldest waiting player
is paired with their closest-rated candidate first. The pair goes into a lobby
with a 60s ready timeout. If one player doesn't press ready, the match is
cancelled and the ready player goes back to the queue with their wait time kept.

---

## 10. HTTP API

All endpoints are JSON. Writes need the session cookie, and every POST checks
an `X-Requested-With` header against CSRF.

**Auth / account**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/start` | `{handle}` → `{task_id, problem{key,name,url}, expires_at}` |
| POST | `/api/auth/check` | `{task_id}` → `{status}`; sets the cookie when verified |
| POST | `/api/auth/logout` | add `?everywhere=1` to bump `session_version` |
| GET | `/api/me` | current user, active lock, `needs_reverify` |
| POST | `/api/me/handle/start` | `{new_handle}`, then `/api/auth/check` |
| PATCH | `/api/me/settings` | `{discord_webhook}` (must start with `https://discord.com/api/webhooks/`) |

**Problems**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/tags` | all tags in the cache |
| POST | `/api/problems/preview` | dry-run selection for settings; returns counts or a "relax X" error |
| POST | `/api/problems/validate` | `{text, match_code?}` → parsed list, unknown IDs, touched warnings |

**Matches**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/matches` | `{mode: friend|group, rules, settings}` → `{code}` |
| GET | `/api/matches/{code}` | full state for this viewer (lobby / live / result, freeze-aware) |
| POST | `/api/matches/{code}/join` | |
| POST | `/api/matches/{code}/leave` | lobby only |
| PATCH | `/api/matches/{code}/settings` | creator, lobby only (also handicap targets / per-player ranges) |
| POST | `/api/matches/{code}/ready` | `{ready: bool}` |
| POST | `/api/matches/{code}/submitted` | hard hint ("I submitted") |
| POST | `/api/matches/{code}/forfeit` | |
| POST | `/api/matches/{code}/rematch` | → `{code}` |
| POST | `/api/matches/{code}/invite` | `{handle}` → sends `invite` to that user's sockets |
| GET | `/api/matches/{code}/timeline` | after the end: every in-window submission |

**Queue**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/queue/join` | |
| POST | `/api/queue/leave` | |
| GET | `/api/queue` | `{in_queue, waited_s, window}` |

**Users, stats, social**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/users/{handle}` | profile: ratings, record |
| GET | `/api/users/{handle}/rating-history` | points for the graph |
| GET | `/api/users/{handle}/stats` | W/L overall and per mode, solve time by tag × rating band, win rate per tag |
| GET | `/api/users/{handle}/matches?before=` | match history, paged |
| GET | `/api/me/upsolve` | list (open + done) |
| POST | `/api/me/upsolve/refresh` | lane-2 check, at most once per 5 min |
| GET | `/api/friends` | list with online status |
| POST | `/api/friends` | `{handle}` |
| DELETE | `/api/friends/{handle}` | |
| GET | `/api/friends/leaderboard` | friends + me by duel rating |
| GET | `/api/h2h/{handle}` | me vs them (1v1 matches only): W–L–D and match list |
| GET | `/api/health` | `{cf_api, lanes: queue depths, active_players}` |

**Pages** (static HTML): `/`, `/login`, `/new`, `/m/{code}`, `/queue`,
`/u/{handle}`, `/friends`, `/upsolve`, `/settings`.

---

## 11. WebSocket messages

One socket per tab at `/ws`. It reads the session cookie, and anonymous
connections are allowed for spectators. Every message is
`{"type": "...", "data": {...}}`.

**Client → server**

| type | data | meaning |
|---|---|---|
| `subscribe` | `{code}` | follow a match (as player or spectator) |
| `unsubscribe` | `{code}` | |
| `submitted` | `{code}` | hard hint (same as the HTTP endpoint) |
| `focus` | `{code, away_s}` | returned to the tab → soft hint if `away_s >= 30` |
| `ping` | `{}` | keep-alive every 25s |

**Server → client**

| type | data | when |
|---|---|---|
| `hello` | `{user, server_time}` | on connect (client corrects clock skew for timers) |
| `match.state` | full viewer-specific snapshot | on subscribe and after big transitions |
| `lobby.update` | `{players[{handle, rating, ready}], settings, warnings}` | join / leave / ready / settings |
| `lobby.blocked` | `{handle, reason}` | handle vanished, selection failed (with "relax X" text) |
| `match.countdown` | `{start_time}` | all ready, problems chosen |
| `match.started` | `{start_time, end_times, problems}` | at start_time |
| `standings` | `{rows, changed[{player, label, kind}]}` | any visible change; `changed` drives the green-flip animation |
| `submission` | `{player, label, verdict, time}` | timeline item visible to this viewer |
| `match.freeze` | `{freeze_start}` | freeze begins |
| `match.warning` | `{minutes_left: 5}` | 5 minutes left (per player for extra time) |
| `match.ended` | `{reason, result_status}` | time / clinched / forfeit |
| `match.result` | `{rows, reveal_steps, rating_changes}` | result is final |
| `rematch.offer` | `{code, from}` | someone pressed rematch |
| `invite` | `{from, code, mode}` | friend invite → browser notification |
| `queue.status` | `{waited_s, window}` | every 5s while searching |
| `queue.matched` | `{code}` | pair found → go to the lobby |
| `presence` | `{handle, online}` | a friend comes online or goes offline |
| `api.status` | `{cf_api: ok|down}` | Codeforces health changes |
| `error` | `{message}` | |

Browser notifications (`notify.js`, opt-in in settings) fire from these
messages: `invite`, `standings` with an opponent's new OK, `match.warning`, and
`match.ended`. They only fire when the tab is hidden.

---

## 12. UI design system

- **Tokens** (`tokens.css`), dark by default, light via `[data-theme=light]`,
  choice saved in localStorage:
  `--bg #0f1012`, `--surface #16181b`, `--surface-2 #1c1f23`, `--line #25282d`,
  `--text #d6d9de`, `--muted #868b94`, `--accent #3ddc84` (sharp green),
  `--danger #ff5d5d`. Light theme uses the same roles with light values.
- **One accent (green):** used for the timer, the current user's row (thin left
  bar + faint tint), Accepted cells, and primary buttons. **Red** is only for
  rejected verdicts.
- **Fonts:** Inter for text, JetBrains Mono for timers, problem IDs, handles,
  penalties and scores. Loaded from Google Fonts with system fallbacks.
- **Layout:** lots of space, 1px `--line` dividers, surfaces only one step
  lighter than the background, no gradients, no icon clutter.
- **Match page:** a big monospace timer on top. Problem cards show letter,
  name, rating, and tags behind a "show tags" toggle. The standings grid is
  styled like a contest table, inside its own `overflow-x: auto` box (sticky
  first column) so only the table scrolls sideways on mobile. An "I submitted"
  button is always visible.
- **Animations** (≤ 250ms, `prefers-reduced-motion` respected): cell flip to
  green on Accepted, freeze reveal step by step, a pulsing ring while
  matchmaking. Nothing else moves.
- **Handles** in Codeforces rating colors: gray < 1200, green < 1400,
  cyan < 1600, blue < 1900, violet < 2100, orange < 2400, red ≥ 2400.
- **Keyboard:** visible `:focus-visible` outlines in the accent color, real
  `<button>`s everywhere, `S` = I submitted, `T` = toggle tags.

---

## 13. Deployment files (not deployed)

- **Dockerfile:** `python:3.12-slim`, install requirements, copy the app, run
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
  --proxy-headers`. SQLite file on a mounted volume `/data`.
- **Caddyfile.example:** `duel.example.com { reverse_proxy 127.0.0.1:8000 }`.
  Caddy handles TLS and passes WebSocket upgrades through automatically.

---

## 14. Build order (one commit per step)

| # | Step | Done when |
|---|---|---|
| 1 | Skeleton (config, db, schema, app) + CF client with lanes, spacing, retries, health + problem/contest cache with dup groups | client tests pass with a fake clock and MockTransport; cache fills from fixtures |
| 2 | Verification + login cookie + `user.info` duel-start check + handle change + `active_locks` | tests for the CE window (before/after/expiry), handle-not-found blocking, lock rules; bare login page works |
| 3 | `core/icpc.py`, `core/lockout.py`, `core/team.py` + tests | all required cases: 62-penalty example, ties, CE, out-of-window, after-OK, N players, first solver, same-second split, clinch, team penalty |
| 4 | `core/selection.py`, `core/custom_list.py` + tests | spread, filters, dedup Div.1/Div.2, "relax X" messages, custom list |
| 5 | Poller: `core/schedule.py` + `poller/` + history sync | scheduler tests (weights, floors, fairness under overload, hints); paging on bursts; ingest updates touched/upsolve |
| 6 | Friend duel end to end (service, lifecycle, finalize, pending-when-API-down) | `test_match_flow.py`: create → join → ready → fake submissions → final result, plus an API-outage case |
| 7 | Design system CSS + live match page + WebSocket hub | full duel playable in the browser on desktop and mobile |
| 8 | Lockout in the UI + clinch flow, scoreboard freeze + reveal, spectator view | freeze visibility tests (`core/freeze.py`) |
| 9 | Handicaps: penalty bonus, extra time, per-player ranges | tests for each in scoring and selection |
| 10 | Group rooms (3–10) with the standings table | N-player flow test |
| 11 | Ranked queue + matchmaking + Elo | matchmaking widening tests, Elo tests |
| 12 | Duel detail page (timeline, links) + upsolve list | |
| 13 | Stats page + friends + online status + leaderboard + head-to-head | `core/stats.py` tests |
| 14 | Browser notifications + Discord webhook; README, Dockerfile, Caddyfile.example | README covers local run, tests, how the poller works |

Version 2 (team, practice, ghost race, tournaments) waits for your approval
after Version 1 works.

---

## 15. Decisions (approved)

1. **Latency is acceptable.** Detection delay only changes when a verdict shows
   up. Every submission is placed and scored at its Codeforces
   `creationTimeSeconds`. The timeline, penalty and Lockout ownership never use
   the time our server saw the submission.
2. **Login on a new device = redo the 2-minute compile-error check.**
3. **Scoreboard freeze only in ICPC-rule modes** (friend/group ICPC, and team
   mode in V2). The freeze setting is hidden and ignored for Lockout.
4. **Editorial:** link to the contest page, where the editorial is under
   "Contest materials". No scraping.
5. **Browser notifications** only while a tab of the site is open.
6. **Ranked range** = average CF rating ± 200 → problems at avg−200, avg, avg+200.
7. **Head-to-head** counts 1v1 matches only (friend + ranked).
8. **CF-unrated users** start at duel rating 1200, and 1200 is used for their ranked range.
