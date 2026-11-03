# Clashforces

Duel your friends on real Codeforces problems. Players submit on Codeforces as
usual. Clashforces picks the problems, follows submissions through the official
Codeforces API, and computes the results.

- **Friend duels** (1v1), **group rooms** (3–10 players) and a **ranked queue** with Elo
- **ICPC** or **Lockout** rules, scoreboard freeze (ICPC), handicaps
- Live scoreboard over WebSockets, spectator links, timelines, upsolve list, stats, friends

The design is in [PLAN.md](PLAN.md).

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
| `SECRET_KEY` | dev value | Signs session cookies. **Set it in production.** |
| `DB_PATH` | `clashforces.db` | SQLite file (WAL mode) |
| `COOKIE_SECURE` | `0` | Set `1` behind HTTPS |
| `PUBLIC_URL` | `http://localhost:8000` | Used in Discord messages |
| `CF_API_BASE` | `https://codeforces.com/api` | Point at the fake API for offline work |

## Tests

```bash
python -m pytest
```

Tests never call the real Codeforces API. They use JSON fixtures in
`tests/fixtures/` (shaped exactly like real API responses), a fake HTTP
transport for the client, and a stub client for services. They cover:

- **Scoring:** ICPC, Lockout, Team.
- **Problem selection:** including the Div.1/Div.2 duplicate case.
- **Poller:** scheduling, burst paging, final checks.
- **Match flows:** friend, group and ranked, including an API outage.
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
docker run -d --name clashforces -p 127.0.0.1:8000:8000 -v clashforces-data:/data -e SECRET_KEY=change-me -e PUBLIC_URL=https://duel.example.com clashforces
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
  matches/        match service, lifecycle ticker, finalisation, ranked queue, views
  routes/         HTTP and WebSocket endpoints (all async, so SQLite stays on one thread)
  notify/         Discord webhooks
static/           HTML, CSS (tokens/base/components/match) and vanilla JS
tests/            pytest suite and recorded-shape fixtures
tools/fake_cf.py  fake Codeforces API for local development
```
