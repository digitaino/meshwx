"""From the product store to v5 messages.

One implementation for both the scheduled broadcasts and the app requests
(the v4 code had two, and they drifted). Everything here returns bytes
ready for `radio.send_channel_data()`, or None when the store has nothing
to say. The wire layouts live in protocol/v5.py; this module only knows
where the numbers come from.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meshcore_weather.geodata import resolver
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5
from meshcore_weather.protocol.encoders import parse_metar

logger = logging.getLogger(__name__)

_CLIENT_DATA = Path(__file__).resolve().parent.parent / "client_data"

# Warnings that get one repeat 90 s after the first send.
LIFE_SAFETY = {"TO.W", "SV.W", "FF.W", "EW.W"}

OBS_MAX_AGE_MIN = 120
MAX_OBS_STATIONS = 14
MAX_DIGEST = 25


# -- Wire tables (index.json) --------------------------------------------------------


class _Tables:
    def __init__(self):
        self._loaded = False
        self.offices: list[str] = []
        self.stations: list[str] = []
        self.states: list[str] = []
        self.points: list[list] = []
        self.events: dict[str, int] = {}
        self._office_idx: dict[str, int] = {}
        self._station_idx: dict[str, int] = {}

    def load(self) -> None:
        if self._loaded:
            return
        try:
            idx = json.loads((_CLIENT_DATA / "index.json").read_text())
            self.offices, self.stations, self.states = idx["offices"], idx["stations"], idx["states"]
        except Exception:
            # index.json is generated from these; the same ordering rule
            # keeps a fresh checkout working.
            self.offices = sorted(json.loads((_CLIENT_DATA / "wfos.json").read_text()))
            self.stations = sorted(json.loads((_CLIENT_DATA / "stations.json").read_text()))
            self.states = json.loads((_CLIENT_DATA / "state_index.json").read_text())["states"]
        self.points = json.loads((_CLIENT_DATA / "pfm_points.json").read_text())["points"]
        self.events = json.loads((_CLIENT_DATA / "protocol.json").read_text()).get("events", {})
        self._office_idx = {c: i for i, c in enumerate(self.offices)}
        self._station_idx = {c: i for i, c in enumerate(self.stations)}
        self._loaded = True

    def office(self, code: str | None) -> int:
        self.load()
        return self._office_idx.get((code or "").upper(), 0)

    def office_code(self, idx: int) -> str:
        self.load()
        return self.offices[idx] if 0 <= idx < len(self.offices) else "???"

    def station(self, icao: str) -> int | None:
        self.load()
        return self._station_idx.get(icao.upper())

    def event_code(self, key: str) -> int:
        self.load()
        return int(self.events.get(key, 0))

    def point_index(self, lat: float, lon: float, max_km: float = 1.5) -> int:
        """The bundled PFM point at these coordinates, or 0xFFFF."""
        self.load()
        best, best_km = 0xFFFF, max_km
        for i, p in enumerate(self.points):
            km = _haversine_km(lat, lon, p[2], p[3])
            if km < best_km:
                best, best_km = i, km
        return best


tables = _Tables()


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class SeqCounter:
    """Per-bot u8 message counter (wraps)."""

    def __init__(self, start: int = 0):
        self._n = start & 0xFF

    def next(self) -> int:
        n = self._n
        self._n = (n + 1) & 0xFF
        return n


def bot_id(public_key_hex: str | None) -> int:
    """The `bot` header field: first two bytes of the public key, LE."""
    if not public_key_hex or len(public_key_hex) < 4:
        return 0
    return int.from_bytes(bytes.fromhex(public_key_hex[:4]), "little")


def now_min() -> int:
    return int(time.time() // 60)


# -- Warnings ---------------------------------------------------------------------------


def warning_identity(w: dict) -> tuple[int, int, int] | None:
    """(event, office index, etn), or None for products without an ETN (SPS)."""
    etn = w.get("vtec_etn")
    if etn is None:
        return None
    event = int(w.get("event_code") or 0)
    if not event:
        return None
    return event, tables.office(w.get("vtec_office")), int(etn) & 0xFFFF


def identity_str(identity: tuple[int, int, int]) -> str:
    """'SV.W.EWX.42' for logs and the request grammar."""
    tables.load()
    code = next((k for k, v in tables.events.items() if v == identity[0]), str(identity[0]))
    return f"{code}.{tables.office_code(identity[1])}.{identity[2]}"


def parse_identity(text: str) -> tuple[int, int, int] | None:
    """'SV.W.EWX.42' -> (event, office index, etn)."""
    parts = text.strip().upper().split(".")
    if len(parts) != 4 or not parts[3].isdigit():
        return None
    event = tables.event_code(parts[0] + "." + parts[1])
    if not event or parts[2] not in tables.offices:
        return None
    return event, tables.office(parts[2]), int(parts[3]) & 0xFFFF


def expires_min(w: dict) -> int:
    exp = w.get("expires_at")
    if isinstance(exp, datetime):
        return int(exp.timestamp() // 60)
    return now_min() + int(w.get("expiry_minutes") or 60)


def warning_fingerprint(w: dict) -> tuple:
    """What counts as a material change: expiry moved by 30 min or more,
    tags changed, area changed. Wording changes do not."""
    return (expires_min(w) // 30, int(w.get("hail_qin") or 0), int(w.get("wind_mph") or 0),
            int(w.get("tornado_tag") or 0), int(w.get("flood_source") or 0), int(w.get("flood_damage") or 0),
            tuple(sorted(w.get("ugcs") or [])), len(w.get("vertices") or []))


def _decimate(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def warning_message(seq: int, bot: int, w: dict, update: bool = False) -> bytes | None:
    ident = warning_identity(w)
    if ident is None:
        return None
    event, office, etn = ident
    polygon = [(float(la), float(lo)) for la, lo in (w.get("vertices") or [])]
    if len(polygon) < 3:
        polygon = []
    tables.load()
    areas = v5.areas_from_ugcs(list(w.get("ugcs") or []), tables.states)
    kwargs = dict(event=event, office=office, etn=etn, expires_min=expires_min(w),
                  tornado=int(w.get("tornado_tag") or 0), flood_source=int(w.get("flood_source") or 0),
                  flood_damage=int(w.get("flood_damage") or 0), hail_qin=int(w.get("hail_qin") or 0),
                  wind_mph=int(w.get("wind_mph") or 0), update=update)
    # Fit into one packet: shed detail in the order a phone can best do without.
    attempts = [
        (polygon[:30], areas[:30]),
        (_decimate(polygon, 16), areas[:30]),
        (_decimate(polygon, 16), areas[:12]),
        (_decimate(polygon, 10), areas[:6]),
        (_decimate(polygon, 8), []),
        ([], areas[:30]),
    ]
    for poly, ar in attempts:
        if not poly and not ar:
            continue
        try:
            return v5.encode_warning(seq, bot, polygon=poly or None, areas=ar or None, **kwargs)
        except ValueError:
            continue
    logger.warning("Warning %s does not fit in one packet even without detail", identity_str(ident))
    return None


def cancel_message(seq: int, bot: int, identity: tuple[int, int, int], reason: int = 0) -> bytes:
    event, office, etn = identity
    return v5.encode_cancel(seq, bot, event=event, office=office, etn=etn, reason=reason)


def digest_message(seq: int, bot: int, active: list[dict], feed_health: int) -> bytes:
    entries = []
    for w in sorted(active, key=expires_min):
        ident = warning_identity(w)
        if ident is None:
            continue
        entries.append((ident[0], ident[1], ident[2], expires_min(w)))
        if len(entries) >= MAX_DIGEST:
            break
    return v5.encode_digest(seq, bot, now_min=now_min(), feed_health=feed_health, entries=entries)


def feed_health(store: WeatherStore, offices: set[str]) -> int:
    """Minutes since the newest product from any of the home offices, in
    4-minute units, 255 when nothing was ever received."""
    newest = None
    for prod in store._products.values():
        if prod.office in offices and (newest is None or prod.timestamp > newest):
            newest = prod.timestamp
    if newest is None:
        return 255
    age_min = (datetime.now(timezone.utc) - newest).total_seconds() / 60
    return max(0, min(255, int(age_min // 4)))


# -- Observations -----------------------------------------------------------------------


def coverage_stations(center: tuple[float, float] | None, radius_km: float, store: WeatherStore,
                      limit: int = MAX_OBS_STATIONS) -> list[str]:
    """ICAO stations inside the coverage circle that reported recently,
    nearest first."""
    if center is None:
        return []
    resolver.load()
    now = datetime.now(timezone.utc)
    out: list[tuple[float, str]] = []
    for icao, st in resolver._stations.items():
        try:
            km = _haversine_km(center[0], center[1], st["la"], st["lo"])
        except (KeyError, TypeError):
            continue
        if km > radius_km or tables.station(icao) is None:
            continue
        raw = store._find_metar_raw(icao)
        if not raw or (now - raw[1]) > timedelta(minutes=OBS_MAX_AGE_MIN):
            continue
        out.append((km, icao))
    return [icao for _, icao in sorted(out)[:limit]]


def _humidity(temp_f: int | None, dew_f: int | None) -> int | None:
    if temp_f is None or dew_f is None:
        return None
    t, d = (temp_f - 32) * 5 / 9, (dew_f - 32) * 5 / 9
    a, b = 17.625, 243.04
    rh = 100 * math.exp(a * d / (b + d)) / math.exp(a * t / (b + t))
    return max(0, min(100, int(round(rh))))


def _feels_delta(temp_f: int | None, rh: int | None, wind_mph: int | None) -> int:
    if temp_f is None:
        return 0
    t = float(temp_f)
    if t >= 80 and rh is not None and rh >= 40:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 0.00683783 * t * t
              - 0.05481717 * rh * rh + 0.00122874 * t * t * rh + 0.00085282 * t * rh * rh
              - 0.00000199 * t * t * rh * rh)
        return int(round(hi - t))
    if t <= 50 and wind_mph and wind_mph >= 3:
        wc = 35.74 + 0.6215 * t - 35.75 * wind_mph ** 0.16 + 0.4275 * t * wind_mph ** 0.16
        return int(round(wc - t))
    return 0


def obs_message(seq: int, bot: int, store: WeatherStore, stations: list[str]) -> bytes | None:
    now = datetime.now(timezone.utc)
    rows, newest = [], None
    for icao in stations[:MAX_OBS_STATIONS]:
        idx = tables.station(icao)
        raw = store._find_metar_raw(icao)
        if idx is None or not raw:
            continue
        text, ts = raw
        if (now - ts) > timedelta(minutes=OBS_MAX_AGE_MIN):
            continue
        f = parse_metar(text) or {}
        if not f:
            continue
        rh = _humidity(f.get("temp_f"), f.get("dewpoint_f"))
        rows.append({
            "station": idx, "temp_f": f.get("temp_f"), "dewpoint_f": f.get("dewpoint_f"),
            "wind_dir_deg": f.get("wind_dir_deg"), "sky": f.get("sky_code", 0),
            "wind_mph": f.get("wind_speed_mph"), "gust_mph": f.get("wind_gust_mph") or 0,
            "visibility_mi": f.get("visibility_mi"), "pressure_inhg": f.get("pressure_inhg"),
            "humidity_pct": rh, "feels_delta_f": _feels_delta(f.get("temp_f"), rh, f.get("wind_speed_mph")),
        })
        if newest is None or ts > newest:
            newest = ts
    if not rows:
        return None
    return v5.encode_obs(seq, bot, ts_min=int(newest.timestamp() // 60), stations=rows)


# -- Forecast ---------------------------------------------------------------------------

_COND_THUNDER, _COND_FROST, _COND_FOG, _COND_HIGH_WIND = 0x01, 0x02, 0x04, 0x08
_COND_FREEZING_RAIN, _COND_HEAVY_SNOW = 0x10, 0x40


def forecast_message(seq: int, bot: int, store: WeatherStore, lat: float, lon: float) -> bytes | None:
    from meshcore_weather.core import services
    from meshcore_weather.parser.pfm import downsample_to_daily
    found = services.nearest_pfm_point(store, lat, lon)
    if found is None:
        return None
    pt, prod, _km = found
    daily = downsample_to_daily(pt, max_days=7)
    if not daily:
        return None
    periods = []
    for d in daily:
        e = d.to_encoder_dict()
        flags = int(e.get("condition_flags") or 0)
        periods.append({
            "high_f": None if e["high_f"] == 127 else e["high_f"],
            "low_f": None if e["low_f"] == 127 else e["low_f"],
            "pop_pct": e.get("precip_pct"), "sky": int(e.get("sky_code") or 0),
            "thunder": bool(flags & _COND_THUNDER),
            "wintry": bool(flags & (_COND_FROST | _COND_FREEZING_RAIN | _COND_HEAVY_SNOW)),
            "windy": bool(flags & _COND_HIGH_WIND), "fog": bool(flags & _COND_FOG),
            "wind_dir_deg": (int(e.get("wind_dir_nibble") or 0) * 22.5), "wind_mph": int(e.get("wind_speed_5mph") or 0) * 5,
        })
    issued = pt.issue_time or prod.timestamp
    first = int(daily[0].to_encoder_dict().get("period_id") or 0) * 2      # daily periods: day ids
    return v5.encode_forecast(seq, bot, point=tables.point_index(pt.lat, pt.lon),
                              issued_min=int(issued.timestamp() // 60), first_period=first, periods=periods[:14])


# -- Text and errors -------------------------------------------------------------------


def text_messages(seq: SeqCounter, bot: int, subject: int, text: str) -> list[bytes]:
    text = " ".join(text.split())
    for cut in (len(text), 1200, 1000, 800, 600, 400, 150):
        try:
            start = seq.next()
            msgs = v5.text_chunks(start, bot, subject=subject, text=text[:cut])
            for _ in msgs[1:]:
                seq.next()
            return msgs
        except ValueError:
            continue
    return []


def not_available(seq: int, bot: int, request: str, reason: int) -> bytes:
    return v5.encode_not_available(seq, bot, request=request[:1] or "?", reason=reason)
