"""In-memory ring of recent log lines for the admin portal's Logs card.

Installed on the root logger when the portal starts, so the operator can read
the last few hundred lines from the browser without journal access.
"""

from __future__ import annotations

import logging
from collections import deque
from threading import Lock

_LINES: deque[dict] = deque(maxlen=600)
_LOCK = Lock()
_INSTALLED = False


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        with _LOCK:
            _LINES.append({"t": record.created, "level": record.levelname,
                           "logger": record.name, "msg": msg})


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    h = RingHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(h)
    _INSTALLED = True


def tail(n: int = 200, level: str | None = None) -> list[dict]:
    with _LOCK:
        lines = list(_LINES)
    if level:
        order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        floor = order.index(level.upper()) if level.upper() in order else 0
        lines = [l for l in lines if order.index(l["level"]) >= floor]
    return lines[-n:]
