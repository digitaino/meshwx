"""One place that answers "what does the radar show here": the `>radar`
request and the `radar` text command both come through it.

It holds no schedule and no thread.  A question reads the newest picture of
the best product for the tile (cached until a newer file arrives) and cuts the
tile out of it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meshcore_weather.config import settings
from meshcore_weather.protocol import v5
from meshcore_weather.radar.picture import RadarPicture
from meshcore_weather.radar.source import RadarSource
from meshcore_weather.radar.tiles import Frame, RadarTile, candidates, cut_tile, load_frames

logger = logging.getLogger(__name__)

#: A picture older than this is not an answer to "what is out there now".
MAX_AGE_MIN = 60


class RadarService:
    def __init__(self, root: "str | Path | None", frames: "list[Frame] | None" = None):
        self.source = RadarSource(root) if root else None
        self.frames = frames if frames is not None else load_frames()

    @property
    def available(self) -> bool:
        """Does this bot have anywhere to get radar pictures from at all?"""
        return self.source is not None and self.source.available

    def tile_for(self, lat: float, lon: float, zoom: int = 0, now: "datetime | None" = None
                 ) -> "tuple[RadarTile, RadarPicture, Frame] | None":
        """The tile for a coordinate, from the best picture that can give one."""
        if not self.available:
            return None
        now = now or datetime.now(timezone.utc)
        south, west = v5.radar_tile(lat, lon, zoom)
        for frame in candidates(self.frames, south, west, zoom):
            picture = self.source.newest(frame.id, now)
            if picture is None:
                continue
            if not picture.frame_ok:
                logger.warning("radar: %s is not drawn on the map it was calibrated on; "
                               "run scripts/radar_calibrate.py", picture.path.name)
                continue
            if now - picture.taken > timedelta(minutes=MAX_AGE_MIN):
                continue
            tile = cut_tile(picture, frame, south, west, zoom)
            if tile is not None:
                return tile, picture, frame
        return None

    def status(self) -> dict:
        if not self.available:
            return {"available": False, "products": {}}
        calibrated = {f.id for f in self.frames if f.calibrated}
        products = self.source.status()
        for name, info in products.items():
            info["calibrated"] = name in calibrated
        return {"available": True, "dir": str(self.source.root), "products": products}


_shared: "RadarService | None" = None


def radar_dir() -> "str | None":
    """Where the radar GIFs are: `MCW_RADAR_DIR`, else the dish's EMWIN
    directory when the dish is the source, else nowhere."""
    if settings.radar_dir:
        return settings.radar_dir
    return settings.sdr_emwin_dir if settings.emwin_source == "sdr" else None


def shared() -> RadarService:
    global _shared
    if _shared is None:
        _shared = RadarService(radar_dir())
    return _shared
