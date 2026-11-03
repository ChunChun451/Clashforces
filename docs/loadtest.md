# Load test: 20, 30 and 40 players

**Verdict: 20 and 30 players are fine; 40 works, but players who don't press "I submitted" wait too long.**
This holds both for a mix of small matches and for one hosted contest with everyone in it.
The server itself is never the bottleneck. It stayed under 70 MB of RAM and about 1% CPU, with no
errors and no lost verdicts. What sets the speed is the Codeforces rule of one API call per 2 seconds.
That rule caps us at ~25 checks per minute for everyone together. Pressing **I submitted** jumps the
queue, so tell players to press it.

| Players | Verdict delay with "I submitted"<br>(median / 90th pct) | Delay without any hint<br>(median / 90th pct) | All players, median / p90 | Ready for the event? |
|---|---|---|---|---|
| **20** | 6 s / 11 s | 20 s / 81 s | 8 s / 30 s | **Yes**, comfortably |
| **30** | 8 s / 12 s | 43 s / 2.0 min | 11 s / 71 s | **Yes**, if players use the button |
| **40** | 7 s / 16 s | 80 s / 2.3 min | 17 s / 1.8 min | **Only if most players use the button** |

Delays are measured from the moment fake Codeforces had the final verdict to the moment it showed up on
the player's own scoreboard over the WebSocket. Codeforces' own judging time (2–20 s here) comes on top.
Measured from `creationTimeSeconds`, the median with the button is 18–20 s at every size.

## Setup

`python -m tools.loadtest --players 20 30 40 --parallel --seed 11` (raw results in
[`loadtest-data/`](loadtest-data/)). Everything ran on localhost against `tools/fake_cf.py`, never
against production or the real Codeforces API.

- **Fresh stack per size:** a fresh app process (`uvicorn app.main:app --workers 1`, unchanged `app/`)
  with its own SQLite database and its own fake Codeforces. All sizes ran at once, as separate stacks
  on a 4-core machine. The app's CPU never went above 6% of one core, so they didn't slow each other.
