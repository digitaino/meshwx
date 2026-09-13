"""Text renderings of core objects for the DM path.

Every function returns ONE string meant to fit a single MeshCore DM
(target <= 120 characters, hard cap 160). The same objects feed the
binary encoders, so what a human reads and what an app decodes come from
the same parse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from meshcore_weather.config import settings
from meshcore_weather.core.services import Forecast, Observation
from meshcore_weather.core.vtec_names import short_name

MAX_DM = 160

_SKY = {0: "clear", 1: "few", 2: "sct", 3: "bkn", 4: "ovc", 5: "fog", 6: "smoke",
        7: "haze", 8: "rain", 9: "snow", 10: "tstm", 11: "drzl", 12: "mist",
        13: "squall", 14: "sand", 15: "wx"}
_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return ZoneInfo("UTC")


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz())


def _clock(dt: datetime) -> str:
    """'4:45PM' in the bot's local zone."""
    l = _local(dt)
    return l.strftime("%I:%M%p").lstrip("0")


def _when(dt: datetime) -> str:
    """'4:45PM' if today, else 'Mon 4:45PM'."""
    l = _local(dt)
    today = datetime.now(timezone.utc).astimezone(_tz()).date()
    if l.date() == today:
        return _clock(dt)
    return f"{l.strftime('%a')} {_clock(dt)}"


def _dir(deg: int) -> str:
    return _DIRS[int((deg + 11.25) // 22.5) % 16]


def _cap(s: str) -> str:
    return s if len(s) <= MAX_DM else s[: MAX_DM - 1].rstrip() + "…"


def place_label(loc: dict) -> str:
    """'Round Rock, TX' plus a warning when the name was ambiguous."""
    name = loc.get("name", "?")
    amb = loc.get("ambiguous") or []
    if amb:
        return f"{name} (also {'/'.join(amb[:3])}; add state)"
    return name


def observation(loc: dict, ob: Observation | None) -> str:
    if ob is None:
        return _cap(f"{place_label(loc)}: no current obs within 2h of nearby stations")
    wind = "calm" if ob.wind_speed_mph == 0 else f"{_dir(ob.wind_dir_deg)}{ob.wind_speed_mph}"
    if ob.wind_gust_mph:
        wind += f"g{ob.wind_gust_mph}"
    hh, mm = divmod(ob.obs_utc_min, 60)
    obs_dt = datetime.now(timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return _cap(
        f"{place_label(loc)} {_clock(obs_dt)} ({ob.station} {ob.distance_km:.0f}km): "
        f"{ob.temp_f}F dp{ob.dewpoint_f} {wind} {_SKY.get(ob.sky_code, 'wx')} "
        f"{ob.visibility_mi}mi {ob.pressure_inhg:.2f}"
    )


def forecast(loc: dict, fc: Forecast | None, days: int = 5) -> str:
    if fc is None:
        return _cap(f"{place_label(loc)}: no forecast point within 80km")
    parts = []
    for i, p in enumerate(fc.periods[:days]):
        day = "?"
        if fc.start_date is not None:
            from datetime import timedelta
            day = (fc.start_date + timedelta(days=p["period_id"])).strftime("%a")
        seg = f"{day} {p['high_f']}/{p['low_f']}"
        if p.get("precip_pct", 0) >= 20:
            seg += f" {p['precip_pct']}%"
        sky = p.get("sky_code", 0)
        if sky >= 8:
            seg += f" {_SKY.get(sky, '')}"
        parts.append(seg)
    head = f"{place_label(loc)} ({fc.point_name.split('-')[0].strip()} {fc.distance_km:.0f}km):"
    return _cap(head + " " + " | ".join(parts))


def warnings(loc: dict, ws: list[dict]) -> str:
    if not ws:
        return _cap(f"No active warnings for {place_label(loc)}")
    items = []
    for w in ws:
        name = short_name(w.get("vtec_phenomenon"), w.get("vtec_significance"))
        exp = w.get("expires_at")
        onset = w.get("onset_at")
        now = datetime.now(timezone.utc)
        if onset and onset > now:
            items.append(f"{name} {_when(onset)}-{_when(exp)}")
        else:
            items.append(f"{name} til {_when(exp)}")
    head = f"{len(ws)} active, {place_label(loc)}: " if len(ws) > 1 else f"{place_label(loc)}: "
    return _cap(head + "; ".join(items))


def summary(loc: dict, ob: Observation | None, ws: list[dict], fc: Forecast | None) -> str:
    """The 'wx <place>' reply: warnings first, then obs, then tomorrow."""
    bits = []
    if ws:
        w = ws[0]
        bits.append(f"!{short_name(w.get('vtec_phenomenon'), w.get('vtec_significance'))} til {_when(w['expires_at'])}")
    if ob:
        wind = "calm" if ob.wind_speed_mph == 0 else f"{_dir(ob.wind_dir_deg)}{ob.wind_speed_mph}"
        if ob.wind_gust_mph:
            wind += f"g{ob.wind_gust_mph}"
        bits.append(f"{ob.temp_f}F dp{ob.dewpoint_f} {wind} {_SKY.get(ob.sky_code, 'wx')} ({ob.station} {ob.distance_km:.0f}km)")
    if fc and fc.periods:
        p = fc.periods[0]
        seg = f"{p['high_f']}/{p['low_f']}"
        if p.get("precip_pct", 0) >= 20:
            seg += f" {p['precip_pct']}%"
        bits.append(seg)
    if not bits:
        return _cap(f"{place_label(loc)}: no data yet")
    return _cap(f"{place_label(loc)}: " + " | ".join(bits))
