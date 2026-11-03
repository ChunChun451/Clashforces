-- ---------- accounts ----------
CREATE TABLE IF NOT EXISTS users (
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

CREATE TABLE IF NOT EXISTS verifications (
  id               TEXT PRIMARY KEY,             -- random token
  handle           TEXT NOT NULL,
  user_id          INTEGER REFERENCES users(id), -- set when changing an existing account's handle
  problem_key      TEXT NOT NULL,                -- e.g. "4A"
  issued_at        INTEGER NOT NULL,
  expires_at       INTEGER NOT NULL,             -- issued_at + 120
  cf_rating        INTEGER NOT NULL DEFAULT 0,   -- from user.info when the task was issued
  cf_max_rating    INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL,                -- pending | verified | expired
  last_checked_at  INTEGER
);

-- ---------- Codeforces cache ----------
CREATE TABLE IF NOT EXISTS contests (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  phase       TEXT NOT NULL,                     -- BEFORE | CODING | ... | FINISHED
  start_time  INTEGER,
  duration    INTEGER
);

CREATE TABLE IF NOT EXISTS problems (
  key         TEXT PRIMARY KEY,                  -- "2043C"
  contest_id  INTEGER NOT NULL,
  idx         TEXT NOT NULL,                     -- "C", "B1"
  name        TEXT NOT NULL,
  rating      INTEGER,                           -- NULL = unrated
  tags        TEXT NOT NULL,                     -- JSON array
  dup_group   TEXT NOT NULL                      -- same value for Div.1/Div.2 copies
);
CREATE INDEX IF NOT EXISTS problems_rating ON problems(rating);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);  -- e.g. problemset_refreshed_at

-- ---------- per-user Codeforces data ----------
CREATE TABLE IF NOT EXISTS touched (                           -- any submission, any verdict
  user_id     INTEGER NOT NULL REFERENCES users(id),
  problem_key TEXT NOT NULL,
  solved      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, problem_key)
);

CREATE TABLE IF NOT EXISTS submissions (                       -- only submissions seen while polling matches
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
CREATE INDEX IF NOT EXISTS submissions_user_time ON submissions(user_id, creation_time);

-- ---------- matches ----------
CREATE TABLE IF NOT EXISTS matches (
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
CREATE INDEX IF NOT EXISTS matches_status ON matches(status);

CREATE TABLE IF NOT EXISTS match_players (
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

CREATE TABLE IF NOT EXISTS match_problems (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  user_id     INTEGER REFERENCES users(id),      -- NULL = shared; set = this player's own set
  label       TEXT NOT NULL,                     -- A, B, C...
  problem_key TEXT NOT NULL,
  points      INTEGER NOT NULL DEFAULT 0         -- lockout
);

-- one active lobby/match/queue entry per user (the insert fails if one exists)
CREATE TABLE IF NOT EXISTS active_locks (
  user_id    INTEGER PRIMARY KEY REFERENCES users(id),
  kind       TEXT NOT NULL,                      -- match | queue
  match_id   INTEGER REFERENCES matches(id),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS queue (
  user_id     INTEGER PRIMARY KEY REFERENCES users(id),
  duel_rating INTEGER NOT NULL,
  joined_at   INTEGER NOT NULL
);

-- ---------- history / social ----------
CREATE TABLE IF NOT EXISTS rating_history (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id),
  match_id   INTEGER NOT NULL REFERENCES matches(id),
  before     INTEGER NOT NULL,
  after      INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS upsolve (
  user_id     INTEGER NOT NULL REFERENCES users(id),
  problem_key TEXT NOT NULL,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  added_at    INTEGER NOT NULL,
  done_at     INTEGER,                           -- set when an OK is seen later
  PRIMARY KEY (user_id, problem_key)
);

CREATE TABLE IF NOT EXISTS friends (                           -- one-directional "follow"
  user_id    INTEGER NOT NULL REFERENCES users(id),
  friend_id  INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, friend_id)
);
CREATE INDEX IF NOT EXISTS match_players_user ON match_players(user_id);
CREATE INDEX IF NOT EXISTS match_problems_match ON match_problems(match_id);
CREATE INDEX IF NOT EXISTS rating_history_user ON rating_history(user_id, created_at);
