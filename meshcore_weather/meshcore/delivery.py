"""Did the mesh hear us? Echo and ACK tracking with a retransmit budget.

A person who hears no repeat of their chat message sends it again; the bot
does the same, carefully.

How it knows:
- The companion firmware pushes every raw packet it hears (RX log), before
  its own duplicate check. When a repeater repeats our packet, the copy
  comes back with the repeater's hash in the path. Every MeshCore node
  identifies a packet by sha256(payload_type || payload)[:8], which ignores
  header and path, so the echo has the same hash as what we sent.
- A channel packet's bytes are reproducible: the node's channel secret,
  its name, the text and a timestamp we choose. So we know a reply's hash
  before it leaves, and an identical retransmit is dropped by every node
  that already has it and accepted by every node that missed it. Nobody
  sees the message twice.
- A DM has an end-to-end ACK from the recipient (the firmware tells us the
  ack code to expect and how long to wait).

The retransmit is skipped when transmit is off, when the hourly budget is
spent, when no repeater has been heard from anyone recently (nothing would
change), and, optionally, when a CoreScope instance says the packet was
observed. Every outcome is written back to the traffic event so the feed
and the counters can show it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from Crypto.Cipher import AES        # pycryptodome, a meshcore-py dependency

from meshcore_weather.config import settings

logger = logging.getLogger(__name__)

PAYLOAD_GRP_TXT = 5
PAYLOAD_GRP_DATA = 6
ROUTE_TRANSPORT = (0, 3)             # header route types that carry 4 transport bytes


# -- Packet bytes ----------------------------------------------------------------------


def packet_hash(payload_type: int, payload: bytes) -> str:
    """The hash every MeshCore node dedupes on (Packet::calculatePacketHash)."""
    return hashlib.sha256(bytes([payload_type]) + payload).digest()[:8].hex()


def channel_hash_byte(secret: bytes) -> int:
    return hashlib.sha256(secret).digest()[0]


def build_channel_payload(secret: bytes, name: str, text: str, ts: int, flags: int = 0) -> bytes:
    """What the firmware puts on air for a channel text message: channel hash
    byte, 2-byte HMAC-SHA256 of the ciphertext, AES-128-ECB of
    timestamp(4, LE) || flags || "name: text" zero-padded to 16 bytes."""
    plain = ts.to_bytes(4, "little") + bytes([flags]) + f"{name}: {text}".encode("utf-8")
    plain += b"\0" * ((-len(plain)) % 16)
    ct = AES.new(secret, AES.MODE_ECB).encrypt(plain)
    mac = hmac.new(secret, ct, hashlib.sha256).digest()[:2]
    return bytes([channel_hash_byte(secret)]) + mac + ct


def build_channel_data_payload(secret: bytes, data_type: int, data: bytes) -> bytes:
    """What the firmware puts on air for a channel GRP_DATA datagram: channel
    hash byte, 2-byte HMAC-SHA256 of the ciphertext, AES-128-ECB of
    data_type(2, LE) || len(1) || data zero-padded to 16 bytes."""
    plain = data_type.to_bytes(2, "little") + bytes([len(data)]) + bytes(data)
    plain += b"\0" * ((-len(plain)) % 16)
    ct = AES.new(secret, AES.MODE_ECB).encrypt(plain)
    mac = hmac.new(secret, ct, hashlib.sha256).digest()[:2]
    return bytes([channel_hash_byte(secret)]) + mac + ct


def parse_packet(raw: bytes) -> dict | None:
    """Split a raw packet into route, payload type, path and payload."""
    if len(raw) < 2:
        return None
    hdr = raw[0]
    route, ptype = hdr & 3, (hdr >> 2) & 0xF
    i = 1 + (4 if route in ROUTE_TRANSPORT else 0)
    if i >= len(raw):
        return None
    plen = raw[i]
    i += 1
    path = raw[i:i + plen]
    payload = raw[i + plen:]
    if len(path) != plen or not payload:
        return None
    return {"route": route, "ptype": ptype, "path_len": plen, "path": path.hex(), "payload": payload}


def fmt_path(path_hex: str) -> str:
    """'d03a' -> 'D0,3A': the repeater hashes that carried the packet."""
    return ",".join(path_hex[i:i + 2].upper() for i in range(0, len(path_hex), 2))


# -- Tracking ------------------------------------------------------------------------------


@dataclass
class Outbound:
    kind: str                                    # channel_text | dm
    hash: str | None                             # packet hash for echo matching (None: not matchable)
    resend: Callable[[int], Awaitable[object]]   # attempt -> False on failure; a str is a new ack code
    ev: dict | None = None                       # traffic event to write the outcome to
    ack: str | None = None                       # DM: ack code the firmware expects
    window_s: float = 5.0
    ptype: int = PAYLOAD_GRP_TXT
    give_up: Callable[[], Awaitable[None]] | None = None
    sent_at: float = field(default_factory=time.time)
    attempts: int = 1
    echoed_at: float | None = None
    via: str | None = None
    echo_snr: float | None = None
    acked_at: float | None = None
    skipped: str | None = None
    observed: dict | None = None                 # the SETTLED CoreScope reading, the only one fit to show
    probe: dict | None = None                    # what CoreScope said at resend-decision time, seconds in
    lag_given_s: float = 0.0                     # window extended by a blocked loop
    last_tx_at: float = 0.0                      # the transmission an echo is timed against
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if not self.last_tx_at:
            self.last_tx_at = self.sent_at

    @property
    def heard(self) -> bool:
        return self.echoed_at is not None or self.acked_at is not None


OUTCOMES_KEEP_S = 86400
#: How long after a send to ask CoreScope who heard it. Observers report
#: over several seconds, so an answer taken during the echo wait is far too
#: early to put on the record.
SCOPE_LATE_S = 45


class LoopLag:
    """How far behind the event loop is running its callbacks.

    The bot shares one thread with the EMWIN parser, the portal and the
    schedulers, so a long synchronous stretch delays the handler that
    matches a repeater's echo against what we sent. Counting that delay
    against the echo window makes a packet the mesh *did* repeat look
    unrepeated, and the bot floods the whole message a second time for
    nothing. This measures the delay so the window can give it back.
    """

    #: Lag is judged over this trailing window, never over the life of the
    #: process: one slow start-up must not read as a permanently sick loop.
    RECENT_S = 300.0

    def __init__(self, interval: float = 0.5, jitter: float = 0.05):
        self.interval = interval
        self.jitter = jitter          # ordinary scheduling noise, not a stall
        self.total = 0.0              # cumulative, monotonic: the echo window reads deltas off this
        self.worst = 0.0              # worst single stall since start
        self.last = 0.0
        self.since = time.time()
        self._recent: deque[tuple[float, float]] = deque()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.since = time.time()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(self.interval)
            late = time.monotonic() - t0 - self.interval
            if late > self.jitter:
                self.total += late
                self.last = late
                self.worst = max(self.worst, late)
                self._recent.append((time.time(), late))
            self._prune()

    def _prune(self) -> None:
        cut = time.time() - self.RECENT_S
        while self._recent and self._recent[0][0] < cut:
            self._recent.popleft()

    @property
    def pct(self) -> float:
        """Share of the trailing window the loop spent running late."""
        self._prune()
        if not self._recent:
            return 0.0
        span = min(self.RECENT_S, max(1.0, time.time() - self.since))
        return 100.0 * sum(late for _, late in self._recent) / span

    @property
    def recent_worst(self) -> float:
        self._prune()
        return max((late for _, late in self._recent), default=0.0)

    def stats(self) -> dict:
        return {"running": self._task is not None and not self._task.done(),
                "pct": round(self.pct, 2), "recent_worst_s": round(self.recent_worst, 2),
                "window_s": int(self.RECENT_S), "last_s": round(self.last, 2),
                "total_s": round(self.total, 1), "worst_s": round(self.worst, 2)}


loop_lag = LoopLag()


class DeliveryTracker:
    def __init__(self, persist_path: Path | None = None):
        self._by_hash: dict[str, Outbound] = {}
        self._by_ack: dict[str, Outbound] = {}
        self._tasks: set[asyncio.Task] = set()
        self._resends: deque[float] = deque(maxlen=1000)
        self._outcomes: deque[tuple] = deque(maxlen=5000)   # (t, kind, echoed, acked, resent, echo_ms)
        self.started_at = time.time()
        self.last_repeat_heard_at = 0.0
        self.last_rx_at = 0.0
        self.rx_frames = 0
        self.rx_repeats = 0
        # The outcome rows outlive a restart (the 24 h window would otherwise
        # start empty after every deploy); the singleton persists, tests don't.
        self._persist_path = persist_path
        self._load_outcomes()

    def _load_outcomes(self) -> None:
        if not self._persist_path:
            return
        try:
            if self._persist_path.exists():
                cut = time.time() - OUTCOMES_KEEP_S
                rows = json.loads(self._persist_path.read_text()).get("outcomes", [])
                self._outcomes.extend(tuple(r) for r in rows if isinstance(r, list) and len(r) == 6 and r[0] >= cut)
        except Exception as e:
            logger.warning("Ignoring delivery outcome file: %s", e)

    def _save_outcomes(self) -> None:
        if not self._persist_path:
            return
        try:
            cut = time.time() - OUTCOMES_KEEP_S
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"outcomes": [list(r) for r in self._outcomes if r[0] >= cut]}))
            tmp.replace(self._persist_path)
        except Exception as e:
            logger.debug("Could not write delivery outcomes: %s", e)

    def recent_outcomes(self, n: int = 50) -> list[tuple]:
        """The newest n outcome rows, newest last."""
        rows = list(self._outcomes)
        return rows[-n:]

    # -- what the radio tells us --

    def on_rx_log(self, raw: bytes, snr: float | None = None) -> None:
        pkt = parse_packet(raw)
        self.rx_frames += 1
        self.last_rx_at = time.time()
        if pkt is None or pkt["path_len"] == 0:
            return
        now = time.time()
        self.rx_repeats += 1
        self.last_repeat_heard_at = now
        ob = self._by_hash.get(packet_hash(pkt["ptype"], pkt["payload"]))
        if ob is not None and ob.echoed_at is None:
            ob.echoed_at, ob.via, ob.echo_snr = now, pkt["path"], snr
            ob.done.set()

    def on_ack(self, code: str) -> None:
        ob = self._by_ack.get(code)
        if ob is not None and ob.acked_at is None:
            ob.acked_at = time.time()
            ob.done.set()

    # -- tracking one send --

    def track(self, ob: Outbound) -> asyncio.Task:
        self._register(ob)
        task = asyncio.create_task(self._watch(ob))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _register(self, ob: Outbound) -> None:
        if ob.hash:
            self._by_hash[ob.hash] = ob
        if ob.ack:
            self._by_ack[ob.ack] = ob

    def _unregister(self, ob: Outbound) -> None:
        if ob.hash and self._by_hash.get(ob.hash) is ob:
            del self._by_hash[ob.hash]
        if ob.ack and self._by_ack.get(ob.ack) is ob:
            del self._by_ack[ob.ack]

    def _why_not_resend(self, ob: Outbound) -> str | None:
        now = time.time()
        if ob.attempts > settings.retransmit_max:
            return None if ob.attempts == 1 and settings.retransmit_max == 0 else "budget: max attempts"
        if not settings.tx_enabled:
            return "tx off"
        while self._resends and now - self._resends[0] > 3600:
            self._resends.popleft()
        if len(self._resends) >= settings.retransmit_per_hour:
            return "budget: hourly retransmits spent"
        quiet = settings.mesh_quiet_s
        if now - self.started_at > quiet and now - self.last_repeat_heard_at > quiet:
            return f"mesh quiet: no repeat heard in {int((now - self.last_repeat_heard_at) / 60)} min"
        return None

    async def _wait_for_echo(self, ob: Outbound) -> None:
        """Wait out the echo window, giving back any time the event loop
        spent blocked. A handler that ran late is not evidence that nobody
        repeated us, and acting on it costs a whole retransmission."""
        deadline = time.monotonic() + ob.window_s
        lag_at_start = loop_lag.total
        while not ob.heard:
            given = min(loop_lag.total - lag_at_start, ob.window_s)
            ob.lag_given_s = given
            remaining = deadline + given - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(ob.done.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def _watch(self, ob: Outbound) -> None:
        try:
            while True:
                await self._wait_for_echo(ob)
                if ob.heard:
                    break
                if ob.attempts > settings.retransmit_max:
                    break
                reason = self._why_not_resend(ob)
                if reason:
                    ob.skipped = reason
                    break
                if settings.scope_url and settings.scope_mode == "decide" and ob.hash:
                    # This runs seconds after the send, while observers are
                    # still reporting, so it is only ever good enough to veto
                    # a resend. It never becomes the observer count on the
                    # record: that comes from the settled read in _finish.
                    ob.probe = await scope_lookup(settings.scope_url, ob.hash, ob.ptype)
                    logger.info("CoreScope probe %s after %.1fs: %s", ob.hash, time.time() - ob.sent_at,
                                ob.probe or "no answer (packet not in the page, or the lookup failed)")
                    # Only a repeated copy proves a repeater carried it; an
                    # observer next door hearing us direct proves nothing.
                    if ob.probe and ob.probe["repeated_by"] >= settings.scope_min_observers:
                        ob.skipped = f"CoreScope: {ob.probe['repeated_by']} observers heard a repeat"
                        break
                await asyncio.sleep(random.uniform(0.5, 2.0))
                if ob.heard:
                    break
                ob.done.clear()
                self._resends.append(time.time())
                try:
                    res = await ob.resend(ob.attempts)
                except Exception as e:
                    logger.warning("Retransmit failed: %s", e)
                    res = False
                if res is not False:
                    ob.last_tx_at = time.time()
                ob.attempts += 1
                if res is False:
                    break
                if isinstance(res, str):
                    if ob.ack:
                        self._by_ack.pop(ob.ack, None)
                    ob.ack = res
                    self._by_ack[res] = ob
        finally:
            self._unregister(ob)
            await self._finish(ob)

    async def _finish(self, ob: Outbound) -> None:
        now = time.time()
        d = self.outcome(ob)
        self._outcomes.append((now, ob.kind, ob.echoed_at is not None, ob.acked_at is not None,
                               ob.attempts - 1, d.get("echo_ms")))
        self._save_outcomes()
        if ob.ev is not None:
            from meshcore_weather.traffic import traffic_log
            traffic_log.update(ob.ev, delivery=d, push=True)
        logger.info("Delivery %s: %s", ob.kind, d["result"] + (f" via {fmt_path(ob.via)}" if ob.via else "") +
                    (f" ({d['echo_ms']} ms)" if d.get("echo_ms") is not None else "") +
                    (f", {ob.attempts - 1} retransmit, {d['echo_total_ms']} ms from the first send"
                     if ob.attempts > 1 and d.get("echo_total_ms") is not None else
                     f", {ob.attempts - 1} retransmit" if ob.attempts > 1 else "") +
                    (f", waited {ob.lag_given_s:.1f}s longer for a stalled loop" if ob.lag_given_s else ""))
        if not ob.heard and ob.give_up is not None:
            try:
                await ob.give_up()
            except Exception:
                logger.exception("give-up hook failed")
        # Always take the late reading, even when the decide-mode lookup
        # during the wait already filled `observed`: that one ran seconds
        # after the send, before the observers had reported, and leaving it
        # on the record makes a well-repeated packet read "direct only: 1".
        if settings.scope_url and ob.hash and settings.scope_mode in ("stats", "decide"):
            t = asyncio.create_task(self._annotate_from_scope(ob))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _annotate_from_scope(self, ob: Outbound) -> None:
        """A minute later, ask CoreScope who heard it (statistics only).
        A thinner answer never replaces a fuller one: observers report over
        several seconds, so later is normally better, but a lookup that
        half-failed must not erase what we already knew."""
        await asyncio.sleep(SCOPE_LATE_S)
        late = await scope_lookup(settings.scope_url, ob.hash, ob.ptype)
        if not late:
            return
        if late.get("observers", 0) < (ob.observed or {}).get("observers", 0):
            return
        ob.observed = late
        if ob.ev is not None:
            from meshcore_weather.traffic import traffic_log
            traffic_log.update(ob.ev, delivery=self.outcome(ob), push=True)

    @staticmethod
    def outcome(ob: Outbound) -> dict:
        # Timed against the transmission that was echoed, not against the
        # first one: a resent packet is byte-identical, so measuring from the
        # original reported the echo window and the back-off as if they were
        # mesh latency (10.8 s for a mesh that answered in under a second).
        echo_ms = int((ob.echoed_at - ob.last_tx_at) * 1000) if ob.echoed_at else None
        rtt_ms = int((ob.acked_at - ob.last_tx_at) * 1000) if ob.acked_at else None
        echo_total_ms = int((ob.echoed_at - ob.sent_at) * 1000) if ob.echoed_at else None
        rtt_total_ms = int((ob.acked_at - ob.sent_at) * 1000) if ob.acked_at else None
        if ob.acked_at:
            result = "acked"
        elif ob.echoed_at:
            result = "echoed"
        elif ob.skipped:
            result = "skipped"
        else:
            result = "no_ack" if ob.kind == "dm" else "no_echo"
        return {"result": result, "echo": ob.echoed_at is not None, "echo_ms": echo_ms,
                "echo_total_ms": echo_total_ms, "rtt_total_ms": rtt_total_ms,
                "via": fmt_path(ob.via) if ob.via else None, "snr": ob.echo_snr,
                "acked": ob.acked_at is not None, "rtt_ms": rtt_ms,
                "attempts": ob.attempts, "resent": ob.attempts - 1, "skipped": ob.skipped,
                "lag_given_s": round(ob.lag_given_s, 2) or None,
                "observed_by": (ob.observed or {}).get("observers"),
                "observed_repeats": (ob.observed or {}).get("repeated_by"),
                "observed_paths": (ob.observed or {}).get("paths")}

    # -- for the portal --

    def stats(self) -> dict:
        now = time.time()
        out: dict = {"rx_frames": self.rx_frames, "rx_repeats": self.rx_repeats,
                     "last_repeat_heard_at": self.last_repeat_heard_at or None,
                     "last_rx_at": self.last_rx_at or None,
                     "pending": len(self._by_hash) + len(self._by_ack),
                     "loop_lag": loop_lag.stats(), "windows": {}}
        rows = list(self._outcomes)
        for label, secs in (("1h", 3600), ("24h", 86400)):
            cut = now - secs
            sel = [r for r in rows if r[0] >= cut]
            heard = [r for r in sel if r[2] or r[3]]
            delays = sorted(r[5] for r in sel if r[5] is not None)
            out["windows"][label] = {
                "sent": len(sel), "heard": len(heard), "echoed": sum(1 for r in sel if r[2]),
                "acked": sum(1 for r in sel if r[3]), "resent": sum(r[4] for r in sel),
                "heard_pct": int(100 * len(heard) / len(sel)) if sel else None,
                "echo_median_ms": int(statistics.median(delays)) if delays else None,
            }
        return out


async def scope_lookup(url: str, hash_hex: str, ptype: int, timeout: float = 3.0) -> dict | None:
    """Ask a CoreScope instance who heard the packet and how. The search
    parameter does not match hashes, so this pulls a page of this packet
    type and matches on our side. NB the server ignores `timeRange`: a
    "15m" query returns packets days old. What saves it is the ordering,
    newest first, so a packet seconds old sits at the top of the page and
    `limit` is what really governs the reach. Then it reads every
    observation:
    `observers` = distinct observers, `repeated_by` = distinct observers
    whose copy had at least one repeater in its path (the only ones that
    prove a repeat), `direct_by` = observers that heard us zero-hop. Fail-soft."""
    try:
        import httpx
        base = url.rstrip("/")
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(base + "/api/packets", params={"timeRange": "15m", "type": str(ptype), "limit": "300"})
            r.raise_for_status()
            page = r.json().get("packets", [])
            hit = next((p for p in page if p.get("hash") == hash_hex), None)
            if hit is None:
                logger.debug("CoreScope: %s not in the %d newest type-%d packets", hash_hex, len(page), ptype)
                return None
            r = await c.get(f"{base}/api/packets/{hit['id']}")
            r.raise_for_status()
            return summarize_observations(r.json().get("observations") or [])
    except Exception as e:
        logger.debug("CoreScope lookup failed: %s", e)
    return None


def summarize_observations(observations: list[dict]) -> dict:
    import json as _json
    repeated, direct, paths = set(), set(), set()
    for o in observations:
        who = o.get("observer_id") or o.get("observer_name") or "?"
        path = o.get("path_json") or o.get("path") or []
        if isinstance(path, str):
            try:
                path = _json.loads(path)
            except ValueError:
                path = []
        if path:
            repeated.add(who)
            paths.add(",".join(str(h).upper() for h in path))
        else:
            direct.add(who)
    return {"observers": len(repeated | direct), "repeated_by": len(repeated), "direct_by": len(direct - repeated),
            "paths": sorted(paths)[:4]}


delivery_tracker = DeliveryTracker(persist_path=Path(settings.data_dir) / "delivery_outcomes.json")
