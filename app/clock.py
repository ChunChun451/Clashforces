"""Server time in unix seconds. Tests replace `now` to control time."""

import time


def now() -> int:
    return int(time.time())
