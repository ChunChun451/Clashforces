"""Plain data types shared by the pure core and the I/O layers."""

from dataclasses import dataclass, field

OK = "OK"
COMPILATION_ERROR = "COMPILATION_ERROR"
TESTING = "TESTING"


@dataclass(frozen=True)
class Problem:
    key: str              # "2043C"
    contest_id: int
    index: str            # "C"
    name: str
    rating: int | None
    tags: tuple[str, ...]
    dup_group: str = ""   # same for Div.1/Div.2 copies; filled by the cache

    @property
    def group(self) -> str:
        return self.dup_group or self.key


@dataclass(frozen=True)
class Contest:
    id: int
    name: str
    phase: str
    start_time: int | None
    duration: int | None

    @property
    def running(self) -> bool:
        return self.phase not in ("FINISHED", "BEFORE")


@dataclass(frozen=True)
class Submission:
    """A Codeforces submission as returned by user.status."""
    id: int
    handle: str
    problem_key: str
    verdict: str          # missing verdict is stored as TESTING
    time: int             # creationTimeSeconds (Codeforces time)
    passed_tests: int = 0
    language: str = ""


# ---------- scoring inputs ----------

@dataclass(frozen=True)
class Sub:
    """A submission as the scoring functions see it."""
    id: int
    player: int | str
    problem: str
    verdict: str
    time: int


@dataclass(frozen=True)
class PlayerInput:
    id: int | str
    end_time: int
    problems: tuple[str, ...]
    penalty_bonus: int = 0       # minutes, may be negative
    forfeited: bool = False


@dataclass
class Cell:
    problem: str
    tries: int = 0               # rejected tries before OK (or total rejected if unsolved)
    solved: bool = False
    solved_at: int | None = None # Codeforces time of first OK
    minute: int | None = None    # whole minutes from start to first OK
    pending: bool = False        # an in-window submission is still TESTING
    points: float = 0.0          # lockout only
    first: bool = False          # lockout: this player owns (or shares) the problem


@dataclass
class Row:
    player: int | str
    place: int = 0
    solved: int = 0
    penalty: int = 0
    points: float = 0.0
    last_ok: int | None = None   # time of last OK that counts for the tie-break
    cells: dict[str, Cell] = field(default_factory=dict)
    forfeited: bool = False
