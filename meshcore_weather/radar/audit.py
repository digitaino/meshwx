"""What radar tiles went out, kept so the conversion can be checked afterwards.

One JSON line per tile the bot actually transmitted: what was asked, which
picture it was cut from, and the bytes that left. `scripts/radar_audit.py`
reads these on another machine, fetches the same GIFs while the dish's three
days of retention still hold them, and scores each tile against its source.

It is a log for the operator, not state: nothing reads it back, a failure to
write it never touches an answer, and it is cut at two megabytes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from meshcore_weather.config import settings

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000


def path() -> Path:
    return Path(settings.data_dir) / "radar_audit.jsonl"


def record(*, request: str, sender: str, tile, picture, frame, packet: bytes,
           now: "float | None" = None, file: "Path | None" = None) -> None:
    """Append one served tile. Never raises."""
    try:
        target = file or path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > MAX_BYTES:
            target.replace(target.with_suffix(".jsonl.1"))
        line = {
            "ts": int(now if now is not None else time.time()),
            "request": request.strip()[:48],
            # Enough to tell two askers apart, not enough to be a key.
            "sender": sender[:12],
            "south": tile.south, "west": tile.west, "zoom": tile.zoom,
            "product": frame.id,
            "file": picture.path.name,
            "taken_min": int(picture.taken.timestamp() // 60),
            "taken_is_printed": picture.taken_is_printed,
            "received": int(picture.received.timestamp()),
            "coarse": bool(packet[3] & 0x1), "partial": bool(packet[3] & 0x2),
            "wet": tile.wet, "bytes": len(packet), "hex": packet.hex(),
        }
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    except Exception:
        logger.exception("radar audit: could not record a tile")
