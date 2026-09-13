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
# A station farther than this is not "your" observation (Guam's nearest
# entry in stations.json was Hawaii, 5,966 km away).
STATION_MAX_KM = 150.0


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
        if km > STATION_MAX_KM:
            break
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


# -- Hazardous Weather Outlook ----------------------------------------------------


@dataclass
class Outlook:
    zone: str
    product: EMWINProduct

    @property
    def issued_at(self) -> datetime:
        return self.product.timestamp

    def summary_text(self) -> str:
        return hwo_summary(self.product.raw_text)

    def to_bytes(self) -> bytes | None:
        from meshcore_weather.protocol.encoders import encode_hwo
        issued_min = self.product.timestamp.hour * 60 + self.product.timestamp.minute
        return encode_hwo(self.zone, self.product.raw_text, issued_min)


def outlook_for(store: WeatherStore, loc: dict) -> Outlook | None:
    """Latest HWO covering the location's zone: first by the zone's office,
    then any HWO whose UGC line lists the zone."""
    from meshcore_weather.parser.weather import _expand_zone_ranges
    zones = loc.get("zones") or []
    if not zones:
        return None
    zone = zones[0]
    hwo = store._find_any_orig("HWO", store._build_origs(loc))
    if hwo is None:
        loc_zones = set(zones)
        for prod in store._products.values():
            if prod.product_type != "HWO":
                continue
            if loc_zones & _expand_zone_ranges(prod.raw_text):
                if hwo is None or prod.timestamp > hwo.timestamp:
                    hwo = prod
    return Outlook(zone=zone, product=hwo) if hwo else None


def hwo_summary(text: str) -> str:
    """Compact the Day 1 / Days 2-7 sections of an HWO into one string."""
    import re
    parts: list[str] = []
    collecting = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(".DAY"):
            section = s.lstrip(".").strip()
            section = (section.replace("DAYS TWO THROUGH SEVEN...", "D2-7:")
                              .replace("DAY ONE...", "Today:"))
            parts.append(section)
            collecting = True
            continue
        if s.startswith(".SPOTTER") or s.startswith("$$") or s.startswith("&&"):
            if collecting:
                break
            continue
        if not collecting or not s:
            continue
        if re.match(r"^[A-Z]{2}[ZC]\d{3}", s) or re.match(r"^\d{3,4}\s+(AM|PM)\s+\w+", s):
            continue
        if s.startswith("See the Graphical") or s.startswith("http"):
            continue
        if re.match(r"^(RISK|AREA|ONSET|DISCUSSION)\.\.\.", s):
            s = s.replace("...", ": ", 1).rstrip(".")
        parts.append(s)
    if not parts:
        return "No hazards identified."
    out: list[str] = []
    for p in parts:
        if p.endswith(":"):
            out.append(("\n" if out else "") + p)
        elif out and out[-1].endswith(":"):
            out.append(" " + p)
        else:
            out.append(" " + p if out else p)
    return "".join(out)


# -- Local Storm Reports ------------------------------------------------------------


@dataclass
class StormReports:
    zone: str
    state: str
    entries: list[dict] = field(default_factory=list)   # time, event, location, mag, state

    def to_bytes(self) -> bytes | None:
        from meshcore_weather.protocol.encoders import encode_lsr_reports, now_utc_minutes
        return encode_lsr_reports(self.zone, self.entries, now_utc_minutes())


def parse_lsr_entries(text: str) -> list[dict]:
    """Individual reports from an LSR product (fixed-width two-line entries)."""
    import re
    entries = []
    lines = text.splitlines()
    i = 0
    while i < len(lines) - 1:
        m = re.match(r"^(\d{4}\s+[AP]M)\s+(\S.*\S)\s{2,}(.*?)\s+\d{2,3}\.\d{2}[NS]", lines[i])
        if m:
            mag = ""
            state = ""
            for j in range(i + 1, min(i + 3, len(lines))):
                line2 = lines[j].strip()
                if not line2:
                    continue
                m2 = re.match(r"\d{2}/\d{2}/\d{4}\s+(\S+.*?)\s{2,}(\S.*?)\s+([A-Z]{2})\s", line2)
                if m2:
                    mag = m2.group(1).strip()
                    state = m2.group(3)
                break
            entries.append({
                "time": m.group(1).strip(),
                "event": m.group(2).strip(),
                "location": m.group(3).strip(),
                "mag": mag,
                "state": state,
            })
        i += 1
    return entries


def product_state(prod: EMWINProduct) -> str:
    """State a warning-type product actually affects: the first UGC line's
    prefix (offices near borders issue for neighbouring states)."""
    import re
    for line in prod.raw_text.splitlines()[:25]:
        m = re.match(r"^([A-Z]{2})[ZC]\d{3}", line.strip())
        if m:
            return m.group(1)
    return prod.state


def storm_reports_for(store: WeatherStore, loc: dict | None = None, state: str | None = None,
                      limit: int = 16) -> StormReports | None:
    """Deduplicated recent LSR entries for a state (the location's state by
    default), newest products first."""
    if state is None:
        zones = (loc or {}).get("zones") or []
        if not zones:
            return None
        state = zones[0][:2]
        zone = zones[0]
    else:
        zone = f"{state}Z000"
    state = state.upper()
    seen: set[str] = set()
    entries: list[dict] = []
    for prod in sorted(store._products.values(), key=lambda p: p.timestamp, reverse=True):
        if prod.product_type != "LSR":
            continue
        if prod.state != state and product_state(prod) != state:
            continue
        for e in parse_lsr_entries(prod.raw_text):
            if e.get("state") and e["state"] != state:
                continue
            key = f"{e['time']}_{e['event']}_{e['location']}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(e)
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    return StormReports(zone=zone, state=state, entries=entries) if entries else None


