r"""Shared, human-readable activity log on the management server, so anyone
with drive access can see pipeline runs without opening this repo or a
database -- same pattern as VB Stock update's db/job_tracker.py.

Writes one timestamped line per event to a daily file under
\\mbc-nt01\Management\Final Automations\Sales Report Tool\log\, in addition
to whatever run_scheduler.bat already captures locally in logs\. Purely an
outward-facing audit trail -- nothing here is ever read back by code.

Fire-and-forget: a log write failing (the share being briefly unreachable)
must never break the real pipeline run it's recording.
"""

from __future__ import annotations

import datetime
import os

SHARED_LOG_DIR = r"\\mbc-nt01\Management\Final Automations\Sales Report Tool\log"


def _timestamp() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def log_activity(level: str, message: str, *, component: str = "sales_pipeline") -> None:
    """Append one line to today's shared log file. Never raises."""
    line = f"{_timestamp()} [{level}] {component}: {message}\n"
    try:
        os.makedirs(SHARED_LOG_DIR, exist_ok=True)
        date_str = datetime.date.today().isoformat()
        path = os.path.join(SHARED_LOG_DIR, f"{component}_{date_str}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
