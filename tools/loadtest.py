"""Load test: N fake players in running matches, against tools/fake_cf.py. Never production.

    python -m tools.loadtest --players 20 30 40

For each N this starts a fresh fake Codeforces API and a fresh app (own ports,
own temp database), logs N players in through the real verification flow,
puts them into a mix of matches and lets them submit for the whole match:

- one submission every --sub-min..--sub-max seconds (default 180..360),
  created as TESTING and judged 2-20 s later (5% take 40-90 s);
  verdicts are about 35% OK, 50% WRONG_ANSWER, 10% TLE, 5% CE
- 40% of players press "I submitted" a few seconds after submitting,
  30% only come back to the tab (soft "focus" hint), 30% do neither
- every player and one spectator per match keep a WebSocket open
- --layout mix (default): rooms, 1v1s and team duels as below; --layout contest: one hosted
  solo ICPC contest with everyone in it (5 problems, 5-minute freeze), run by a separate host
- fake CF answers each call in --cf-latency-ms (default 300-700 ms, like the real API)
- in team ICPC, nobody resubmits a problem their team already solved

Measured: delay from creationTimeSeconds (and from the moment the verdict was
ready on fake CF) until the final verdict shows up on the player's own
WebSocket; Codeforces calls per minute and the smallest gap between calls;
CPU and RSS of the app process; WebSocket fan-out spread per match.

Safety: the app under test is started here with CF_API_BASE pointing at the
local fake, on 127.0.0.1. There is no option to aim it anywhere else.
"""

import argparse
import asyncio
import json
import os
import random
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Requested-With": "fetch"}
CLK_TCK = os.sysconf("SC_CLK_TCK")

# Each cycle of this layout fills 18 players: a 6-player room, a 2v2 lockout team duel,
# a 1v1 and a 3v3. Leftovers become one more room (3+) or a 1v1.
LAYOUT = [("group", "icpc", 6), ("team", "lockout", 4), ("friend", "icpc", 2), ("team", "icpc", 6)]
VERDICTS = [("OK", 35), ("WRONG_ANSWER", 50), ("TIME_LIMIT_EXCEEDED", 10), ("COMPILATION_ERROR", 5)]
BEHAVIOURS = [("button", 40), ("focus", 30), ("none", 30)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pick(rng: random.Random, weighted):
    return rng.choices([v for v, _ in weighted], [w for _, w in weighted])[0]


def layout(n: int) -> list[tuple[str, str, int]]:
    out, left, i = [], n, 0
    while left > 0:
        mode, rules, size = LAYOUT[i % len(LAYOUT)]
        if size > left:
            break
        out.append((mode, rules, size))
        left -= size
        i += 1
    if left >= 3:
        out.append(("group", "icpc", left))
    elif left == 2:
        out.append(("friend", "icpc", 2))
    elif left == 1:  # fold into an existing room
        for j, (mode, rules, size) in enumerate(out):
            if mode == "group" and size < 10:
                out[j] = (mode, rules, size + 1)
                break
    return out


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def summary(values) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50": round(pct(values, 50), 1), "p90": round(pct(values, 90), 1),
            "max": round(max(values), 1), "mean": round(statistics.mean(values), 1)}


@dataclass
class Sub:
    handle: str
    behaviour: str
    created: int          # creationTimeSeconds
    created_f: float      # wall clock at submit
    verdict: str
    match: str = ""
    judge_s: float = 0.0
    judged_at: float | None = None
    first_seen: float | None = None
    final_seen: float | None = None


@dataclass
class Player:
    i: int
    handle: str
    ip: str
    behaviour: str
    http: httpx.AsyncClient = None
    user_id: int = 0
    code: str = ""
    solved: set = field(default_factory=set)
    end_time: int = 0
    team: int | None = None


@dataclass
class Match:
    code: str
    mode: str
    rules: str
    players: list
    labels: list = field(default_factory=list)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    final: asyncio.Event = field(default_factory=asyncio.Event)
    end_time: int = 0
    ended_at: float | None = None
    final_at: float | None = None
    claimed: set = field(default_factory=set)
    team_solved: dict = field(default_factory=dict)  # team -> labels (team ICPC)


