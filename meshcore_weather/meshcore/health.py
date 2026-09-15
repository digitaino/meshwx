"""Is the radio hardware doing its job? A verdict from what the bot can
observe without any extra equipment.

The telling patterns:

* **tx_suspect**: several of our own sends in a row got no echo (and no
  ACK) while the node was still hearing other people's repeats. The
  receiver works, the mesh is alive, our packets are not getting out.
* **rx_silent**: nothing at all heard from anyone for a long time. On a
  mesh with any traffic that is a deaf receiver (or a dead antenna), not a
  quiet night.
* **idle**: no sends to judge; the receiver is hearing traffic.
* **tx_off**: transmit is switched off; nothing to judge.

The firmware's own radio statistics (noise floor, last RSSI/SNR, airtime
counters) are shown next to the verdict; a noise floor far above the
usual −110 to −120 dBm on a quiet channel is a hardware or interference
problem the echo counters cannot separate.
"""

from __future__ import annotations

import re
import time

# GRP_DATA (channel datagrams) arrived in the companion firmware and the
# phone apps with this release; older nodes never deliver the v5 packets.
MIN_FIRMWARE = (1, 15, 0)
TX_SUSPECT_STREAK = 3
#: Share of wall-clock time the event loop may run late before an unheard
#: echo says more about this software than about the radio.
LAG_SUSPECT_PCT = 2.0
MESH_ALIVE_S = 600                # a repeat from someone else this recently = the mesh is up
CHANNEL_KINDS = ("channel_text", "channel_data")


def parse_version(ver: str | None) -> tuple[int, ...] | None:
    """'v1.17.1-d929643' -> (1, 17, 1)."""
    if not ver:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(ver))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def unheard_streak(outcomes: list[tuple]) -> int:
    """Consecutive most recent channel sends with neither echo nor ACK."""
    n = 0
    for row in reversed(outcomes):
        t, kind, echoed, acked, resent, echo_ms = row[:6]     # a DM reply row carries a 7th field
        if kind not in CHANNEL_KINDS:
            continue
        if echoed or acked:
            break
        n += 1
    return n


def assess(*, outcomes: list[tuple], last_rx_at: float, last_repeat_heard_at: float, rx_frames: int,
           started_at: float, tx_enabled: bool, rx_silent_s: float, loop_lag_pct: float = 0.0,
           now: float | None = None) -> dict:
    now = now or time.time()
    streak = unheard_streak(outcomes)
    rx_age = (now - last_rx_at) if last_rx_at else (now - started_at)
    mesh_alive = bool(last_repeat_heard_at) and now - last_repeat_heard_at < MESH_ALIVE_S
    last_heard_send = next((r[0] for r in reversed(outcomes) if r[1] in CHANNEL_KINDS and (r[2] or r[3])), None)
    sends = [r for r in outcomes if r[1] in CHANNEL_KINDS]

    if rx_age >= rx_silent_s and (rx_frames or now - started_at >= rx_silent_s):
        verdict, reason = "rx_silent", f"nothing heard from anyone for {int(rx_age / 60)} min"
    elif not tx_enabled:
        verdict, reason = "tx_off", "transmit is off; the receiver is " + ("hearing traffic" if rx_frames else "quiet")
    elif streak >= TX_SUSPECT_STREAK and mesh_alive and loop_lag_pct >= LAG_SUSPECT_PCT:
        verdict = "unknown"
        reason = (f"{streak} sends in a row got no echo, but this bot's own event loop ran late "
                  f"{loop_lag_pct:.0f}% of the time: the echo may have arrived and been handled too late "
                  f"to count. Fix the stall before suspecting the radio")
    elif streak >= TX_SUSPECT_STREAK and mesh_alive:
        verdict = "tx_suspect"
        reason = (f"{streak} sends in a row got no echo while repeats from other nodes were heard "
                  f"{int((now - last_repeat_heard_at) / 60)} min ago")
    elif streak >= TX_SUSPECT_STREAK:
        verdict = "unknown"
        reason = f"{streak} sends in a row got no echo, but no repeater has been heard either: the mesh may be quiet"
    elif not sends:
        verdict, reason = "idle", "no sends to judge yet"
    else:
        verdict = "ok"
        reason = "the last send was heard" if streak == 0 else f"{streak} recent send(s) unheard, within normal loss"
    return {
        "verdict": verdict, "reason": reason,
        "unheard_streak": streak, "mesh_alive": mesh_alive,
        "last_rx_at": last_rx_at or None, "rx_age_s": int(rx_age) if last_rx_at else None,
        "last_repeat_heard_at": last_repeat_heard_at or None,
        "last_heard_send_at": last_heard_send,
        "loop_lag_pct": round(loop_lag_pct, 2),
        "checked_at": now,
    }


def firmware_check(ver: str | None) -> dict:
    parsed = parse_version(ver)
    return {"ver": ver, "parsed": list(parsed) if parsed else None,
            "min": ".".join(str(x) for x in MIN_FIRMWARE),
            "ok": bool(parsed) and parsed >= MIN_FIRMWARE}
