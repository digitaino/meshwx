"""The status channel: one short line on the mesh when the station's state
changes, so whoever follows "#meshwx-status" hears of an outage from the
bot itself and not from a `sat` reply that happened to mention it.

Fed every 10 s by the dashboard poll in sdr_monitor.py (the receiver and
the Pi) together with the EMWIN source (the internet backup, how late the
products run). Nothing here is a request or an answer: the lines go on a
channel of their own, never the request channel, and only while transmit
is on.

What makes a line, and what clears it:
- GOES lock lost, once the receiver has been unlocked ten minutes without a
  break (a flap is not an outage, and pointing mode is the operator at work,
  not an outage); "locked again" on recovery.
- The internet backup on (how long the dish had been quiet) and off (how
  many products came that way).
- Products running an hour or more behind the uplink (NOAA's replay of
  2026-10-16); cleared once under half an hour.
- The receiver dashboard unreachable for five minutes; reachable again.
- The Pi: disk 85 % full, CPU at 70 C, or the firmware's throttle flags set;
  one line, and one when all three are back to normal, with a margin so a
  temperature that hovers on the line does not post twice an hour.

Changes seen in the same poll go out as one line, joined with "; ", when
they fit. The same condition is not repeated within half an hour, and no
more than six conditions are reported in an hour, so never more than six
lines go out, whatever happens. A condition held back by the hourly cap is
tried again at the next poll while it lasts; a clearing line held back is
dropped, a stale "all clear" being worse than none.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

from meshcore_weather.config import settings

logger = logging.getLogger("meshcore_weather.status")

LOCK_LOST_AFTER_S = 600
DASHBOARD_DOWN_AFTER_S = 300
LAG_ON_MIN, LAG_OFF_MIN = 60, 30
DISK_PCT, DISK_OK_PCT = 85, 80
TEMP_C, TEMP_OK_C = 70.0, 65.0
REPEAT_S = 1800
PER_HOUR = 6
MAX_LEN = 120


def _m(seconds: float) -> str:
    return f"{int(seconds // 60)}m"


def _behind(minutes: int) -> str:
    return f"{minutes}m" if minutes < 120 else f"{round(minutes / 60)}h"


def _throttled(value) -> bool:
    """The firmware's throttle flags as the dashboard reports them ("0x0",
    "0x50005"); missing or zero means none. Only the low four bits count
    (under-voltage, capped, throttled, soft limit: now). The high bits say
    the same happened at some point since boot and stay set until the next
    one, which would hold the Pi line "bad" forever and swallow every later
    disk or temperature line."""
    try:
        return int(str(value or "0x0"), 0) & 0xF != 0
    except ValueError:
        return bool(value)


class StatusReporter:
    """Decides the lines (`check`, no I/O, a clock passed in) and posts them
    (`tick`). `send` takes the finished line and says whether it went out;
    `name` is the bot's name on the node, read when a line is built."""

    def __init__(self, send: Callable[[str], Awaitable[bool]], name: Callable[[], str | None]):
        self._send = send
        self._name = name
        self._unlocked_since: float | None = None
        self._lock_said = False
        self._down_since: float | None = None
        self._down_said = False
        self._backup_on: bool | None = None        # None until the first look: a start is not a change
        self._lag_said = False
        self._host_said = False
        self._posted_at: dict[str, float] = {}
        self._hour: deque[float] = deque()
        self.recent: deque[tuple[float, str]] = deque(maxlen=20)

    def check(self, now: float, state: dict | None, reachable: bool, feed=None) -> list[str]:
        """One look at the station: `state` is the dashboard's /api/state
        when `reachable`, `feed` the EMWIN source. Returns the lines to post
        now, the transitions and the rate limits applied."""
        lines: list[str] = []
        self._lock(now, state if reachable else None, lines)
        self._backup(now, feed, lines)
        self._lag(now, feed, lines)
        self._dashboard(now, reachable, lines)
        self._host(now, state if reachable else None, lines)
        return lines

    async def tick(self, now: float | None, state: dict | None, reachable: bool, feed=None) -> list[str]:
        """The poll's hook: decide, then post. Returns what went out."""
        now = now or time.time()
        prefix = f"{self._name() or 'WX'} status: "
        lines: list[str] = []
        for text in self.check(now, state, reachable, feed):
            if lines and len(lines[-1]) + 2 + len(text) <= MAX_LEN:
                lines[-1] += "; " + text          # one packet for what changed together
            else:
                lines.append((prefix + text)[:MAX_LEN])
        out: list[str] = []
        for line in lines:
            if not settings.tx_enabled:
                logger.info("status (transmit off, not sent): %s", line)
                continue
            try:
                sent = await self._send(line)
            except Exception:
                logger.exception("status line not sent: %s", line)
                sent = False
            if sent:
                logger.info("status: %s", line)
                self.recent.append((now, line))
                out.append(line)
            else:
                logger.warning("status line not sent (no status channel on the node?): %s", line)
        return out

    def _say(self, key: str, text: str, now: float, lines: list[str]) -> bool:
        while self._hour and now - self._hour[0] >= 3600:
            self._hour.popleft()
        last = self._posted_at.get(key)
        if (last is not None and now - last < REPEAT_S) or len(self._hour) >= PER_HOUR:
            return False
        self._posted_at[key] = now
        self._hour.append(now)
        lines.append(text)
        return True

    def _lock(self, now: float, state: dict | None, lines: list[str]) -> None:
        st = (state or {}).get("stats")
        if (state or {}).get("mode") == "point":
            self._unlocked_since = None   # goesrecv is stopped on purpose; the clock starts again after
            return
        if not st:                        # no reading: no evidence either way
            return
        if st.get("locked"):
            if self._lock_said:
                self._say("lock_ok", "GOES locked again", now, lines)
            self._lock_said, self._unlocked_since = False, None
            return
        if self._unlocked_since is None:
            self._unlocked_since = now
        if not self._lock_said and now - self._unlocked_since >= LOCK_LOST_AFTER_S:
            self._lock_said = self._say("lock", f"GOES lock lost {_m(now - self._unlocked_since)}", now, lines)

    def _backup(self, now: float, feed, lines: list[str]) -> None:
        on = bool(getattr(feed, "fallback_active", False))
        was, self._backup_on = self._backup_on, on
        if was is None or on == was:
            return
        if on:
            quiet = getattr(feed, "dish_quiet_min", None)
            q = quiet(now) if callable(quiet) else None
            how = "no file from the dish yet" if q is None or q == float("inf") else f"dish quiet {q:.0f}m"
            self._say("backup_on", f"internet backup on ({how})", now, lines)
        else:
            added = getattr(feed, "fallback_added", 0)
            self._say("backup_off", f"internet backup off, dish is back ({added} products came by internet)",
                      now, lines)

    def _lag(self, now: float, feed, lines: list[str]) -> None:
        lag = getattr(feed, "feed_lag_min", None)
        if lag is None:
            return
        if not self._lag_said and lag >= LAG_ON_MIN:
            self._lag_said = self._say("lag", f"products ~{_behind(lag)} behind the uplink", now, lines)
        elif self._lag_said and lag < LAG_OFF_MIN:
            self._say("lag_ok", f"products current again (~{lag}m behind)", now, lines)
            self._lag_said = False

    def _dashboard(self, now: float, reachable: bool, lines: list[str]) -> None:
        if reachable:
            if self._down_said:
                self._say("dash_ok", "receiver dashboard reachable again", now, lines)
            self._down_said, self._down_since = False, None
            return
        if self._down_since is None:
            self._down_since = now
        if not self._down_said and now - self._down_since >= DASHBOARD_DOWN_AFTER_S:
            self._down_said = self._say("dash", f"receiver dashboard unreachable {_m(now - self._down_since)}",
                                        now, lines)

    def _host(self, now: float, state: dict | None, lines: list[str]) -> None:
        status = (state or {}).get("status")
        if not status:
            return
        disk, temp, thr = status.get("disk_used_pct"), status.get("temp"), status.get("throttled")
        bad: list[str] = []
        if disk is not None and disk >= DISK_PCT:
            bad.append(f"disk {disk:.0f}% full")
        if temp is not None and temp >= TEMP_C:
            bad.append(f"CPU {temp:.0f}C")
        if _throttled(thr):
            bad.append(f"throttled {thr}")
        if bad and not self._host_said:
            self._host_said = self._say("host", "Pi: " + ", ".join(bad), now, lines)
        elif self._host_said and not bad and (disk is None or disk < DISK_OK_PCT) \
                and (temp is None or temp < TEMP_OK_C):
            now_is = ", ".join(p for p in (f"disk {disk:.0f}%" if disk is not None else "",
                                           f"CPU {temp:.0f}C" if temp is not None else "") if p)
            self._say("host_ok", f"Pi back to normal ({now_is})" if now_is else "Pi back to normal", now, lines)
            self._host_said = False
