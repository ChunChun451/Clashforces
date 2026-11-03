# Clashforces

Duel your friends on real Codeforces problems. Players submit on Codeforces as
usual. Clashforces picks the problems, follows submissions through the official
Codeforces API, and computes the results.

- **Friend duels** (1v1), **team duels** (2–6 teams of 2–6), **group rooms** (3–10 players) and a **ranked queue** with Elo
- **Hosted contests** for events: a host who doesn't play, hidden problems, a scheduled start, announcements,
  solo (up to 40) or teams, listed under **Events**
- **ICPC** or **Lockout** rules, scoreboard freeze (ICPC), handicaps (1v1 and rooms)
- For event nights: a **big-screen scoreboard** for a projector, an **ICPC-style freeze reveal** run by the
  host, and a private **team chat**
- Live scoreboard over WebSockets, spectator links, timelines, upsolve list, stats, friends

The design is in [PLAN.md](PLAN.md).

### Team duels

Create one from **New match → Team duel**. Pick 2–6 teams of 2–6 players, ICPC
or Lockout, and the usual problem filters. Anyone with the link can join while
there's room:

- **Joining** puts you on the smallest team. `/m/CODE?team=2` is the invite
  link for the second team (1-based) and joins it directly.
- **Sides:** players switch teams in the lobby; the creator can drag players
  between teams, **Shuffle**, or **Balance by rating** (a snake draft by
  rating, then swaps that shrink the gap; always the same result for the same
  players).
- **Names:** the creator clicks a team name to rename it (24 characters,
  unique in the match). Names show in the lobby, scoreboard, timeline, results
  and profile history. Up to 6 team colours, apart from the green "accepted"
  and red "wrong" colours.
- **Captains' draft** (optional): the creator names one captain per team;
  captains pick in snake order (A B C C B A …). Each pick has 45 seconds, then
  the highest-rated player left is picked automatically. Once only one team
  has room, the rest go to it.

The match starts when every team is full and everyone is ready (or, with
"allow uneven teams", when at least two teams have players).

- **ICPC:** a problem counts for the team when any member gets Accepted. Every
  member's rejected tries before the team's first Accepted add 20 minutes.
- **Lockout:** a problem goes to the team whose member solved it first; the
  match ends early once one team can't be caught.
- **Forfeit:** teammates keep playing when one member forfeits. A team with
  every member out loses.
- **Freeze:** each team is treated like one player: you see your own team live
  and the other teams frozen. Spectators and the host see everyone frozen.
- **With 3+ teams** the scoreboard shows team rows that expand to their
  members (your team starts open), problem headers stay in view in tall
  boards, and only the table scrolls sideways.
- Team duels are unrated. In history and stats a team result counts as a win,
  loss or draw for each member (with 3+ teams: the place). Problems are picked
  so that nobody on any team has tried them. Rematch keeps the same teams and names.

### Hosted contests (Events)

**Events → Host a contest** (`/events/new`). The host runs the contest and
doesn't play: they're never on the scoreboard and never polled.