- **Real clock, real rate limiter, 15-minute matches** (the app's minimum).
- **Fake Codeforces answer time:** 300–700 ms per call, like the real API. Round 1 used instant
  answers; it gave 30 calls/min and slightly better numbers ([data](loadtest-data/round1-no-latency.json)).
- **Login:** players log in through the real verification flow, each from its own IP.
- **Match mix:** repeats of a 6-player room (ICPC), a 2v2 team duel (Lockout), a 1v1 (ICPC) and a
  3v3 team duel (ICPC).
  - 20 players: 5 matches.
  - 30 players: 7 matches.
  - 40 players: 9 matches.
- **Submissions:** about one per player every 3–6 minutes.
  - Each starts in TESTING and gets its verdict 2–20 s later; 5% take 40–90 s.
  - Verdict mix: 35% OK, 50% WRONG_ANSWER, 10% TLE, 5% CE.
  - Nobody resubmits a problem they (or, in team ICPC, their team) already solved.
- **Player behaviour:**
  - 40% press **I submitted** 2–8 s after submitting.
  - 30% only come back to the Clashforces tab 5–30 s later (the "focus" hint, no extra API call).
  - 30% do neither.
- **WebSockets:** one per player plus one spectator per match: 25, 37 and 49 sockets.

## Results (baseline: current `app/config.py`)

| | 20 players | 30 players | 40 players |
|---|---|---|---|
| Submissions measured | 54 | 73 | 94 |
| Verdicts missing on the scoreboard | 0 | 0 | 0 |
| **"I submitted"**: ready → scoreboard, p50 / p90 / max | 6.1 / 11.3 / 12.9 s | 7.9 / 11.6 / 15.6 s | 6.9 / 15.5 / 26.2 s |
| **Came back to the tab**: p50 / p90 / max | 11.1 / 27.3 / 36.1 s | 10.4 / 30.4 / 70.5 s | 17.5 / 50.3 / 80.4 s |
| **No hint**: p50 / p90 / max | 19.5 / 80.9 / 92.3 s | 43.1 / 120.5 / 145.7 s | 79.5 / 138.7 / 185.5 s |
| Everyone, from `creationTimeSeconds`: p50 / p90 / max | 22.6 / 61.6 / 101.5 s | 27.5 / 91.6 / 153.3 s | 37.7 / 126.7 / 201.9 s |
| Codeforces calls: max in any 60 s | 25 | 25 | 25 |
| Smallest gap between two calls (start to start) | 2.31 s | 2.31 s | 2.30 s |
| Calls in flight at the same time | never | never | never |
| App CPU (one core): p50 / max | 0.5% / 3.0% | 0.5% / 4.0% | 0.5% / 5.0% |
| App memory (RSS), max | 65 MB | 67 MB | 68 MB |
| WebSocket messages received | 741 | 964 | 1221 |
| Fan-out spread (first to last socket of one match): p50 / max | 0.5 / 1.4 ms | 0.5 / 3.1 ms | 0.5 / 0.9 ms |
| Match end → final result | 24–42 s | 27–50 s | 20–61 s |
| Everyone logs in at the same moment | 1.7 min | 2.5 min | 3.4 min |
| Everyone presses Ready at once → all matches running | 1.4 min | 2.0 min | 2.7 min |

### What the numbers mean

- **The Codeforces rule is never broken.** The client waits 2 s after each *answer*, so calls start
  2.3 s apart with a 300–700 ms API. That caps the app at 25 calls per minute.
- **The poller is busy from 20 players up.** Every watched player is due again after 45 s in the
  first 20 minutes of a match. With 20+ players the 25 calls/min never catch up, so the minimum
  intervals in `MIN_INTERVAL_S` never take effect. Each player simply waits their turn.
  - Without a hint, a new submission waits for that turn: median ~0.4 min at 30 players, ~1.3 min at 40.
  - Once a TESTING submission is seen, re-checks every 10 s bring the verdict in quickly.
- **"I submitted" works at every size.** It queues a check 8 s later on the INTERACTIVE lane, which
  goes ahead of background polling. The 90th percentile stays near 12–16 s.
- **Coming back to the tab helps too.** It marks the player HOT (polled twice as often) at no extra
  API cost, but it doesn't jump the queue.
- **Memory and CPU are not a concern for a 1 GB VM.** The app peaked at 68 MB; Caddy and Docker
  add roughly 100–150 MB more. CPU was about 0.5% of one core on this machine. That's comfortably
  under the 10% baseline of an Azure B1s, even if its core is 2–3× slower.
  - The one heavy thing would be a `docker build` during the event; pause auto-deploy (runbook).
- **WebSocket fan-out is not a concern.** Every update reached all sockets of a match within 5 ms.
  The scoreboard is rebuilt once per viewer per update, which is cheap at these sizes.
- **Login and match start go through Codeforces too.** Each login needs `user.info` plus
  `user.status` checks, and each start needs `user.info` and a history sync per player. With 40 people
  doing it at the same minute, that's 3–4 minutes. Have players log in before the event starts.
- **Limits of this test:**
  - The matches were 15 minutes. In longer matches, players idle for 20 minutes drop to COLD
    (checked half as often), which leaves more checks for active players. Real 60–120 minute
    matches should do a bit better after the first 20 minutes.
  - Submission timing is uniform here. Real players resubmit faster right after a WA, which the
    HOT tier is designed for.
  - Samples are small (54–97 submissions per run), so treat differences under ~5 s as noise.

## Hosted contest: everyone in one match

`main` gained hosted contests while this was being written, and the event may run as one big
contest. So the test was repeated with `--layout contest`:

- one host (who doesn't play) creates a solo ICPC contest with 5 problems and a 5-minute freeze
- everyone joins, the host presses Start
- same player behaviour and fake-API latency as above ([data](loadtest-data/contest.json))

| | 30 players, 1 contest | 40 players, 1 contest |
|---|---|---|
| Submissions / missing on the scoreboard | 87 / 0 | 116 / 0 |
| "I submitted": p50 / p90 | 6.8 / 12.4 s | 6.6 / 25.8 s |
| Came back to the tab: p50 / p90 | 14.3 / 59.4 s | 24.0 / 72.9 s |
| No hint: p50 / p90 | 42.4 / 109.7 s | 78.4 / 157.8 s |
| Codeforces calls: max in any 60 s / smallest gap | 25 / 2.30 s | 25 / 2.31 s |
| App CPU: p50 / p90 / max | 0.5 / 3.0 / 5.0% | 0.5 / 4.0 / 7.0% |
| App memory, max | 69 MB | 71 MB |
| WebSocket messages received | 4195 (31 sockets) | 7141 (41 sockets) |
| Fan-out spread per update (all sockets of the contest): p50 / p90 | 7 / 11 ms | 12 / 18 ms |
| Contest end → final result | 83 s | 110 s |

- **Verdict delays match the mixed layout.** They depend on the number of players, not on how
  they're grouped.
- **The scoreboard is rebuilt per viewer.** During the freeze every viewer sees a different
  scoreboard, so one submission means 31 or 41 scoreboard builds. That's where the extra CPU (p90
  3–4%) and the 12–18 ms fan-out come from. Still small.
- **Results take longer to become final in one big contest.** Every player needs their own final
  check (one or more calls, 2.3 s apart). With 40 players that's ~2 minutes. Tell people the result
  appears a minute or two after the end.
- A 30-player mixed run on the merged `main` (67dcf94) matched the earlier numbers
  ([data](loadtest-data/mix-30-on-main-67dcf94.json)): "I submitted" 6.9 / 11.3 s, no hint
  23.9 / 101.1 s.
- The fan-out maximum isn't reported for these two runs. Frozen viewers receive empty updates,
  and this version of the tool grouped those across different submissions, which made the max
  meaningless. The median and p90 are unaffected; the tool now skips empty updates.

## Tuning proposals (`app/config.py`, not applied)

The test can't buy more Codeforces capacity, so no setting makes the no-hint path fast at 40
players. These are the changes worth making:

1. **`HARD_HINT_DELAY_S = 8 → 5`.** Measured with the same seed and workload
   ([data](loadtest-data/hard-hint-delay-5.json)): "I submitted" median 7.9 → 5.5 s at 30 players and
   6.9 → 5.0 s at 40, p90 unchanged (~12 s), no measurable change for the other groups.
   - If Codeforces is still judging after 5 s, the existing 10 s TESTING re-check picks it up.
   - The cost is at most one extra call per press (the cooldown stays 20 s).
2. **Keep `CF_MIN_INTERVAL_S = 2.0` measured from the answer, as now.** Measuring it from the
   request start would give ~30 calls/min instead of 25. It would also risk two calls arriving at
   Codeforces less than 2 s apart when network delay varies. Not worth it for a 20% gain.
3. **Leave `MIN_INTERVAL_S`, `POLL_COUNT` and `CF_POLL_MIN_SHARE` alone.** With 20+ players the
   minimum intervals never bind. `POLL_COUNT` only changes the page size. The waiting queues were
   usually 1–2 jobs deep (5 at most), so polling was never starved.
4. **`WEIGHT` only moves waiting time between players; it can't remove it.** A higher HOT weight
   helps someone who resubmits within 5 minutes, at the expense of everyone else. Not changed; this
   test's uniform submission timing would be a biased judge of it.

The biggest lever isn't a setting: get everyone to press **I submitted** (or `S`). At 30 players
that alone turns a ~2-minute worst case into ~15 seconds.

## Re-running

```bash
python -m tools.loadtest --players 20 30 40 --parallel
```

```bash
python -m tools.loadtest --players 30 --config '{"HARD_HINT_DELAY_S": 5}'
```

```bash
python -m tools.loadtest --layout contest --players 30 40 --parallel
```

Useful options:

- `--duration` (minutes, 15 or more)
- `--sub-min` / `--sub-max` (seconds between a player's submissions)
- `--cf-latency-ms` (fake Codeforces answer time, e.g. `300-700` or `0`)
- `--spectators` (extra sockets per match)
- `--config` (JSON overrides for `app/config.py`, applied by `tools/tuned_app.py`; `app/` itself
  is not changed)
