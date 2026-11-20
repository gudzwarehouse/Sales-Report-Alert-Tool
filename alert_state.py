"""Remembers which alerts were already emailed for a time slot, so a slot that
is re-checked every ~40 minutes sends each alert once.

Slots: 1h alerts -> one per hourly slot (max 24/day per SKU/total); 4h alerts
-> one per 4-hour block 00-04, 04-08, ... (max 6/day). A SKU that later
escalates to a HIGHER threshold in the same slot still alerts (its key
includes the threshold, so the SMS/phone bucket isn't swallowed by an earlier
email-only alert).

Persisted to state/alert_state.json next to this file (gitignored). A
read/write failure never blocks an alert -- worst case is a duplicate email.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

STATE_PATH = Path(__file__).parent / "state" / "alert_state.json"
KEEP_DAYS = 3


def slot_key(window_label: str, window_end: datetime.datetime) -> str:
    """1h: the hour the window ends at. 4h: the 4-hour block the window's last
    completed hour falls in (rolling windows ending 09:00..12:00 -> block 08-12)."""
    if window_label == "4h":
        last_hour = window_end - datetime.timedelta(hours=1)
        block = last_hour.hour // 4 * 4
        return f"4h@{last_hour:%Y-%m-%d}T{block:02d}"
    return f"1h@{window_end:%Y-%m-%dT%H}"


class AlertState:
    def __init__(self, slot: str, path: Path = STATE_PATH) -> None:
        self.slot = slot
        self.path = path
        try:
            self._sent: dict[str, str] = json.loads(path.read_text(encoding="utf-8")).get("sent", {})
        except (OSError, ValueError):
            self._sent = {}

    def _key(self, kind: str, ident: str, threshold: int) -> str:
        return f"{self.slot}|{kind}|{ident}|{threshold}"

    def is_new(self, kind: str, ident: str, threshold: int) -> bool:
        return self._key(kind, ident, threshold) not in self._sent

    def mark(self, kind: str, ident: str, threshold: int) -> None:
        now = datetime.datetime.now()
        try:  # merge with marks other AlertState objects wrote since we loaded
            self._sent = {**json.loads(self.path.read_text(encoding="utf-8")).get("sent", {}), **self._sent}
        except (OSError, ValueError):
            pass
        self._sent[self._key(kind, ident, threshold)] = now.isoformat(timespec="seconds")
        cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
        self._sent = {k: v for k, v in self._sent.items() if v >= cutoff}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"sent": self._sent}, indent=1), encoding="utf-8")
        except OSError:
            pass
