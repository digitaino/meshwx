"""Product services: the single implementation of "what is the weather at
this location" used by the text commands, the scheduler, and on-demand
requests.

Each service takes the WeatherStore and a resolved location dict (from
`geodata.resolver.resolve`) and returns a canonical object. Rendering to
text lives in `core.render_text`; rendering to bytes uses the packers in
`protocol.meshwx` via the `to_bytes()` helpers here.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from meshcore_weather.parser.pfm import PFMPoint, downsample_to_daily, parse_pfm
from meshcore_weather.parser.weather import EMWINProduct, WeatherStore
from meshcore_weather.protocol.encoders import parse_metar
from meshcore_weather.protocol.meshwx import (
    LOC_STATION,
    LOC_ZONE,
    pack_forecast,
    pack_observation,
)

logger = logging.getLogger(__name__)

# An observation older than this is not "current".
OBS_MAX_AGE_MIN = 120
# A forecast point farther than this is not "your" forecast.
FORECAST_MAX_KM = 80.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# -- Observation ------------------------------------------------------------------


@dataclass
class Observation:
    station: str
    distance_km: float
    observed_at: datetime          # UTC, product receive time (METAR line has only hh:mm)
    obs_utc_min: int               # minutes since midnight UTC from the METAR itself
    temp_f: int
    dewpoint_f: int
    wind_dir_deg: int
    wind_speed_mph: int
    wind_gust_mph: int
    visibility_mi: int
    pressure_inhg: float
    sky_code: int
    raw: str

    @property
    def age_min(self) -> int:
        return max(0, int((datetime.now(timezone.utc) - self.observed_at).total_seconds() / 60))

    def to_bytes(self, loc_type: int | None = None, loc_id=None) -> bytes:
        return pack_observation(
            loc_type if loc_type is not None else LOC_STATION,
            loc_id if loc_id is not None else self.station,
            timestamp_utc_min=self.obs_utc_min,
            temp_f=self.temp_f,
            dewpoint_f=self.dewpoint_f,
            wind_dir_deg=self.wind_dir_deg,
            sky_code=self.sky_code,
            wind_speed_mph=self.wind_speed_mph,
            wind_gust_mph=self.wind_gust_mph,
            visibility_mi=self.visibility_mi,
            pressure_inhg=self.pressure_inhg,
        )


def observation_for(
    store: WeatherStore, loc: dict, max_age_min: int = OBS_MAX_AGE_MIN,
) -> Observation | None:
    """Latest fresh METAR from the nearest station THAT ACTUALLY REPORTS.

    Walks the resolver's distance-ranked station list and returns the first
    one with a METAR newer than `max_age_min`. A silent AWOS next door never
    hides a reporting airport a few km further away.
    """
    now = datetime.now(timezone.utc)
    candidates = loc.get("stations") or ([(loc["station"], loc.get("station_km") or 0.0)] if loc.get("station") else [])
    for icao, km in candidates:
        raw = store._find_metar_raw(icao)
        if not raw:
            continue
        text, ts = raw
        if (now - ts) > timedelta(minutes=max_age_min):
            continue
        fields = parse_metar(text)
        if not fields:
            continue
        return Observation(
            station=icao,
            distance_km=float(km),
            observed_at=ts,
            raw=text,
            **fields,
        )
    return None


# -- Forecast ---------------------------------------------------------------------


@dataclass
class Forecast:
    point_name: str
    point_zone: str
    distance_km: float
    issued_at: datetime | None
    source: str                        # "PFM"
    periods: list[dict] = field(default_factory=list)   # pack_forecast period dicts
    start_date: datetime | None = None                    # local date of period 0

    @property
    def issued_hours_ago(self) -> int:
        if not self.issued_at:
            return 0
        return max(0, int((datetime.now(timezone.utc) - self.issued_at).total_seconds() / 3600))

    def to_bytes(self, loc_type: int | None = None, loc_id=None) -> bytes:
        return pack_forecast(
            loc_type if loc_type is not None else LOC_ZONE,
            loc_id if loc_id is not None else self.point_zone,
            issued_hours_ago=self.issued_hours_ago,
            periods=self.periods,
        )


# Parsed PFM cache: product filename -> list[PFMPoint]. PFMs are 30 KB of
# fixed-column text and every request would otherwise re-parse them.
_pfm_cache: dict[str, list[PFMPoint]] = {}


def _parsed_pfm(prod: EMWINProduct) -> list[PFMPoint]:
    pts = _pfm_cache.get(prod.filename)
    if pts is None:
        try:
            pts = parse_pfm(prod.raw_text)
        except Exception as exc:
            logger.warning("PFM %s failed to parse: %s", prod.emwin_id, exc)
            pts = []
        if len(_pfm_cache) > 200:
            _pfm_cache.clear()
        _pfm_cache[prod.filename] = pts
    return pts


def nearest_pfm_point(
    store: WeatherStore, lat: float, lon: float, max_km: float = FORECAST_MAX_KM,
) -> tuple[PFMPoint, EMWINProduct, float] | None:
    """Nearest PFM forecast point across every PFM product in the store,
    newest product per office. Zone equality is NOT required: Kyle gets San
    Marcos Airport (12 km) even though that point is filed under Caldwell."""
    newest: dict[str, EMWINProduct] = {}
    for prod in store._products.values():
        if prod.product_type != "PFM":
            continue
        cur = newest.get(prod.emwin_id)
        if cur is None or prod.timestamp > cur.timestamp:
            newest[prod.emwin_id] = prod
    best: tuple[PFMPoint, EMWINProduct, float] | None = None
    for prod in newest.values():
        for pt in _parsed_pfm(prod):
            d = _haversine_km(lat, lon, pt.lat, pt.lon)
            if d <= max_km and (best is None or d < best[2]):
                best = (pt, prod, d)
    return best


def forecast_for(store: WeatherStore, loc: dict, max_days: int = 7) -> Forecast | None:
    found = nearest_pfm_point(store, loc["lat"], loc["lon"])
    if not found:
        return None
    pt, prod, km = found
    daily = downsample_to_daily(pt, max_days=max_days)
    if not daily:
        return None
    # Local date of period 0: first local date with a full day of slots.
    dates = sorted({pt.local_date(s.dt) for s in pt.slots})
    start = None
    if dates:
        counts = {d: sum(1 for s in pt.slots if pt.local_date(s.dt) == d) for d in dates}
        full = [d for d in dates if counts[d] >= 4]
        start = datetime.combine(full[0] if full else dates[0], datetime.min.time())
    return Forecast(
        point_name=pt.name,
        point_zone=pt.zone,
        distance_km=round(km, 1),
        issued_at=pt.issue_time or prod.timestamp,
        source="PFM",
        periods=[p.to_encoder_dict() for p in daily],
        start_date=start,
    )


# -- Warnings ---------------------------------------------------------------------


def warnings_for(store: WeatherStore, loc: dict, all_active: list[dict] | None = None) -> list[dict]:
    """Active warnings that apply to the location: any whose UGC list
    includes the location's zone, or whose storm-based polygon contains the
    point. Uses the pyIEM extractor (the same one the broadcasts use)."""
    from meshcore_weather.protocol.coverage import _point_in_polygon
    from meshcore_weather.protocol.warnings import extract_active_warnings

    if all_active is None:
        all_active = extract_active_warnings(store, coverage=None)
    zone = loc["zones"][0] if loc.get("zones") else ""
    lat, lon = loc.get("lat"), loc.get("lon")
    out = []
    for w in all_active:
        ugcs = set(w.get("ugcs") or w.get("zones") or [])
        if zone and zone in ugcs:
            out.append(w)
            continue
        if lat is not None and w.get("vertices") and _point_in_polygon(lat, lon, w["vertices"]):
            out.append(w)
    # Most urgent first: warnings, then watches, then advisories; sooner expiry first.
    sev_rank = {"W": 0, "A": 1, "Y": 2, "S": 3}
    out.sort(key=lambda w: (sev_rank.get(w.get("vtec_significance") or "S", 3), w["expires_at"]))
    return out
