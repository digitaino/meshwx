"""Broadcast scheduler: when each job runs, and the one place that puts
v5 messages on the data channel (the app responder sends through it too,
so the sequence counter and the spacing are shared).

Each tick: reload the config if the portal changed it, run the jobs that
are due, send what they built with a gap between packets, keep per-job
stats. Warning state (what was sent, with which fingerprint) and the next
header seq are persisted so a restart neither re-sends every active warning
as new nor jumps the sequence apps track.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from meshcore_weather.activity import EventDir, activity_log
from meshcore_weather.config import settings
from meshcore_weather.geodata import resolver
from meshcore_weather.meshcore.radio import MeshcoreRadio
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.schedule.executor import BroadcastExecutor, ExecutorContext
from meshcore_weather.schedule.models import BroadcastConfig
from meshcore_weather.schedule.store import CONFIG_PATH, load_config, save_config

logger = logging.getLogger(__name__)

TX_SPACING = 2.0                 # seconds between consecutive packets
TICK_INTERVAL_SECONDS = 30
FIRST_TICK_DELAY_S = 15          # settle before the first tick (after the backlog, see _tick_loop)
DIGEST_AFTER_CANCEL_S = 60

_STATE_PATH = Path(settings.data_dir) / "warning_state.json"


class Scheduler:
    def __init__(self, store: WeatherStore, radio: MeshcoreRadio, ready: asyncio.Event | None = None):
        self.store = store
        self.radio = radio
        # Set when the bot's product backlog is in; None when there is nothing
        # to wait for (a test, the CLI). The tick loop holds until then.
        self._ready = ready
        self.executor = BroadcastExecutor()
        self._config: BroadcastConfig = BroadcastConfig()
        self._coverage: Coverage = Coverage.empty()
        self._config_mtime: float = 0.0
        self._config_lock = asyncio.Lock()
        self._last_run: dict[str, float] = {}
        self._last_bytes: dict[str, int] = {}
        self._total_bytes: dict[str, int] = {}
        self._total_runs: dict[str, int] = {}
        self._last_msg_count: dict[str, int] = {}
        # The seq the next packet on air gets. Restored from the state file;
        # the clock only seeds a bot that has never saved one.
        self._next_seq = int(time.time()) & 0xFF
        self._tx_lock = asyncio.Lock()
        self._last_tx = 0.0
        self._warning_state: dict[str, dict] = {}
        self._digest_due_at: float | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    # -- lifecycle --

    async def start(self) -> None:
        self.reload_coverage()
        self._load_state()
        await self._reload_config()
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("Broadcast scheduler started: %d jobs, tick every %ds", len(self._config.jobs), TICK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._save_state()

    # -- coverage and identity --

    def reload_coverage(self) -> None:
        try:
            self._coverage = Coverage.from_config()
        except Exception:
            logger.exception("Coverage could not be built; broadcasting everything")
            self._coverage = Coverage.empty()

    @property
    def coverage(self) -> Coverage:
        return self._coverage

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def bot_id(self) -> int:
        mc = getattr(self.radio, "_mc", None)
        key = (getattr(mc, "self_info", None) or {}).get("public_key") if mc else None
        return b.bot_id(key)

    def context(self) -> ExecutorContext:
        home = None
        try:
            home = resolver.home()
        except Exception:
            pass
        offices = set(self._coverage.sources.get("wfos") or []) if self._coverage else set()
        if not offices and home is not None:
            try:
                loc = resolver.resolve_by_coords(home[0], home[1]) or {}
                offices = set(loc.get("wfos") or [])
            except Exception:
                pass
        # A scratch counter: builders number their messages, transmit restamps them.
        return ExecutorContext(store=self.store, coverage=self._coverage, seq=b.SeqCounter(), bot=self.bot_id(),
                               warning_state=self._warning_state, home=home,
                               radius_km=float(settings.home_radius_km or 0), home_offices=offices)

    # -- warning state and seq persistence --

    def _load_state(self) -> None:
        try:
            if _STATE_PATH.exists():
                d = json.loads(_STATE_PATH.read_text())
                if isinstance(d.get("next_seq"), int):          # absent in files written before revision 3
                    self._next_seq = d["next_seq"] & 0xFF
                self._warning_state = {k: v for k, v in d.get("warnings", {}).items()
                                       if v.get("expires", 0) > b.now_min()}
                logger.info("Warning state: %d active identities restored, next seq %d",
                            len(self._warning_state), self._next_seq)
        except Exception as e:
            logger.warning("Ignoring warning state file: %s", e)
            self._warning_state = {}

    def _save_state(self, next_seq: int | None = None) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE_PATH.with_suffix(".tmp")
            seq = self._next_seq if next_seq is None else next_seq
            tmp.write_text(json.dumps({"warnings": self._warning_state, "next_seq": seq}))
            tmp.replace(_STATE_PATH)
        except Exception as e:
            logger.warning("Could not write warning state: %s", e)

    # -- config --

    async def _reload_config(self) -> None:
        async with self._config_lock:
            try:
                mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0
            except OSError:
                mtime = 0.0
            if mtime != self._config_mtime or not self._config.jobs:
                self._config = load_config()
                self._config_mtime = mtime

    def current_config(self) -> BroadcastConfig:
        return self._config

    async def save_config(self, cfg: BroadcastConfig) -> None:
        async with self._config_lock:
            save_config(cfg)
            self._config = cfg
            try:
                self._config_mtime = CONFIG_PATH.stat().st_mtime
            except OSError:
                pass

    # -- the loop --

    async def _tick_loop(self) -> None:
        # Nothing goes out before the bot's products are in. A digest,
        # observations or a warning list built from the empty store of a bot
        # that restarted 20 seconds ago is wrong on air, and every app that
        # hears it files it as current.
        if self._ready is not None and not self._ready.is_set():
            logger.info("Broadcasts held until the product backlog is loaded")
            await self._ready.wait()
        await asyncio.sleep(FIRST_TICK_DELAY_S)
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler tick crashed")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def tick(self) -> int:
        """Run every enabled job whose interval has elapsed. Returns the
        number of packets sent."""
        await self._reload_config()
        ctx = self.context()
        now = time.time()
        sent = 0
        for job in list(self._config.jobs):
            if not job.enabled:
                continue
            if now - self._last_run.get(job.id, 0.0) < job.interval_minutes * 60:
                continue
            sent += await self._run(job, ctx)
        if ctx.cancel_sent:
            self._digest_due_at = time.time() + DIGEST_AFTER_CANCEL_S
        if self._digest_due_at and time.time() >= self._digest_due_at:
            self._digest_due_at = None
            digest = self._config.get_job("digest")
            if digest is not None:
                sent += await self._run(digest, ctx)
        if ctx.warning_state is not self._warning_state:
            self._warning_state = ctx.warning_state
        self._save_state()
        return sent

    async def _run(self, job, ctx: ExecutorContext) -> int:
        try:
            msgs = self.executor.run_job(job, ctx)
        except Exception:
            logger.exception("Scheduler: exception running job %s", job.id)
            msgs = []
        now = time.time()
        self._last_run[job.id] = now
        self._total_runs[job.id] = self._total_runs.get(job.id, 0) + 1
        if not msgs:
            self._last_msg_count[job.id] = 0
            self._last_bytes[job.id] = 0
            logger.debug("job %s: nothing to send", job.id)
            return 0
        n, nbytes = await self.transmit(msgs, f"job {job.id} ({job.product})")
        self._last_msg_count[job.id] = n
        self._last_bytes[job.id] = nbytes
        self._total_bytes[job.id] = self._total_bytes.get(job.id, 0) + nbytes
        activity_log.record(EventDir.OUT, "broadcast",
                            f"Job {job.id}: {n} msg(s), {nbytes}B ({job.product})",
                            {"job_id": job.id, "product": job.product, "messages": n, "bytes": nbytes})
        return n

    async def transmit(self, msgs: list[bytes], label: str, ev: dict | None = None) -> tuple[int, int]:
        """Send v5 messages on the data channel with spacing. Returns
        (packets sent, bytes).

        One batch at a time, so a request answer and a scheduled tick never
        interleave and packets leave in seq order. The seq is stamped here,
        not by the builders, and a number is used only when the radio took
        the packet: a failed send leaves no gap. The radio's echo resend
        repeats these stamped bytes, same seq."""
        async with self._tx_lock:
            # Saved past the batch before it starts: killed part-way, the bot comes back ahead of
            # every number it may have put on air (apps see a gap), never behind them (apps would
            # take the next packets for late copies). The save after the batch keeps the real value.
            if msgs:
                self._save_state(next_seq=(self._next_seq + len(msgs)) & 0xFF)
            sent = nbytes = 0
            groups: dict[int, int] = {}
            for msg in msgs:
                wait = self._last_tx + TX_SPACING - time.monotonic()
                if wait > 0:                        # spacing holds across batches too
                    await asyncio.sleep(wait)
                msg = self._stamp(msg, groups)
                try:
                    ok = await self.radio.send_channel_data(msg, ev=ev)
                except Exception:
                    logger.exception("%s: send failed", label)
                    ok = False
                self._last_tx = time.monotonic()
                if ok:
                    self._next_seq = (self._next_seq + 1) & 0xFF
                    sent += 1
                    nbytes += len(msg)
            if msgs:
                self._save_state()
            if sent:
                activity_log.record_send(sent, nbytes)
                logger.info("%s: %d packet(s), %d bytes", label, sent, nbytes)
            return sent, nbytes

    def _stamp(self, msg: bytes, groups: dict[int, int]) -> bytes:
        """Byte 0 = the next seq. A text reply's group is the seq its first
        chunk goes out with (spec 8.1), so it follows the restamp."""
        out = bytearray(msg)
        out[0] = self._next_seq
        if len(out) >= 8 and out[3] >> 4 == v5.TYPE_TEXT:
            if out[6] == 0:
                groups[msg[5]] = self._next_seq
            out[5] = groups.get(msg[5], msg[5])
        return bytes(out)

    # -- portal --

    def job_status(self, job_id: str) -> dict:
        now = time.time()
        job = self._config.get_job(job_id)
        if job is None:
            return {"job_id": job_id, "found": False}
        last_run = self._last_run.get(job_id, 0.0)
        next_due = last_run + job.interval_minutes * 60 if last_run else now
        return {
            "job_id": job_id, "found": True, "enabled": job.enabled,
            "last_run_unix": last_run if last_run else None,
            "last_run_seconds_ago": int(now - last_run) if last_run else None,
            "next_run_unix": next_due,
            "next_run_in_seconds": max(0, int(next_due - now)) if last_run else 0,
            "total_runs": self._total_runs.get(job_id, 0),
            "total_bytes": self._total_bytes.get(job_id, 0),
            "last_bytes": self._last_bytes.get(job_id, 0),
            "last_msg_count": self._last_msg_count.get(job_id, 0),
        }

    async def run_job_now(self, job_id: str) -> int:
        job = self._config.get_job(job_id)
        if job is None:
            return 0
        ctx = self.context()
        n = await self._run(job, ctx)
        self._save_state()
        return n
