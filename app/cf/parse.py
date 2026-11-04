"""Turn Codeforces API JSON into our dataclasses."""

from app.models import TESTING, Contest, Problem, Submission


GYM_MIN_ID = 100000  # gym contests and mashups have ids from here up


def problem_key(contest_id, index) -> str:
    return f"{contest_id}{index}"


def is_gym(contest_id) -> bool:
    return contest_id is not None and int(contest_id) >= GYM_MIN_ID


def _base(contest_id) -> str:
    return f"https://codeforces.com/{'gym' if is_gym(contest_id) else 'contest'}/{contest_id}"


def problem_url(contest_id, index) -> str | None:
    return f"{_base(contest_id)}/problem/{index}" if contest_id else None


def contest_url(contest_id) -> str | None:
    return _base(contest_id) if contest_id else None


def submission_url(contest_id, submission_id) -> str | None:
    return f"{_base(contest_id)}/submission/{submission_id}" if contest_id else None


def parse_problems(result: dict) -> list[Problem]:
    out = []
    for p in result.get("problems", []):
        if "contestId" not in p:
            continue  # old acmsguru-style entries without a contest
        out.append(Problem(
            key=problem_key(p["contestId"], p["index"]),
            contest_id=p["contestId"],
            index=p["index"],
            name=p.get("name", ""),
            rating=p.get("rating"),
            tags=tuple(p.get("tags", [])),
        ))
    return out


def parse_contests(result: list) -> list[Contest]:
    return [
        Contest(
            id=c["id"],
            name=c.get("name", ""),
            phase=c.get("phase", "FINISHED"),
            start_time=c.get("startTimeSeconds"),
            duration=c.get("durationSeconds"),
        )
        for c in result
    ]


def parse_submissions(result: list, handle: str) -> list[Submission]:
    out = []
    for s in result:
        p = s.get("problem", {})
        contest_id = p.get("contestId", s.get("contestId"))
        if contest_id is None:
            continue
        out.append(Submission(
            id=s["id"],
            handle=handle,
            problem_key=problem_key(contest_id, p.get("index", "")),
            verdict=s.get("verdict") or TESTING,
            time=s["creationTimeSeconds"],
            passed_tests=s.get("passedTestCount", 0),
            language=s.get("programmingLanguage", ""),
            problem_name=p.get("name", ""),
        ))
    return out
