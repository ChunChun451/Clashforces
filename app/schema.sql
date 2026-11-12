-- ---------- accounts ----------
CREATE TABLE IF NOT EXISTS users (
  id               INTEGER PRIMARY KEY,
  handle           TEXT UNIQUE COLLATE NOCASE,   -- NULL if someone else verified this handle later
  cf_rating        INTEGER NOT NULL DEFAULT 0,   -- refreshed from user.info
  cf_max_rating    INTEGER NOT NULL DEFAULT 0,
  duel_rating      INTEGER NOT NULL,             -- starts at CF rating (UNRATED_START if 0)
  needs_reverify   INTEGER NOT NULL DEFAULT 0,   -- handle vanished (renamed on CF)
  session_version  INTEGER NOT NULL DEFAULT 1,
  history_synced   INTEGER NOT NULL DEFAULT 0,   -- full user.status import done
  last_sub_id      INTEGER NOT NULL DEFAULT 0,   -- newest CF submission id we have seen
  created_at       INTEGER NOT NULL,
  last_seen_at     INTEGER,
  banned_at        INTEGER                       -- set by an admin; banned accounts can't log in
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
  mode          TEXT NOT NULL,                   -- friend | group | ranked | team | contest
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
  result        TEXT,                            -- JSON standings snapshot once final
  scheduled_at  INTEGER,                         -- contest: planned start (auto-start time)
  visibility    TEXT NOT NULL DEFAULT 'private'  -- contest: public (listed under Events) | unlisted
);
CREATE INDEX IF NOT EXISTS matches_status ON matches(status);

CREATE TABLE IF NOT EXISTS match_players (
  match_id        INTEGER NOT NULL REFERENCES matches(id),
  user_id         INTEGER NOT NULL REFERENCES users(id),
  handle          TEXT NOT NULL,                 -- handle at start (confirmed by user.info)
  cf_rating       INTEGER,                       -- at start
  joined_at       INTEGER NOT NULL,
  ready           INTEGER NOT NULL DEFAULT 0,
  team            INTEGER,                       -- team index (0 = Team A ... 5 = Team F); NULL = not placed
  end_time        INTEGER,                       -- per player (extra-time handicap)
  penalty_bonus   INTEGER NOT NULL DEFAULT 0,    -- minutes, e.g. -40
  rating_min      INTEGER,                       -- per-player range handicap
  rating_max      INTEGER,
  pick            TEXT,                          -- freestyle: the problem this player sets for the others
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

-- teams of a team match: names and captains (idx matches match_players.team)
CREATE TABLE IF NOT EXISTS match_teams (
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  idx         INTEGER NOT NULL,                  -- 0..5
  name        TEXT NOT NULL,                     -- unique per match (case-insensitive), <= 24 chars
  captain_id  INTEGER REFERENCES users(id),      -- captains' draft
  PRIMARY KEY (match_id, idx)
);

-- hosted contests: messages from the host, shown live to everyone watching
CREATE TABLE IF NOT EXISTS announcements (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  author_id   INTEGER REFERENCES users(id),
  body        TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS announcements_match ON announcements(match_id, id);

-- hosted contests: players the host removed; they can't rejoin until the host allows it
CREATE TABLE IF NOT EXISTS match_kicks (
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  user_id     INTEGER NOT NULL REFERENCES users(id),
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (match_id, user_id)
);

-- team chat: one private channel per team of a team match (idx matches match_players.team)
CREATE TABLE IF NOT EXISTS team_messages (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  team_idx    INTEGER NOT NULL,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  body        TEXT NOT NULL,                     -- plain text, <= 500 chars
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS team_messages_match ON team_messages(match_id, team_idx, id);

-- saved invites to a lobby, so they reach players who were offline (open while the lobby is)
CREATE TABLE IF NOT EXISTS challenges (
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  from_id     INTEGER NOT NULL REFERENCES users(id),
  to_id       INTEGER NOT NULL REFERENCES users(id),
  created_at  INTEGER NOT NULL,
  declined_at INTEGER,
  UNIQUE (match_id, to_id)
);
CREATE INDEX IF NOT EXISTS challenges_to ON challenges(to_id, created_at);
CREATE INDEX IF NOT EXISTS challenges_from ON challenges(from_id, created_at);

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
