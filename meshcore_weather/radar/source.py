"""The newest radar picture of each product, from the dish's EMWIN directory.

goesproc writes radar GIFs next to the text products, under
`<dir>/YYYY-MM-DD/`, named `Z_<wmo>_C_KWIN_<received>_<seq>-3-<PRODUCT>.GIF`.
Nothing is decoded until a request needs it, and a decoded picture is kept
until a newer file for that product appears.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meshcore_weather.radar.picture import RadarPicture, read_picture, received_time

logger = logging.getLogger(__name__)

#: How long one listing of the directory is trusted.  A day's folder holds
#: some 27,000 files, so a burst of requests shares one scan.
SCAN_TTL_S = 30
#: A damaged newest file falls back to the ones before it, this many deep.
FALLBACK_DEPTH = 3


class RadarSource:
    def __init__(self, root: "str | Path"):
        self.root = Path(root).expanduser()
        self._listing: "dict[str, list[tuple[datetime, Path]]]" = {}
        self._listed_at = 0.0
        self._pictures: "dict[str, RadarPicture]" = {}
        self._bad: "set[Path]" = set()

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def _scan(self, now: datetime) -> None:
        if time.monotonic() - self._listed_at < SCAN_TTL_S and self._listing:
            return
        found: "dict[str, list[tuple[datetime, Path]]]" = {}
        settle = time.time() - 2          # goesproc may still be writing the newest
        for day in (now, now - timedelta(days=1)):
            folder = self.root / day.strftime("%Y-%m-%d")
            try:
                entries = os.scandir(folder)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    name = entry.name
                    if not name.upper().endswith(".GIF") or "-RAD" not in name.upper():
                        continue
                    received = received_time(name)
                    if received is None:
                        continue
                    try:
                        if entry.stat().st_mtime > settle:
                            continue
                    except OSError:
                        continue
                    product = name.rsplit("-", 1)[-1][:-4].upper()
                    found.setdefault(product, []).append((received, Path(entry.path)))
        for files in found.values():
            files.sort(reverse=True)
        self._listing = found
        self._listed_at = time.monotonic()

    def newest(self, product: str, now: "datetime | None" = None) -> "RadarPicture | None":
        """The newest readable picture of a product, decoded, or None."""
        now = now or datetime.now(timezone.utc)
        self._scan(now)
        for _, path in self._listing.get(product, [])[:FALLBACK_DEPTH]:
            if path in self._bad:
                continue
            held = self._pictures.get(product)
            if held is not None and held.path == path:
                return held
            picture = read_picture(path, product)
            if picture is None:
                self._bad.add(path)
                continue
            self._pictures[product] = picture
            return picture
        return None

    def status(self, now: "datetime | None" = None) -> dict:
        """Newest file per product, for the portal.  Decodes nothing."""
        now = now or datetime.now(timezone.utc)
        self._scan(now)
        return {
            product: {"file": files[0][1].name, "received": files[0][0].isoformat(),
                      "age_min": int((now - files[0][0]).total_seconds() // 60)}
            for product, files in sorted(self._listing.items()) if files
        }