class Run:
    def __init__(self, n: int, args, workdir: Path):
        self.n = n
        self.args = args
        self.rng = random.Random(args.seed + n)
        self.workdir = workdir
        self.cf_port = free_port()
        self.app_port = free_port()
        self.cf_url = f"http://127.0.0.1:{self.cf_port}"
        self.app_url = f"http://127.0.0.1:{self.app_port}"
        self.players: list[Player] = []
        self.matches: dict[str, Match] = {}
        self.subs: dict[tuple[str, int], Sub] = {}
        self.fanout: dict[tuple, list[float]] = {}
        self.samples: list[dict] = []
        self.health: list[dict] = []
        self.ws_messages = 0
        self.ws_clients = 0
        self.errors: list[str] = []
        self.procs: list[subprocess.Popen] = []
        self.phase_marks: dict[str, float] = {}

    # ---------- processes ----------

    def start_processes(self) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", FAKE_CF_LATENCY_MS=self.args.cf_latency_ms)
        env.pop("HTTPS_PROXY", None)
        env.pop("HTTP_PROXY", None)
        cf_log = open(self.workdir / "fake_cf.log", "w")
        self.procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tools.fake_cf:app", "--port", str(self.cf_port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=cf_log, stderr=subprocess.STDOUT))
        app_env = dict(env, CF_API_BASE=f"{self.cf_url}/api", DB_PATH=str(self.workdir / "load.db"),
                       SECRET_KEY="loadtest-secret", COOKIE_SECURE="0", LOADTEST_CONFIG=self.args.config)
        app_log = open(self.workdir / "app.log", "w")
        self.app = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tools.tuned_app:app", "--port", str(self.app_port), "--workers", "1",
             "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"],
            cwd=ROOT, env=app_env, stdout=app_log, stderr=subprocess.STDOUT)
        self.procs.append(self.app)

    def stop_processes(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    async def wait_up(self, client: httpx.AsyncClient) -> None:
        for _ in range(200):
            try:
                if (await client.get(f"{self.app_url}/api/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("app did not start; see app.log")

    async def sampler(self, stop: asyncio.Event) -> None:
        """CPU and RSS of the app process, plus /api/health, every 2 s."""
        pid = self.app.pid
        last_ticks, last_t = None, None
        async with httpx.AsyncClient() as client:
            while not stop.is_set():
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    ticks = int(fields[11]) + int(fields[12])
                    rss_kb = next(int(line.split()[1]) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                                  if line.startswith("VmRSS:"))
                except (FileNotFoundError, StopIteration):
                    break
                now = time.time()
                if last_ticks is not None:
                    cpu = (ticks - last_ticks) / CLK_TCK / (now - last_t) * 100
                    self.samples.append({"t": now, "cpu": cpu, "rss_mb": rss_kb / 1024})
                last_ticks, last_t = ticks, now
                try:
                    h = (await client.get(f"{self.app_url}/api/health", timeout=5)).json()
                    h["t"] = now
                    self.health.append(h)
                except (httpx.HTTPError, ValueError):
                    pass
                try:
                    await asyncio.wait_for(stop.wait(), 2)
                except asyncio.TimeoutError:
                    pass

    # ---------- fake CF helpers ----------

    async def cf_submit(self, handle: str, problem: str, verdict: str) -> dict:
        r = await self.cf.post(f"{self.cf_url}/fake/submit", params={"handle": handle, "problem": problem, "verdict": verdict})
        return r.json()

    async def cf_verdict(self, sub_id: int, verdict: str) -> None:
        await self.cf.post(f"{self.cf_url}/fake/verdict", params={"sub_id": sub_id, "verdict": verdict})

    # ---------- setup ----------

    async def login(self, p: Player) -> None:
        h = {"X-Forwarded-For": p.ip}
        for _ in range(60):
            r = await p.http.post("/api/auth/start", json={"handle": p.handle}, headers=h)
            if r.status_code == 200:
                break
            await asyncio.sleep(2)  # problem list still loading
        else:
            raise RuntimeError(f"auth/start failed for {p.handle}: {r.text}")
        task = r.json()
        await asyncio.sleep(self.rng.uniform(3, 15))  # time to submit the compile error
        await self.cf_submit(p.handle, task["problem"]["key"], "COMPILATION_ERROR")
        for _ in range(100):
            r = await p.http.post("/api/auth/check", json={"task_id": task["task_id"]}, headers=h)
            if r.json().get("status") == "verified":
                break
            await asyncio.sleep(7)
        else:
            raise RuntimeError(f"verification never finished for {p.handle}")
        p.user_id = (await p.http.get("/api/me")).json()["user"]["id"]

    async def create_contest(self) -> None:
        """One hosted contest for everyone. The host is an extra account that doesn't play."""
        problems = json.loads((ROOT / "tests" / "fixtures" / "problemset.json").read_text())["result"]["problems"]
        keys, names = [], set()
        for p in problems:  # skip Div.1/Div.2 copies (same name)
            if 1200 <= (p.get("rating") or 0) <= 1800 and p["name"] not in names and len(keys) < 5:
                keys.append(f"{p['contestId']}{p['index']}")
                names.add(p["name"])
        host = self.host
        r = await host.http.post("/api/contests", json={
            "title": f"Load test {self.n}", "rules": "icpc", "format": "solo", "visibility": "unlisted",
            "duration_min": self.args.duration,
            "layout": self.args.layout, "freeze_min": 5, "problems": " ".join(keys)})
        if r.status_code != 200:
            raise RuntimeError(f"contest create failed: {r.text}")
        code = r.json()["code"]
        for p in self.players:
            r = await p.http.post(f"/api/matches/{code}/join")
            if r.status_code != 200:
                raise RuntimeError(f"join failed: {r.text}")
            p.code = code
        self.matches[code] = Match(code, "contest", "icpc", list(self.players))
        r = await host.http.post(f"/api/contests/{code}/start", json={"force": True})
        if r.status_code != 200:
            raise RuntimeError(f"contest start failed: {r.text}")

    async def create_matches(self) -> None:
        if self.args.layout == "contest":
            return await self.create_contest()
        idx = 0
        settings = {"count": 3, "rating_min": 1200, "rating_max": 1600, "duration_min": self.args.duration}
        for mode, rules, size in layout(self.n):
            group = self.players[idx:idx + size]
            idx += size
            s = dict(settings)
            if mode == "team":
                s["team_size"] = size // 2
            r = await group[0].http.post("/api/matches", json={"mode": mode, "rules": rules, "settings": s})
            if r.status_code != 200:
                raise RuntimeError(f"create failed: {r.text}")
            code = r.json()["code"]
            for p in group[1:]:
                r = await p.http.post(f"/api/matches/{code}/join")
                if r.status_code != 200:
                    raise RuntimeError(f"join failed: {r.text}")
            for p in group:
                p.code = code
            self.matches[code] = Match(code, mode, rules, group)
        # everyone presses Ready at once
        await asyncio.gather(*[p.http.post(f"/api/matches/{p.code}/ready", json={"ready": True}) for p in self.players])

    async def wait_running(self) -> None:
        deadline = time.time() + 600
        pending = set(self.matches)
        while pending and time.time() < deadline:
            for code in list(pending):
                m = self.matches[code]
                st = (await m.players[0].http.get(f"/api/matches/{code}")).json()
                if st["status"] == "running" and st["problems"]:
                    m.labels = [(x["label"], x["key"]) for x in st["problems"]]
                    m.end_time = st["end_time"]
                    for p in m.players:
                        me = next(x for x in st["players"] if x["user_id"] == p.user_id)
                        p.end_time, p.team = me["end_time"], me.get("team")
                    pending.discard(code)
                elif m.mode != "contest" and st["status"] == "lobby" and not all(x["ready"] for x in st["players"]):
                    # bounced back to the lobby (e.g. CF hiccup): ready again
                    await asyncio.gather(*[p.http.post(f"/api/matches/{code}/ready", json={"ready": True})
                                           for p in m.players])
            await asyncio.sleep(2)
        if pending:
            raise RuntimeError(f"matches never started: {pending}")

    # ---------- WebSockets ----------

    async def ws_client(self, code: str, p: Player | None, ready: asyncio.Event, stop: asyncio.Event,
                        conns: list) -> None:
        url = self.app_url.replace("http", "ws") + "/ws"
        headers = {}
        if p is not None:
            headers["Cookie"] = f"cf_session={p.http.cookies.get('cf_session')}"
        ws = await websockets.connect(url, additional_headers=headers, max_size=None, ping_interval=None)
        conns.append((p, ws))
        self.ws_clients += 1
        await ws.send(json.dumps({"type": "subscribe", "data": {"code": code}}))
        ready.set()
        m = self.matches[code]
        try:
            async for raw in ws:
                now = time.time()
                self.ws_messages += 1
                msg = json.loads(raw)
                t, data = msg.get("type"), msg.get("data") or {}
                if t == "standings":
                    changed = data.get("changed") or []
                    sig = (code, tuple(sorted((c["handle"].lower(), c["time"], c["verdict"] or "") for c in changed)))
                    if changed:  # frozen viewers get empty updates; those can't be matched across sockets
                        self.fanout.setdefault(sig, []).append(now)
                    if p is None:
                        continue
                    for c in changed:
                        sub = self.subs.get((c["handle"].lower(), c["time"]))
                        if sub is None or c["handle"].lower() != p.handle.lower():
                            continue
                        if sub.first_seen is None:
                            sub.first_seen = now
                        if c["verdict"] == sub.verdict and sub.final_seen is None:
                            sub.final_seen = now
                        if c["accepted"] and m.rules == "lockout":
                            m.claimed.add(c["label"])
                elif t == "match.ended":
                    if m.ended_at is None:
                        m.ended_at = now
                    m.ended.set()
                elif t == "match.result":
                    if m.final_at is None:
                        m.final_at = now
                    m.final.set()
                if stop.is_set():
                    break
        except websockets.ConnectionClosed:
            pass

    # ---------- players ----------

    async def judge(self, sub: Sub, sub_id: int) -> None:
        delay = self.rng.uniform(40, 90) if self.rng.random() < 0.05 else self.rng.uniform(2, 20)
        sub.judge_s = delay
        await asyncio.sleep(delay)
        await self.cf_verdict(sub_id, sub.verdict)
        sub.judged_at = time.time()

    async def play(self, p: Player, ws) -> None:
        m = self.matches[p.code]
        rng = random.Random(self.args.seed * 1000 + p.i)
        tasks = []
        # first submission comes 1-5 minutes in; afterwards every sub-min..sub-max seconds
        wait = rng.uniform(min(60, self.args.sub_min), self.args.sub_max)
        while True:
            try:
                await asyncio.wait_for(m.ended.wait(), wait)
                break
            except asyncio.TimeoutError:
                pass
            if time.time() > p.end_time - 20:
                break
            team_done = m.team_solved.get(p.team, set()) if m.mode == "team" else set()
            left = [(lab, key) for lab, key in m.labels
                    if lab not in p.solved and lab not in m.claimed and lab not in team_done]
            if not left:
                break
            label, key = rng.choice(left)
            verdict = pick(rng, VERDICTS)
            res = await self.cf_submit(p.handle, key, "TESTING")
            sub = Sub(p.handle, p.behaviour, res["creationTimeSeconds"], time.time(), verdict,
                      match=f"{m.mode}-{m.rules}-{len(m.players)}")
            self.subs[(p.handle.lower(), sub.created)] = sub
            tasks.append(asyncio.create_task(self.judge(sub, res["id"])))
            if verdict == "OK":
                p.solved.add(label)
                m.team_solved.setdefault(p.team, set()).add(label)
            if p.behaviour == "button":
                await asyncio.sleep(rng.uniform(2, 8))  # switch tabs, press the button
                await ws.send(json.dumps({"type": "submitted", "data": {"code": p.code}}))
            elif p.behaviour == "focus":
                await asyncio.sleep(rng.uniform(5, 30))
                await ws.send(json.dumps({"type": "focus", "data": {"away_s": 60}}))
            wait = rng.uniform(self.args.sub_min, self.args.sub_max)
        await asyncio.gather(*tasks)

    # ---------- main ----------

    async def run(self) -> dict:
        self.start_processes()
        stop = asyncio.Event()
        try:
            async with httpx.AsyncClient() as cf:
                self.cf = cf
                await self.wait_up(cf)
                sampler = asyncio.create_task(self.sampler(stop))
                t0 = time.time()
                for i in range(self.n):
                    self.players.append(Player(i, f"load{self.n}u{i:02d}", f"10.0.{i // 200}.{i % 200 + 1}",
                                               pick(self.rng, BEHAVIOURS)))
                self.host = Player(self.n, f"load{self.n}host", "10.9.9.9", "none")
                everyone = self.players + ([self.host] if self.args.layout == "contest" else [])
                for p in everyone:
                    p.http = httpx.AsyncClient(base_url=self.app_url, headers=HEADERS, timeout=120)
                await asyncio.gather(*[self.login(p) for p in everyone])
                self.phase_marks["logged_in"] = time.time() - t0
                await self.create_matches()
                await self.wait_running()
                self.phase_marks["running"] = time.time() - t0
                match_start = time.time()

                conns: list = []
                ws_tasks = []
                for p in self.players:
                    ev = asyncio.Event()
                    ws_tasks.append(asyncio.create_task(self.ws_client(p.code, p, ev, stop, conns)))
                    await ev.wait()
                for code in self.matches:
                    for _ in range(self.args.spectators):
                        ev = asyncio.Event()
                        ws_tasks.append(asyncio.create_task(self.ws_client(code, None, ev, stop, conns)))
                        await ev.wait()
                ws_of = {id(p): ws for p, ws in conns if p is not None}
                await asyncio.gather(*[self.play(p, ws_of[id(p)]) for p in self.players])
                self.phase_marks["play_done"] = time.time() - t0
                # wait for every result (final checks need Codeforces)
                try:
                    await asyncio.wait_for(asyncio.gather(*[m.final.wait() for m in self.matches.values()]), 600)
                except asyncio.TimeoutError:
                    self.errors.append("some results never became final")
                self.phase_marks["all_final"] = time.time() - t0
                match_end = time.time()
                calls = (await cf.get(f"{self.cf_url}/fake/calls")).json()
                stop.set()
                for _, ws in conns:
                    await ws.close()
                await asyncio.gather(*ws_tasks, return_exceptions=True)
                await sampler
                for p in everyone:
                    await p.http.aclose()
            return self.report(calls, t0, match_start, match_end)
        finally:
            stop.set()
            self.stop_processes()

    def report(self, calls, t0, match_start, match_end) -> dict:
        subs = list(self.subs.values())
        missed = [s for s in subs if s.final_seen is None]
        by = {}
        for name in ("button", "focus", "none", "all"):
            group = [s for s in subs if (name == "all" or s.behaviour == name) and s.final_seen is not None]
            by[name] = {
                "from_creation_s": summary([s.final_seen - s.created for s in group]),
                "from_verdict_ready_s": summary([s.final_seen - s.judged_at for s in group if s.judged_at]),
            }
        starts = sorted(c["start"] for c in calls)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        overlaps = sum(1 for a, b in zip(sorted(calls, key=lambda c: c["start"]), sorted(calls, key=lambda c: c["start"])[1:])
                       if b["start"] < a["end"])
        in_match = [s for s in starts if match_start <= s <= match_end]
        per_min = []
        if in_match:
            t = match_start
            while t < match_end:
                per_min.append(sum(1 for s in in_match if t <= s < t + 60))
                t += 60
        rolling = max((sum(1 for s in starts if a <= s < a + 60) for a in starts), default=0)
        methods = {}
        for c in calls:
            methods[c["method"]] = methods.get(c["method"], 0) + 1
        live = [x for x in self.samples if match_start <= x["t"] <= match_end]
        spreads = [max(v) - min(v) for v in self.fanout.values() if len(v) > 1]
        receivers = [len(v) for v in self.fanout.values()]
        poll_depth = [h["lanes"].get("POLL", 0) + h["lanes"].get("INTERACTIVE", 0) for h in self.health
                      if match_start <= h["t"] <= match_end]
        return {
            "players": self.n,
            "config_overrides": json.loads(self.args.config or "{}"),
            "matches": [{"mode": m.mode, "rules": m.rules, "size": len(m.players),
                         "end_to_final_s": round(m.final_at - m.ended_at, 1) if m.final_at and m.ended_at else None,
                         "ended_early": bool(m.ended_at and m.ended_at < m.end_time - 5)}
                        for m in self.matches.values()],
            "duration_min": self.args.duration,
            "layout": self.args.layout,
            "phases_s": {k: round(v, 1) for k, v in self.phase_marks.items()},
            "submissions": len(subs),
            "missed_on_ws": len(missed),
            "latency": by,
            "cf": {
                "total_calls": len(calls),
                "methods": methods,
                "min_gap_s": round(min(gaps), 3) if gaps else None,
                "overlapping_calls": overlaps,
                "per_minute_in_match": {"max": max(per_min, default=0), "mean": round(statistics.mean(per_min), 1) if per_min else 0},
                "max_in_any_60s": rolling,
            },
            "process": {
                "cpu_pct": summary([x["cpu"] for x in live]),
                "rss_mb": summary([x["rss_mb"] for x in self.samples]),
            },
            "ws": {
                "clients": self.ws_clients,
                "messages_received": self.ws_messages,
                "broadcasts": len(self.fanout),
                "receivers_per_broadcast": summary(receivers),
                "fanout_spread_ms": summary([x * 1000 for x in spreads]),
            },
            "queue_depth_poll_plus_interactive": summary(poll_depth),
            "errors": self.errors,
            "subs": [{"behaviour": s.behaviour, "match": s.match, "verdict": s.verdict, "judge_s": round(s.judge_s, 1),
                      "creation_to_ws_s": round(s.final_seen - s.created, 1) if s.final_seen else None,
                      "ready_to_ws_s": round(s.final_seen - s.judged_at, 1) if s.final_seen and s.judged_at else None,
                      "first_seen_s": round(s.first_seen - s.created, 1) if s.first_seen else None}
                     for s in subs],
        }


def print_summary(r: dict) -> None:
    lat = r["latency"]
    print(f"\n=== {r['players']} players, {len(r['matches'])} matches, {r['submissions']} submissions ===")
    for name in ("button", "focus", "none", "all"):
        c, v = lat[name]["from_creation_s"], lat[name]["from_verdict_ready_s"]
        print(f"  {name:7s} creation->WS p50 {c.get('p50')}  p90 {c.get('p90')}  max {c.get('max')}  (n={c['n']})"
              f" | verdict-ready->WS p50 {v.get('p50')}  p90 {v.get('p90')}  max {v.get('max')}")
    cf = r["cf"]
    print(f"  CF calls {cf['total_calls']}, min gap {cf['min_gap_s']} s, overlaps {cf['overlapping_calls']},"
          f" max/min in match {cf['per_minute_in_match']['max']}, max in any 60 s {cf['max_in_any_60s']}")
    print(f"  CPU {r['process']['cpu_pct']}  RSS {r['process']['rss_mb']}")
    print(f"  WS {r['ws']}")
    print(f"  missed {r['missed_on_ws']}  phases {r['phases_s']}  errors {r['errors']}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", type=int, nargs="+", default=[20, 30, 40])
    ap.add_argument("--duration", type=int, default=15, help="match length in minutes (app minimum is 15)")
    ap.add_argument("--sub-min", type=float, default=180, help="seconds between a player's submissions (min)")
    ap.add_argument("--sub-max", type=float, default=360, help="seconds between a player's submissions (max)")
    ap.add_argument("--spectators", type=int, default=1, help="extra anonymous WebSockets per match")
    ap.add_argument("--parallel", action="store_true", help="run the player counts at the same time (separate stacks)")
    ap.add_argument("--layout", choices=["mix", "contest"], default="mix",
                    help="mix: rooms, 1v1s and team duels; contest: everyone in one hosted contest")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cf-latency-ms", default="300-700", help="fake CF answer time, e.g. 300-700 or 0")
    ap.add_argument("--config", default="", help='JSON overrides for app/config.py, e.g. \'{"HARD_HINT_DELAY_S": 4}\'')
    ap.add_argument("--out", default="loadtest-results.json")
    args = ap.parse_args()

    base = Path(tempfile.mkdtemp(prefix="clashforces-load-"))
    print(f"work dir: {base}")
    runs = []
    for n in args.players:
        d = base / f"n{n}"
        d.mkdir()
        runs.append(Run(n, args, d))
    if args.parallel:
        results = await asyncio.gather(*[r.run() for r in runs])
    else:
        results = []
        for r in runs:
            results.append(await r.run())
            print_summary(results[-1])
            Path(args.out).write_text(json.dumps(results, indent=2))  # keep finished runs if a later one fails
    for r in results:
        print_summary(r)
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    # The app under test always talks to the local fake; refuse anything that looks like production.
    for var in ("CF_API_BASE",):
        if os.environ.get(var) and urlsplit(os.environ[var]).hostname not in ("127.0.0.1", "localhost"):
            sys.exit(f"{var} points at {os.environ[var]}; the load test only runs against the local fake.")
    asyncio.run(main())