# -- Nowcast (NOW) ----------------------------------------------------------------


@dataclass
class Nowcast:
    wfo: str
    product: EMWINProduct

    def body(self) -> str:
        from meshcore_weather.protocol.encoders import _extract_now_body
        return _extract_now_body(self.product.raw_text)

    def to_bytes(self) -> list[bytes]:
        from meshcore_weather.protocol.encoders import encode_nowcast
        return encode_nowcast(self.wfo, self.product.raw_text) or []


def nowcast_for(store: WeatherStore, loc: dict) -> Nowcast | None:
    prod = store._find_any_orig("NOW", store._build_origs(loc))
    return Nowcast(wfo=prod.office, product=prod) if prod else None


# -- Raw METAR / TAF ------------------------------------------------------------------


def raw_metar_for(store: WeatherStore, loc: dict, max_age_min: int = OBS_MAX_AGE_MIN) -> tuple[str, float, str] | None:
    """(station, km, raw METAR line) from the nearest reporting station."""
    now = datetime.now(timezone.utc)
    for icao, km in loc.get("stations") or []:
        if km > STATION_MAX_KM:
            break
        raw = store._find_metar_raw(icao)
        if raw and (now - raw[1]) <= timedelta(minutes=max_age_min):
            return icao, float(km), raw[0]
    return None


@dataclass
class Taf:
    station: str
    distance_km: float
    product: EMWINProduct
    text: str          # the station's TAF block, joined on one line

    def to_bytes(self) -> bytes | None:
        from meshcore_weather.protocol.encoders import encode_taf, now_utc_minutes
        hours_ago = max(0, int((now_utc_minutes() - self.product.timestamp.hour * 60
                                - self.product.timestamp.minute) / 60))
        return encode_taf(self.station, self.product.raw_text, hours_ago)


def _taf_block(text: str, station: str) -> str | None:
    import re
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(f"TAF {station}") or s.startswith(f"TAF AMD {station}") or (
            s.startswith(station + " ") and re.match(r"^[A-Z]{4}\s+\d{6}Z", s)
        ):
            block = [s]
            for j in range(i + 1, min(i + 15, len(lines))):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("=") or nxt.startswith("TAF ") or re.match(r"^[A-Z]{4}\s+\d{6}Z", nxt):
                    break
                block.append(nxt)
            return " ".join(block)
    return None


def taf_for(store: WeatherStore, loc: dict) -> Taf | None:
    """Latest TAF for the nearest station that has one (TAFs are issued for
    ~700 airports, so the nearest METAR station may not have one)."""
    for icao, km in loc.get("stations") or []:
        if km > STATION_MAX_KM:
            break
        for prod in sorted(store._products.values(), key=lambda p: p.timestamp, reverse=True):
            if prod.product_type != "TAF":
                continue
            block = _taf_block(prod.raw_text, icao)
            if block:
                return Taf(station=icao, distance_km=float(km), product=prod, text=block)
    return None


# -- Rain observations (RWR) --------------------------------------------------------------


_RAIN_WORDS = {"RAIN", "LGT RAIN", "HVY RAIN", "TSTORM", "T-STORM", "DRIZZLE", "SHOWERS", "SHOWER", "SNOW"}
_SKY_WORDS = _RAIN_WORDS | {"SUNNY", "MOSUNNY", "PTSUNNY", "CLEAR", "MOCLDY", "PTCLDY", "CLOUDY",
                            "FAIR", "FOG", "HAZE", "WINDY", "LGT", "HVY", "N/A", "NOT", "AVBL"}


@dataclass
class RainObs:
    zone: str
    state: str
    cities: list[dict] = field(default_factory=list)   # name, state, rain_text, temp_f

    def to_bytes(self) -> bytes | None:
        from meshcore_weather.protocol.encoders import encode_rain_cities, now_utc_minutes
        return encode_rain_cities(self.zone, self.cities, now_utc_minutes())


def rain_for(store: WeatherStore, loc: dict | None = None, state: str | None = None) -> RainObs | None:
    """Cities in the RWR roundups currently reporting precipitation, for the
    location's state (or an explicit state)."""
    if state is None:
        zones = (loc or {}).get("zones") or []
        if not zones:
            return None
        state, zone = zones[0][:2], zones[0]
    else:
        state = state.upper()
        zone = f"{state}Z000"
    rainy: list[dict] = []
    seen: set[str] = set()
    for prod in store._products.values():
        if prod.product_type != "RWR" or prod.state != state:
            continue
        in_table = False
        for line in prod.raw_text.splitlines():
            s = line.strip()
            if "SKY/WX" in s and "TMP" in s:
                in_table = True
                continue
            if not in_table or not s:
                continue
            if s.startswith("$$"):
                in_table = False
                continue
            up = s.upper()
            if not any(k in up for k in _RAIN_WORDS):
                continue
            parts = s.split()
            if parts and parts[0].startswith("*"):
                parts[0] = parts[0][1:]
            city_parts: list[str] = []
            wx = ""
            temp = 60
            for p in parts:
                if p.upper() in _SKY_WORDS:
                    wx = p
                    break
                if p.lstrip("-").isdigit():
                    break
                city_parts.append(p)
            if wx in parts:
                for tp in parts[parts.index(wx) + 1:]:
                    if tp.lstrip("-").isdigit():
                        temp = int(tp)
                        break
            name = " ".join(city_parts).title().strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rainy.append({"name": name, "state": state, "rain_text": wx or "rain", "temp_f": temp})
    return RainObs(zone=zone, state=state, cities=rainy) if rainy else None
