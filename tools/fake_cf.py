"""A tiny fake Codeforces API for local development (no network needed).

Run:   python -m uvicorn tools.fake_cf:app --port 9000
Then:  CF_API_BASE=http://127.0.0.1:9000/api uvicorn app.main:app

Problems and contests come from tests/fixtures. Any handle exists, except
handles starting with "ghost". Add submissions with:

    curl -X POST 'http://127.0.0.1:9000/fake/submit?handle=alice&problem=2010D&verdict=OK'

verdict can be OK, WRONG_ANSWER, COMPILATION_ERROR, TESTING, ...
"""

import json
import time
from pathlib import Path

from fastapi import FastAPI, Query

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
app = FastAPI()
subs: dict[str, list] = {}
next_id = [1000]


def ok(result):
    return {"status": "OK", "result": result}


@app.get("/api/problemset.problems")
def problemset():
    return json.loads((FIX / "problemset.json").read_text())


@app.get("/api/contest.list")
def contest_list(gym: str = "false"):
    return json.loads((FIX / "contest_list.json").read_text())


@app.get("/api/user.info")
def user_info(handles: str):
    out = []
    for h in handles.split(";"):
        if h.lower().startswith("ghost"):
            return {"status": "FAILED", "comment": f"handles: User with handle {h} not found"}
        out.append({"handle": h, "rating": 1500 + (sum(map(ord, h)) % 9) * 100, "maxRating": 1900})
    return ok(out)


@app.get("/api/user.status")
def user_status(handle: str, count: int = 100000, from_: int = Query(1, alias="from")):
    newest_first = list(reversed(subs.get(handle.lower(), [])))
    return ok(newest_first[from_ - 1:from_ - 1 + count])


@app.post("/fake/submit")
def submit(handle: str, problem: str, verdict: str = "OK", ago: int = 0):
    contest, index = problem[:-1], problem[-1]
    if not contest.isdigit():
        contest, index = problem[:-2], problem[-2:]
    next_id[0] += 1
    s = {"id": next_id[0], "contestId": int(contest), "creationTimeSeconds": int(time.time()) - ago,
         "problem": {"contestId": int(contest), "index": index}, "programmingLanguage": "C++20",
         "passedTestCount": 3}
    if verdict != "TESTING":
        s["verdict"] = verdict
    subs.setdefault(handle.lower(), []).append(s)
    return {"id": s["id"]}


@app.post("/fake/verdict")
def set_verdict(sub_id: int, verdict: str):
    for lst in subs.values():
        for s in lst:
            if s["id"] == sub_id:
                s["verdict"] = verdict
    return {"ok": True}
