"""Turn the goestools dashboard's numbers into log lines for the console.

Polls the dashboard every few seconds and logs (under the "satellite"
category) only what an operator would want to see scroll by: lock gained or
lost, a per-minute summary whenever packets were dropped, and goesrecv /
goesproc service changes. Never raises; a dashboard that is down is logged
once and retried quietly.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from meshcore_weather.config import settings

logger = logging.getLogger("meshcore_weather.sdr")

POLL_S = 10


class SdrMonitor:
    def __init__(self, url: str | None = None):
        self.url = (url or settings.sdr_dashboard_url).rstrip("/")
        self._task: asyncio.Task | None = None
        self._locked: bool | None = None
        self._services: dict = {}
        self._reachable: bool | None = None
        self._minute_drops = 0
        self._minute_vit: list[int] = []
        self._minute_start: float | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    r = await client.get(self.url + "/api/state")
                    r.raise_for_status()
                    self.observe(r.json())
                    if self._reachable is False:
                        logger.info("receiver dashboard reachable again at %s", self.url)
                    self._reachable = True
                except Exception as e:
                    if self._reachable is not False:
                        logger.warning("receiver dashboard unreachable (%s): %s", self.url, e)
                    self._reachable = False
                await asyncio.sleep(POLL_S)

    def observe(self, state: dict, now: float | None = None) -> None:
        """Feed one dashboard sample; emits log lines on changes."""
        now = now or time.time()
        if self._minute_start is None:
            self._minute_start = now
        st = state.get("stats") or {}
        locked = bool(st.get("locked"))
        if self._locked is None:
            logger.info("receiver %s (vit %s, gain %.1f, mode %s)",
                        "locked" if locked else "NOT locked", st.get("vit_avg"), float(st.get("gain") or 0), state.get("mode"))
        elif locked != self._locked:
            (logger.info if locked else logger.warning)("receiver %s (vit %s)", "locked" if locked else "LOST LOCK", st.get("vit_avg"))
        self._locked = locked

        services = (state.get("status") or {}).get("services") or {}
        for name, val in services.items():
            old = self._services.get(name)
            if old is not None and old != val:
                (logger.warning if val != "active" else logger.info)("%s is %s (was %s)", name, val, old)
        self._services = dict(services)

        self._minute_drops += int(st.get("drops") or 0)
        if st.get("vit_avg") is not None:
            self._minute_vit.append(int(st["vit_avg"]))
        if now - self._minute_start >= 60:
            if self._minute_drops:
                vit = sum(self._minute_vit) // max(1, len(self._minute_vit))
                logger.warning("receiver dropped %d packets in the last minute (vit avg %d)", self._minute_drops, vit)
            self._minute_drops = 0
            self._minute_vit = []
            self._minute_start = now