- **Format:** solo (2–40 players) or teams (2–6 teams of 2–6, with names,
  per-team invite links, moving, balance, or a captains' draft).
- **Problems:** up to 15 Codeforces IDs or links, in order (A, B, C…), with
  optional Lockout points. They stay hidden from everyone but the host until
  the start (the match API, the WebSocket state, `/api/problems/validate`,
  the timeline and the result never show them early). The host sees which
  players already tried each problem, so they can swap it.
- **Rules:** ICPC (with freeze) or Lockout, 15–300 minutes.
- **Schedule:** pick a date and time (shown in each viewer's own time zone),
  or start by hand. Five minutes before the start the server syncs any missing
  Codeforces histories and refreshes handles; at the start it checks every
  handle in one batched `user.info` call, so a 30-player start doesn't stall.
  If teams are uneven or Codeforces is down, it stays in the lobby and retries
  every 30 seconds; the host can **Start now** or **Start anyway**. A player
  whose handle no longer exists sits out instead of blocking everyone.
- **Host controls:** extend time for everyone, post announcements (live toast
  plus a list everyone sees, spectators too), end early, cancel, and in the
  lobby move or remove players (removed players can't rejoin until the host
  allows it).
- **Visibility:** public contests are listed under Events (Upcoming, Live now,
  Past with the winner); unlisted ones only show under **Mine** for the host
  and its players.
- A contest lobby can be open for days, so joining one doesn't block you from
  other matches. The one-match-at-a-time lock is taken at the start (a lobby or
  queue you're in elsewhere is left automatically).

### Event night: big screen, freeze reveal, team chat

- **Big screen** (`/m/CODE/screen`): a full-window scoreboard for a 1080p
  projector, readable from across the room. No login: anyone with the code can
  open it, like the match page. It is always the spectator view, even on a
  player's laptop (`/ws?view=screen`), so it never shows a team's own live
  results during the freeze. It shows the title, a big clock ("Starts in",
  "Time left", a **Frozen** badge), problem columns with tries and a dot on each
  first solve, team rows in their colours (or players in solo contests), the
  latest announcement, and a short "First solve" toast. Problems appear only at
  the start. Up to 6 teams fill the screen; solo contests with more than 20
  players use two columns, and boards that still don't fit page every 12 s.
  `?theme=light` for bright rooms; `F` toggles full screen; the cursor and the
  host buttons hide when the mouse rests. The host gets **Open big screen** on
  the match page, spectators a small **Big screen** link.
- **Freeze reveal** (ICPC contests with a freeze): when the contest is over and
  confirmed, the final results stay frozen until the host runs the reveal from
  the match page or the big screen (**Start reveal**, then **Next**, `Space` or
  `→`; **Auto-play** at 1.5 s a step; **Skip to final**). From the last place up,
  each frozen cell turns green or red and the rows slide to their new places.
  The server drives it, so every screen (big screen, players, spectators) shows
  the same step, and a reconnecting screen picks up where it is. Until it's
  done, the result API, the timeline, the Events winner and profile history
  stay hidden. A contest whose freeze hid nothing (or without a freeze, or
  Lockout) shows its results right away. Other matches keep the old behaviour
  (the reveal plays by itself on the match page).
- **Team chat** (team duels and team contests): a drawer on the match page
  that only your team can read (not the host, spectators or big screens). Plain
  text up to 500 characters, at most one message a second and 20 a minute.
  The last 200 messages load when you open the page or reconnect; an unread dot
  shows while it's closed. Open in the lobby and during the match, read-only
  after it ends. A player moved to another team sees that team's chat from
  then on.

## Run locally

Requires Python 3.12+.

```bash
pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. On first start the server downloads the
Codeforces problem list (two API calls, about 4 seconds).

### Offline, with a fake Codeforces

`tools/fake_cf.py` is a tiny stand-in for the Codeforces API. It serves the
test fixtures and lets you add submissions yourself.

```bash
python -m uvicorn tools.fake_cf:app --port 9000
```

```bash
CF_API_BASE=http://127.0.0.1:9000/api uvicorn app.main:app --port 8000
```

To log in, enter any handle (handles starting with `ghost` "don't exist"). Then
send the compile error the page asks for:

```bash
curl -X POST 'http://127.0.0.1:9000/fake/submit?handle=alice&problem=2000A&verdict=COMPILATION_ERROR'
```

Submit to a match problem the same way, with `verdict=OK`, `WRONG_ANSWER`, or
`TESTING`.

### Settings

Every tunable (poll intervals, weights, Elo K, limits, Lockout points, queue
widening) is in [`app/config.py`](app/config.py). These come from environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | dev value | Signs session cookies. **Set it in production**: the app refuses to start with the default key when `COOKIE_SECURE=1`. |
| `DB_PATH` | `clashforces.db` | SQLite file (WAL mode) |
| `COOKIE_SECURE` | `0` | Set `1` behind HTTPS |
| `ALLOWED_ORIGINS` | unset | Comma-separated origins allowed to open the WebSocket (e.g. `https://duel.example.com`). Unset: only the page's own host. |
| `CF_API_BASE` | `https://codeforces.com/api` | Point at the fake API for offline work |

## Tests

```bash
python -m pytest
```

Tests never call the real Codeforces API. They use JSON fixtures in
`tests/fixtures/` (shaped exactly like real API responses), a fake HTTP
transport for the client, and a stub client for services. They cover:

- **Scoring:** ICPC, Lockout, team ICPC and team Lockout (claims, ties, clinch), team balance and shuffle.
- **N teams** (`tests/test_team_core.py`): ICPC and Lockout with 3–4 teams, clinch with 3+ teams, shared
  places, same-second splits, the snake balance, `can_start`, team-name cleaning.
- **Problem selection:** including the Div.1/Div.2 duplicate case.
- **Poller:** scheduling, burst paging, final checks.
- **Match flows:** friend, group and ranked, including an API outage.
- **Team duels** (`tests/test_team_flow.py`): lobby sides, switching, full
  teams, balance and shuffle, start-blocked rules, a full 2v2 in ICPC and
  Lockout (with clinch), partial forfeit, the freeze view per team, history
  and rematch.
- **Team matches beyond 2v2** (`tests/test_teams_n.py`): smallest-team joins, per-team invite links,
  renaming, moving, a 3-team Lockout match end to end, balance in the lobby, shrinking teams, uneven
  starts, and the captains' draft (turns, timeout auto-pick, leftovers).
- **Hosted contests** (`tests/test_contest.py`): validation, hidden problems (API, WebSocket, validate,
  timeline, result and page source for players, strangers and spectators), host warnings, scheduled
  auto-start through the ticker (one batched `user.info`, no history calls at the start), blocked and
  retried starts, renamed handles, extend time, announcements, kick / allow / move, solo scoring
  without the host, the Events listing and visibility, idle rules, and migrating an old database.
- **Big screen** (`tests/test_bigscreen.py`): the page is static and public, a screen socket is a
  spectator even with a player's cookie, no problems before the start (also during the countdown), live
  updates during the freeze carry no hidden results, solo boards, any match is watchable by code.
- **Freeze reveal** (`tests/test_reveal.py`): the order (last place first, leftmost cell) and that the
  last step is the true final ranking in ICPC and Lockout, the lock on result / timeline / Events winner /
  profile history, host-only controls, one shared board per step, auto-play, pause, skip, reconnecting
  mid-reveal, and no lock without frozen submissions or in Lockout.
- **Team chat** (`tests/test_team_chat.py`): only the team receives and reads it (not the host,
  spectators, big screens or other teams; over the API and the WebSocket), length and rate limits, the
  200-message history, moved players, read-only after the end, and migrating an old database.
- **Other:** freeze and reveal, Elo, matchmaking, stats.

## How the poller works

Codeforces allows **one API call every 2 seconds**, so every call goes through
one client (`app/cf/client.py`). That client sends requests one at a time,
always at least 2 s apart, and retries failures with backoff (2, 4, 8, 16, 32 s).
Requests wait in four priority lanes:

1. **URGENT:** final checks when a player's time ends, the confirmation
   before a Lockout early finish, and `user.info` when a duel starts.
2. **INTERACTIVE:** handle verification, the "I submitted" button,
   re-checks of TESTING verdicts, and history top-ups when someone joins a lobby.
3. **POLL:** the background scheduler for players in running matches.
4. **BACKGROUND:** problem-list refresh and upsolve sweeps.

When POLL has work due, it always gets at least 1 of every 3 slots.

**Scheduler (`app/core/schedule.py`).** Each watched player is HOT (activity in
the last 5 min), WARM (5–20 min, or early in the match) or COLD. When a POLL slot
opens, the player with the highest `seconds since last check × weight` goes
next. The weights are HOT 4, WARM 2, COLD 1, with minimum gaps of 15/45/120 s.
Under heavy load everyone slows down by the same factor and nobody starves.

**Two hints from the match page** make it faster:

- Pressing **I submitted** (or `S`) queues a check about 8 s later.
- Coming back to the tab after 30+ seconds away marks you HOT. This costs no API call.

**Correctness never depends on speed.** Only Codeforces `creationTimeSeconds`
is used, never the time we saw a submission. A submission counts if
`start ≤ time < your end time`. When a player's time ends, a final check pages
back to the match start. The match ends on our clock even if Codeforces is down;
the result then shows as *pending* and is finalised once the API answers. A
verdict still in TESTING keeps the result pending; after an hour it is ignored.

At 50 duels plus a few 10-player rooms (~130 players), expect a verdict within
about 10 s after "I submitted". Without the button, a HOT player is checked
roughly every 4 minutes. The full math is in PLAN.md §6.4.

## Deploying (one server behind Caddy)

```bash
docker build -t clashforces .
```

```bash
docker run -d --name clashforces -p 127.0.0.1:8000:8000 -v clashforces-data:/data -e SECRET_KEY=change-me clashforces
```

Then use `Caddyfile.example` as your Caddyfile. Run **one** worker only: the
rate limiter, the poller and the WebSocket hub live in memory.

## Layout

```
app/
  config.py       all settings
  cf/             Codeforces client (rate limit, lanes, retries) and problem cache
  core/           pure functions: scoring, selection, freeze, schedule, Elo, matchmaking, stats
  auth/           verification, sessions, handle checks
  poller/         submission poller, history sync, upsolve
  matches/        match service, contests, captains' draft, lifecycle ticker, finalisation, ranked queue, views
  routes/         HTTP and WebSocket endpoints (all async, so SQLite stays on one thread)
static/           HTML, CSS (tokens/base/components/home/match/events), vanilla JS, favicon.svg
tests/            pytest suite and recorded-shape fixtures
tools/fake_cf.py  fake Codeforces API for local development
```
