"""Store submissions seen by the poller and keep derived data up to date."""

from app import clock, db
from app.cf import cache
from app.models import OK, Submission


def group_keys(problem_key: str) -> list[str]:
    """All problem keys in the same Div.1/Div.2 group as this one."""
    p = cache.get(problem_key)
    if p is None:
        return [problem_key]
    return [r["key"] for r in db.all("SELECT key FROM problems WHERE dup_group = ?", (p.group,))] or [problem_key]


def store(user_id: int, subs: list[Submission]) -> list[Submission]:
    """Upsert submissions. Returns the ones that are new or changed verdict."""
    changed = []
    now = clock.now()
    for s in subs:
        old = db.one("SELECT verdict FROM submissions WHERE id = ?", (s.id,))
        if old is None:
            db.execute(
                "INSERT INTO submissions(id, user_id, handle, problem_key, verdict, passed_tests, creation_time,"
                " language, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (s.id, user_id, s.handle, s.problem_key, s.verdict, s.passed_tests, s.time, s.language, now),
            )
            changed.append(s)
        elif old["verdict"] != s.verdict:
            db.execute("UPDATE submissions SET verdict = ?, passed_tests = ? WHERE id = ?",
                       (s.verdict, s.passed_tests, s.id))
            changed.append(s)
    mark_touched(user_id, subs)
    cache.learn_gym_names(subs)
    if subs:
        db.execute("UPDATE users SET last_sub_id = MAX(last_sub_id, ?) WHERE id = ?",
                   (max(s.id for s in subs), user_id))
    return changed


def mark_touched(user_id: int, subs: list[Submission]) -> None:
    rows = {}
    for s in subs:
        rows[s.problem_key] = max(rows.get(s.problem_key, 0), 1 if s.verdict == OK else 0)
    db.executemany(
        "INSERT INTO touched(user_id, problem_key, solved) VALUES (?,?,?)"
        " ON CONFLICT(user_id, problem_key) DO UPDATE SET solved = MAX(solved, excluded.solved)",
        [(user_id, k, v) for k, v in rows.items()],
    )
    solved = [k for k, v in rows.items() if v]
    now = clock.now()
    for key in solved:
        for k in group_keys(key):
            db.execute("UPDATE upsolve SET done_at = ? WHERE user_id = ? AND problem_key = ? AND done_at IS NULL",
                       (now, user_id, k))


def touched_groups(user_id: int) -> set[str]:
    rows = db.all(
        "SELECT COALESCE(p.dup_group, t.problem_key) AS g FROM touched t"
        " LEFT JOIN problems p ON p.key = t.problem_key WHERE t.user_id = ?",
        (user_id,),
    )
    return {r["g"] for r in rows}
