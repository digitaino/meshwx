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
import logging
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
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
    observed: dict | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def heard(self) -> bool:
        return self.echoed_at is not None or self.acked_at is not None


class DeliveryTracker:
    def __init__(self):
        self._by_hash: dict[str, Outbound] = {}
        self._by_ack: dict[str, Outbound] = {}
        self._tasks: set[asyncio.Task] = set()
        self._resends: deque[float] = deque(maxlen=1000)
        self._outcomes: deque[tuple] = deque(maxlen=5000)   # (t, kind, echoed, acked, resent, echo_ms)
        self.started_at = time.time()
        self.last_repeat_heard_at = 0.0
        self.rx_frames = 0
        self.rx_repeats = 0

    # -- what the radio tells us --

    def on_rx_log(self, raw: bytes, snr: float | None = None) -> None:
        pkt = parse_packet(raw)
        self.rx_frames += 1
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

    async def _watch(self, ob: Outbound) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(ob.done.wait(), ob.window_s)
                except asyncio.TimeoutError:
                    pass
                if ob.heard:
                    break
                if ob.attempts > settings.retransmit_max:
                    break
                reason = self._why_not_resend(ob)
                if reason:
                    ob.skipped = reason
                    break
                if settings.scope_url and settings.scope_mode == "decide" and ob.hash:
                    ob.observed = await scope_lookup(settings.scope_url, ob.hash, ob.ptype)
                    if ob.observed:
                        ob.skipped = "seen by CoreScope"
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
        if ob.ev is not None:
            from meshcore_weather.traffic import traffic_log
            traffic_log.update(ob.ev, delivery=d, push=True)
        logger.info("Delivery %s: %s", ob.kind, d["result"] + (f" via {fmt_path(ob.via)}" if ob.via else "") +
                    (f" ({d['echo_ms']} ms)" if d.get("echo_ms") is not None else "") +
                    (f", {ob.attempts - 1} retransmit" if ob.attempts > 1 else ""))
        if not ob.heard and ob.give_up is not None:
            try:
                await ob.give_up()
            except Exception:
                logger.exception("give-up hook failed")
        if settings.scope_url and ob.hash and ob.observed is None and settings.scope_mode in ("stats", "decide"):
            t = asyncio.create_task(self._annotate_from_scope(ob))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _annotate_from_scope(self, ob: Outbound) -> None:
        """A minute later, ask CoreScope who heard it (statistics only)."""
        await asyncio.sleep(45)
        ob.observed = await scope_lookup(settings.scope_url, ob.hash, ob.ptype)
        if ob.ev is not None and ob.observed:
            from meshcore_weather.traffic import traffic_log
            traffic_log.update(ob.ev, delivery=self.outcome(ob), push=True)

    @staticmethod
    def outcome(ob: Outbound) -> dict:
        echo_ms = int((ob.echoed_at - ob.sent_at) * 1000) if ob.echoed_at else None
        rtt_ms = int((ob.acked_at - ob.sent_at) * 1000) if ob.acked_at else None
        if ob.acked_at:
            result = "acked"
        elif ob.echoed_at:
            result = "echoed"
        elif ob.skipped:
            result = "skipped"
        else:
            result = "no_ack" if ob.kind == "dm" else "no_echo"
        return {"result": result, "echo": ob.echoed_at is not None, "echo_ms": echo_ms,
                "via": fmt_path(ob.via) if ob.via else None, "snr": ob.echo_snr,
                "acked": ob.acked_at is not None, "rtt_ms": rtt_ms,
                "attempts": ob.attempts, "resent": ob.attempts - 1, "skipped": ob.skipped,
                "observed_by": (ob.observed or {}).get("observers"),
                "observer": (ob.observed or {}).get("observer")}

    # -- for the portal --

    def stats(self) -> dict:
        now = time.time()
        out: dict = {"rx_frames": self.rx_frames, "rx_repeats": self.rx_repeats,
                     "last_repeat_heard_at": self.last_repeat_heard_at or None,
                     "pending": len(self._by_hash) + len(self._by_ack), "windows": {}}
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
    """Ask a CoreScope instance whether any observer heard the packet. The
    search parameter does not match hashes, so this scans the last 15
    minutes of the packet type and matches on our side. Fail-soft."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url.rstrip("/") + "/api/packets",
                            params={"timeRange": "15m", "type": str(ptype), "limit": "300"})
            r.raise_for_status()
            for p in r.json().get("packets", []):
                if p.get("hash") == hash_hex:
                    return {"observers": p.get("observation_count"), "observer": p.get("observer_name"),
                            "path": p.get("_parsedPath")}
    except Exception as e:
        logger.debug("CoreScope lookup failed: %s", e)
    return None


delivery_tracker = DeliveryTracker()
