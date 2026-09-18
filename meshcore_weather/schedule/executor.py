"""Job → v5 messages.

The scheduler decides when a job is due; this module decides what it
sends. State that must survive between runs (which warnings were sent
with which fingerprint, pending life-safety repeats) lives in the
ExecutorContext the scheduler owns and persists.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from meshcore_weather.geodata import resolver
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5_builders as b
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.warnings import extract_active_warnings
from meshcore_weather.schedule.models import BroadcastJob

logger = logging.getLogger(__name__)

REPEAT_AFTER_S = 90          # life-safety warnings go out once more after this


@dataclass
class ExecutorContext:
    store: WeatherStore
    coverage: Coverage
    seq: b.SeqCounter              # provisional numbers: Scheduler.transmit stamps the seq on air
    bot: int
    # identity string -> {"fp": [...], "expires": min, "event": code, "sent_at": t, "repeat_at": t|None}
    warning_state: dict[str, dict] = field(default_factory=dict)
    home: tuple[float, float] | None = None
    radius_km: float = 0.0
    home_offices: set[str] = field(default_factory=set)
    # set by the warnings builder when a cancel went out: the scheduler
    # sends a digest a minute later
    cancel_sent: bool = False


def _fp_list(w: dict) -> list:
    return [list(x) if isinstance(x, tuple) else x for x in b.warning_fingerprint(w)]


def _build_warnings(job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
    """New and materially changed warnings, cancels for the ones that
    ended early, and the one repeat life-safety warnings get."""
    now = time.time()
    active = [w for w in extract_active_warnings(ctx.store, coverage=ctx.coverage)
              if b.warning_identity(w) is not None]
    out: list[bytes] = []
    seen: set[str] = set()
    for w in active:
        ident = b.warning_identity(w)
        key = b.identity_str(ident)
        seen.add(key)
        fp = _fp_list(w)
        st = ctx.warning_state.get(key)
        if st is not None and st.get("fp") == fp:
            if st.get("repeat_at") and now >= st["repeat_at"]:
                msg = b.warning_message(ctx.seq.next(), ctx.bot, w, update=False)
                if msg:
                    out.append(msg)
                    logger.info("Warning %s: life-safety repeat", key)
                st["repeat_at"] = None
            continue
        msg = b.warning_message(ctx.seq.next(), ctx.bot, w, update=st is not None)
        if msg is None:
            continue
        out.append(msg)
        code = w.get("vtec_phenomenon", "") + "." + (w.get("vtec_significance") or "")
        ctx.warning_state[key] = {
            "fp": fp, "expires": b.expires_min(w), "event": ident[0], "office": ident[1], "etn": ident[2],
            "sent_at": now, "repeat_at": now + REPEAT_AFTER_S if (code in b.LIFE_SAFETY and st is None) else None,
        }
        logger.info("Warning %s: %s (%d B)", key, "update" if st is not None else "new", len(msg))
    # Ended early: still had time left but is no longer active.
    for key in list(ctx.warning_state):
        if key in seen:
            continue
        st = ctx.warning_state.pop(key)
        if st.get("expires", 0) - b.now_min() > 5:
            out.append(b.cancel_message(ctx.seq.next(), ctx.bot, (st["event"], st["office"], st["etn"])))
            ctx.cancel_sent = True
            logger.info("Warning %s: cancelled before expiry", key)
    return out


def _build_digest(job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
    active = extract_active_warnings(ctx.store, coverage=ctx.coverage)
    health = b.feed_health(ctx.store, ctx.home_offices)
    # Aggregated from many products, so it states the store-wide source
    # (spec 2.2.1). Warnings, observations and forecasts take theirs from the
    # products they were built from, inside the builders.
    return [b.digest_message(ctx.seq.next(), ctx.bot, active, health,
                             source=ctx.store.products_source())]


def _build_observations(job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
    if job.location_type == "station":
        stations = [job.location_id.strip().upper()]
    else:
        stations = b.coverage_stations(ctx.home, ctx.radius_km, ctx.store)
    if not stations:
        return []
    msg = b.obs_message(ctx.seq.next(), ctx.bot, ctx.store, stations)
    return [msg] if msg else []


def _build_forecast(job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
    lat = lon = point = None
    if job.location_type == "pfm_point":
        b.tables.load()
        try:
            point = int(job.location_id)
            p = b.tables.points[point]
            lat, lon = p[2], p[3]
        except (ValueError, IndexError):
            return []
    elif job.location_type == "city" and job.location_id.strip():
        loc = resolver.resolve(job.location_id.strip())
        if not loc:
            return []
        lat, lon = loc.get("lat"), loc.get("lon")
    elif ctx.home is not None:
        lat, lon = ctx.home
    if lat is None or lon is None:
        return []
    msg = b.forecast_message(ctx.seq.next(), ctx.bot, ctx.store, float(lat), float(lon), point=point)
    return [msg] if msg else []


def _build_coverage(job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
    """One packet stating what this bot carries. Broadcast so an app never has
    to guess the bot's area from the stations and warnings it happens to have
    heard: that guess told a real phone WX-AUS might not carry alerts for its
    own home county (spec 7A)."""
    msg = b.coverage_message(ctx.seq.next(), ctx.bot, ctx.coverage, ctx.home, ctx.radius_km)
    return [msg] if msg else []


PRODUCT_BUILDERS = {
    "warnings": _build_warnings,
    "digest": _build_digest,
    "observations": _build_observations,
    "forecast": _build_forecast,
    "coverage": _build_coverage,
}


class BroadcastExecutor:
    def run_job(self, job: BroadcastJob, ctx: ExecutorContext) -> list[bytes]:
        builder = PRODUCT_BUILDERS.get(job.product)
        if builder is None:
            logger.warning("job %s: unknown product %r", job.id, job.product)
            return []
        return builder(job, ctx)
