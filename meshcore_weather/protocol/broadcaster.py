"""The app side of the bot: scheduled v5 broadcasts (owned Scheduler) and
answers to `>` requests.

A request is a DM (or a channel line) that starts with `>` and uses the
same words a person would: `>w`, `>w SV.W.EWX.42`, `>o KAUS`, `>f 102`,
`>afd EWX`. The answer is never a DM: it goes out on the data channel as
v5 messages so every app in range gets it (docs/MeshWX_v5_Spec.md 8.2).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from meshcore_weather.geodata import resolver
from meshcore_weather.meshcore.radio import MeshcoreRadio
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.warnings import extract_active_warnings

logger = logging.getLogger(__name__)

PER_SENDER_S = 5.0
PER_HOUR = 60                    # packets, not requests: a `>w` answer can be 7
MAX_WARNINGS_PER_REQUEST = 6


class AppResponder:
    def __init__(self, store: WeatherStore, radio: MeshcoreRadio,
                 render_text: Callable[[str, str], str | None] | None = None):
        from meshcore_weather.schedule.scheduler import Scheduler
        self.store = store
        self.radio = radio
        self._scheduler = Scheduler(store=store, radio=radio)
        self._render_text = render_text          # the text bot's renderer, for narrative products
        self._last_by_sender: dict[str, float] = {}
        self._sent: deque[float] = deque(maxlen=PER_HOUR * 2)

    @property
    def scheduler(self):
        return self._scheduler

    @property
    def coverage(self) -> Coverage:
        return self._scheduler.coverage

    def reload_coverage(self) -> None:
        self._scheduler.reload_coverage()

    async def start(self) -> None:
        await self._scheduler.start()

    async def stop(self) -> None:
        await self._scheduler.stop()

    # -- requests --

    @staticmethod
    def is_request(text: str) -> bool:
        return text.strip().startswith(">")

    async def handle_request(self, text: str, sender_key: str) -> str:
        """Answer one `>` request. Returns a short outcome for the log."""
        now = time.time()
        if now - self._last_by_sender.get(sender_key, 0.0) < PER_SENDER_S:
            return "rate limited"
        while self._sent and now - self._sent[0] > 3600:
            self._sent.popleft()
        self._last_by_sender[sender_key] = now
        # Before building: an answer that will not be sent must cost nothing.
        if len(self._sent) >= PER_HOUR:
            logger.warning("App request budget spent this hour; ignoring %r", text)
            return "hourly budget spent"
        parts = text.strip()[1:].split(None, 1)
        cmd = (parts[0] if parts else "").lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        # Scratch numbering: Scheduler.transmit stamps the real seq on air.
        seq, bot = b.SeqCounter(), self._scheduler.bot_id()
        try:
            msgs = self._answer(cmd, arg, seq, bot)
        except Exception:
            logger.exception("App request %r failed", text)
            msgs = [b.not_available(seq.next(), bot, cmd, v5.REASON_BOT_ERROR)]
        if not msgs:
            msgs = [b.not_available(seq.next(), bot, cmd, v5.REASON_NO_DATA)]
        n, nbytes = await self._scheduler.transmit(msgs, f"app request {text.strip()[:24]!r}")
        self._sent.extend([now] * n)
        return f"{n} packet(s), {nbytes} B"

    def _answer(self, cmd: str, arg: str, seq: b.SeqCounter, bot: int) -> list[bytes]:
        ctx = self._scheduler.context()
        if cmd == "d":
            active = extract_active_warnings(self.store, coverage=self.coverage)
            return [b.digest_message(seq.next(), bot, active, b.feed_health(self.store, ctx.home_offices))]

        if cmd == "w":
            active = extract_active_warnings(self.store, coverage=None if arg else self.coverage)
            active = [w for w in active if b.warning_identity(w) is not None]
            if arg:
                ident = b.parse_identity(arg)
                if ident is not None:
                    active = [w for w in active if b.warning_identity(w) == ident]
                else:
                    ugc = arg.upper()
                    if len(ugc) != 6:
                        return [b.not_available(seq.next(), bot, "w", v5.REASON_UNKNOWN_LOCATION)]
                    active = [w for w in active if ugc in (w.get("ugcs") or [])]
                if not active:
                    return [b.not_available(seq.next(), bot, "w", v5.REASON_NO_DATA)]
            active.sort(key=lambda w: w.get("onset_unix_min") or 0, reverse=True)
            out = [m for w in active[:MAX_WARNINGS_PER_REQUEST]
                   if (m := b.warning_message(seq.next(), bot, w, update=True))]
            if not arg:
                out.append(b.digest_message(seq.next(), bot, active, b.feed_health(self.store, ctx.home_offices)))
            return out

        if cmd == "wt":
            ident = b.parse_identity(arg)
            if ident is None:
                return [b.not_available(seq.next(), bot, "w", v5.REASON_UNKNOWN_LOCATION)]
            for w in extract_active_warnings(self.store, coverage=None):
                if b.warning_identity(w) == ident:
                    text = " ".join(x for x in (w.get("headline"), w.get("description")) if x)
                    return b.text_messages(seq, bot, v5.SUBJECT_WARNING, text or "No narrative available.")
            return [b.not_available(seq.next(), bot, "w", v5.REASON_NO_DATA)]

        if cmd == "o":
            if arg:
                stations = [arg.upper()]
                if b.tables.station(stations[0]) is None:
                    return [b.not_available(seq.next(), bot, "o", v5.REASON_UNKNOWN_LOCATION)]
            else:
                stations = b.coverage_stations(ctx.home, ctx.radius_km, self.store)
            msg = b.obs_message(seq.next(), bot, self.store, stations) if stations else None
            return [msg] if msg else []

        if cmd == "f":
            lat = lon = point = None
            if not arg and ctx.home is not None:
                lat, lon = ctx.home
            elif arg.isdigit() and len(arg) <= 4:      # a point index; 5 digits is a ZIP
                b.tables.load()
                try:
                    point = int(arg)
                    p = b.tables.points[point]
                    lat, lon = p[2], p[3]
                except (IndexError, ValueError):
                    return [b.not_available(seq.next(), bot, "f", v5.REASON_UNKNOWN_LOCATION)]
            elif arg:
                loc = resolver.resolve(arg)
                if not loc:
                    return [b.not_available(seq.next(), bot, "f", v5.REASON_UNKNOWN_LOCATION)]
                lat, lon = loc.get("lat"), loc.get("lon")
            if lat is None:
                return [b.not_available(seq.next(), bot, "f", v5.REASON_UNKNOWN_LOCATION)]
            msg = b.forecast_message(seq.next(), bot, self.store, float(lat), float(lon), point=point)
            return [msg] if msg else []

        if cmd == "afd":
            wfo = (arg or "").upper() or next(iter(ctx.home_offices), "")
            prod = None
            for p in sorted(self.store._products.values(), key=lambda p: p.timestamp, reverse=True):
                if p.product_type == "AFD" and p.office == wfo:
                    prod = p
                    break
            if prod is None:
                return [b.not_available(seq.next(), bot, "a", v5.REASON_NO_DATA)]
            return b.text_messages(seq, bot, v5.SUBJECT_AFD, _afd_body(prod.raw_text))

        if cmd in ("metar", "taf"):
            return self._station_report(cmd, arg, seq, bot, ctx.home)

        if cmd == "hwo":
            from meshcore_weather.core import services
            loc = resolver.resolve(arg) if arg else (resolver.resolve_by_coords(*ctx.home) if ctx.home else None)
            if not loc:
                return [b.not_available(seq.next(), bot, cmd, v5.REASON_UNKNOWN_LOCATION)]
            ol = services.outlook_for(self.store, loc)
            text = services.hwo_summary(ol.raw_text) if ol and getattr(ol, "raw_text", None) else None
            return b.text_messages(seq, bot, v5.SUBJECT_HWO, text) if text else []

        # `>sat` is always Text, "not reporting" included: that is the answer.
        if cmd in ("space", "storm", "rain", "sat"):
            subject = {"space": v5.SUBJECT_SPACE, "storm": v5.SUBJECT_STORM_REPORTS,
                       "rain": v5.SUBJECT_RAINFALL, "sat": v5.SUBJECT_GENERAL}[cmd]
            text = self._render_text(cmd, arg) if self._render_text else None
            return b.text_messages(seq, bot, subject, text) if text else []

        return [b.not_available(seq.next(), bot, cmd, v5.REASON_UNSUPPORTED)]

    def _station_report(self, cmd: str, arg: str, seq: b.SeqCounter, bot: int,
                        home: tuple[float, float] | None) -> list[bytes]:
        """`>metar` / `>taf`. A request that names a station gets that
        station's own report, starting `METAR <ICAO>` / `TAF <ICAO>`, or Not
        available: the app files the text under the ICAO it asked for, so a
        neighbour's report would land on the wrong airport. A place or ZIP gets
        the nearest reporting station, labelled with its ICAO and distance."""
        from meshcore_weather.core import render_text, services
        icao = arg.upper()
        resolver.load()
        if len(icao) == 4 and icao.isalnum() and (b.tables.station(icao) is not None or icao in resolver._stations):
            if cmd == "metar":
                raw = self.store._find_metar_raw(icao)
                fresh = raw and datetime.now(timezone.utc) - raw[1] <= timedelta(minutes=b.OBS_MAX_AGE_MIN)
                text = f"METAR {raw[0]}" if fresh else None
            else:
                text = services.station_taf(self.store, icao)
            if not text:
                return [b.not_available(seq.next(), bot, cmd, v5.REASON_NO_DATA)]
            return b.text_messages(seq, bot, v5.SUBJECT_METAR, text)
        loc = resolver.resolve(arg) if arg else (resolver.resolve_by_coords(*home) if home else None)
        if not loc:
            return [b.not_available(seq.next(), bot, cmd, v5.REASON_UNKNOWN_LOCATION)]
        if cmd == "metar":
            found = services.raw_metar_for(self.store, loc)
            text = render_text.raw_metar(loc, found) if found else None
        else:
            tf = services.taf_for(self.store, loc)
            text = render_text.taf(loc, tf) if tf else None
        return b.text_messages(seq, bot, v5.SUBJECT_METAR, text) if text else []


def _afd_body(raw: str, limit: int = 1200) -> str:
    """The forecast discussion without its product header, cut for the air."""
    lines = raw.splitlines()
    start = 0
    for i, line in enumerate(lines[:30]):
        if line.strip().startswith(".") and "DISCUSSION" not in line.upper():
            start = i
            break
    body = " ".join(l.strip() for l in lines[start:] if l.strip() and not l.strip().startswith("$$"))
    return body[:limit]
