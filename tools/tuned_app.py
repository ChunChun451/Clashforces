"""The app with app/config.py values overridden from $LOADTEST_CONFIG (JSON). Load tests only.

    LOADTEST_CONFIG='{"HARD_HINT_DELAY_S": 4}' uvicorn tools.tuned_app:app

Overrides are applied before app.main is imported, so defaults bound at import time see them.
"""

import json
import os

from app import config

for key, value in json.loads(os.environ.get("LOADTEST_CONFIG") or "{}").items():
    if not hasattr(config, key):
        raise SystemExit(f"app.config has no setting {key}")
    setattr(config, key, value)

from app.main import app  # noqa: E402

__all__ = ["app"]
