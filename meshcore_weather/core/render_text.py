"""Text renderings of core objects for the DM path.

Every function returns ONE string meant to fit a single MeshCore DM
(target <= 120 characters, hard cap 160). The same objects feed the
binary encoders, so what a human reads and what an app decodes come from
the same parse.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from meshcore_weather.config import settings
from meshcore_weather.core.services import Forecast, Observation
from meshcore_weather.core.vtec_names import short_name

# One MeshCore text message. The firmware clips a channel message at 160
# bytes of "name: text" (plus 5 bytes of header), so with a name like
# WX-AUS about 147 characters of text survive; a DM has a similar budget.
# Everything rendered here aims below that so nothing is ever cut mid-word.
MAX_DM = 147

_SKY = {0: "clear", 1: "few", 2: "sct", 3: "bkn", 4: "ovc", 5: "fog", 6: "smoke",
        7: "haze", 8: "rain", 9: "snow", 10: "tstm", 11: "drzl", 12: "mist",
        13: "squall", 14: "sand", 15: "wx"}
_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


# Times are shown in the place's own zone, not the bot's: a Texas bot
# answering "warn NY" says 8:00AM EDT, not 7:00AM. The abbreviation is
# added only when the zone differs from the bot's.
_STATE_TZ = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix", "AR": "America/Chicago",
    "CA": "America/Los_Angeles", "CO": "America/Denver", "CT": "America/New_York", "DE": "America/New_York",
    "DC": "America/New_York", "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
    "ID": "America/Boise", "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis", "IA": "America/Chicago",
    "KS": "America/Chicago", "KY": "America/New_York", "LA": "America/Chicago", "ME": "America/New_York",
    "MD": "America/New_York", "MA": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
    "MS": "America/Chicago", "MO": "America/Chicago", "MT": "America/Denver", "NE": "America/Chicago",
    "NV": "America/Los_Angeles", "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver",
    "NY": "America/New_York", "NC": "America/New_York", "ND": "America/Chicago", "OH": "America/New_York",
    "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago",
    "UT": "America/Denver", "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles",
    "WV": "America/New_York", "WI": "America/Chicago", "WY": "America/Denver", "PR": "America/Puerto_Rico",
    "VI": "America/Puerto_Rico", "GU": "Pacific/Guam", "MP": "Pacific/Guam", "AS": "Pacific/Pago_Pago",
}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return ZoneInfo("UTC")


def tz_for_state(state: str | None) -> ZoneInfo:
    name = _STATE_TZ.get((state or "").upper())
    if not name:
        return _tz()
    try:
        return ZoneInfo(name)
    except Exception:
        return _tz()


def tz_for_loc(loc: dict | None) -> ZoneInfo:
    zones = (loc or {}).get("zones") or []
    return tz_for_state(zones[0][:2]) if zones else _tz()


def _local(dt: datetime, tz: ZoneInfo | None = None) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz or _tz())


def _clock(dt: datetime, tz: ZoneInfo | None = None) -> str:
    """'4:45PM' in the given zone (bot's zone by default), with the zone's
    abbreviation when it is not the bot's own."""
    l = _local(dt, tz)
    out = l.strftime("%I:%M%p").lstrip("0")
    if tz is not None and tz.key != _tz().key:
        out += " " + l.strftime("%Z")
    return out


def _when(dt: datetime, tz: ZoneInfo | None = None) -> str:
    """'4:45PM' if today, else 'Mon 4:45PM'."""
    l = _local(dt, tz)
    today = datetime.now(timezone.utc).astimezone(tz or _tz()).date()
    if l.date() == today:
        return _clock(dt, tz)
    return f"{l.strftime('%a')} {_clock(dt, tz)}"


def _dir(deg: int) -> str:
    return _DIRS[int((deg + 11.25) // 22.5) % 16]


def _cap(s: str) -> str:
    return s if len(s) <= MAX_DM else s[: MAX_DM - 1].rstrip() + "…"


def _title(name: str | None) -> str:
    """'HEAT ADV' -> 'Heat Adv'; keeps short all-caps tokens like 'SVR' readable."""
    if not name:
        return "?"
    return " ".join(w.capitalize() for w in name.split())


def fit_list(head: str, items: list[str], cap: int = MAX_DM, sep: str = "; ", tail: str = "") -> str:
    """head + as many items as fit in `cap`, then ' +N more'. Never cuts an item."""
    out = head
    used = 0
    for i, item in enumerate(items):
        piece = ("" if i == 0 else sep) + item
        rest = len(items) - i - 1
        suffix = f" +{rest + 1} more" if rest >= 0 else ""
        # Would this item fit, allowing for a '+N more' note if it is not the last?
        need = len(out) + len(piece) + (len(f" +{rest} more") if rest else 0) + len(tail)
        if need > cap:
            break
        out += piece
        used += 1
    if used < len(items):
        out += f" +{len(items) - used} more"
    return out + tail


def _group(items: list[str]) -> list[str]:
    """Collapse repeats in order: [a, b, a] -> ['a x2', 'b']."""
    counts: dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    return [f"{k} x{n}" if n > 1 else k for k, n in counts.items()]


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
        f"{place_label(loc)} {_clock(obs_dt, tz_for_loc(loc))} ({ob.station} {ob.distance_km:.0f}km): "
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
    tz = tz_for_loc(loc)
    for w in ws:
        name = _title(short_name(w.get("vtec_phenomenon"), w.get("vtec_significance")))
        exp = w.get("expires_at")
        onset = w.get("onset_at")
        now = datetime.now(timezone.utc)
        if onset and onset > now:
            items.append(f"{name} {_when(onset, tz)}-{_when(exp, tz)}")
        else:
            items.append(f"{name} til {_when(exp, tz)}")
    head = f"{len(ws)} active, {place_label(loc)}: " if len(ws) > 1 else f"{place_label(loc)}: "
    return fit_list(head, items)


def summary(loc: dict, ob: Observation | None, ws: list[dict], fc: Forecast | None) -> str:
    """The 'wx <place>' reply: warnings first, then obs, then tomorrow."""
    bits = []
    if ws:
        w = ws[0]
        bits.append(f"!{_title(short_name(w.get('vtec_phenomenon'), w.get('vtec_significance')))} til {_when(w['expires_at'], tz_for_loc(loc))}")
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


# -- Request-only products ------------------------------------------------------------


def outlook(loc: dict, ol) -> str:
    if ol is None:
        return _cap(f"{place_label(loc)}: no hazardous weather outlook on file")
    age = max(0, int((datetime.now(timezone.utc) - ol.issued_at).total_seconds() / 3600))
    body = ol.summary_text().replace("\n", " ")
    return _cap(f"{place_label(loc)} HWO ({age}h ago): {body}")


_LSR_SHORT = {
    "Non-Tstm Wnd Gst": "Wind", "Tstm Wnd Gst": "T-Wind", "Tstm Wnd Dmg": "Wind Dmg",
    "Funnel Cloud": "Funnel", "Flash Flood": "FlashFld", "Heavy Rain": "HvyRain",
    "Marine Tstm Wind": "Marine Wind", "Non-Tstm Wnd Dmg": "Wind Dmg",
}
_LSR_DIST_RE = re.compile(r"^\d+\s+[NSEW]{1,3}\s+", re.I)


def storm_reports(label: str, sr, state: str | None = None) -> str:
    """'16 storm reports NY: FlashFld Little Falls, Paterson NJ x3; Hail 1.00 Albany +9 more'."""
    if sr is None or not sr.entries:
        return _cap(f"No storm reports {label}")
    items = []
    for e in sr.entries:
        ev = e["event"]
        for k, v in _LSR_SHORT.items():
            ev = ev.replace(k, v)
        town = _LSR_DIST_RE.sub("", " ".join(e["location"].split())).strip().title()
        st = (e.get("state") or "").strip().upper()
        where = f"{town} {st}" if st and state and st != state.upper() else town
        mag = f" {' '.join(str(e['mag']).split())}" if str(e.get("mag") or "").strip() else ""
        items.append(f"{ev}{mag} {where}")
    return fit_list(f"{len(sr.entries)} storm reports {label}: ", _group(items))


def nowcast(loc: dict, nc) -> str:
    if nc is None:
        return _cap(f"{place_label(loc)}: no short-term forecast on file")
    return _cap(f"{place_label(loc)} NOW ({nc.wfo}): " + " ".join(nc.body().split()))


def raw_metar(loc: dict, found) -> str:
    if not found:
        return _cap(f"{place_label(loc)}: no METAR within 2h from nearby stations")
    icao, km, line = found
    return _cap(f"METAR {line}" if km == 0 else f"METAR ({icao} {km:.0f}km) {line}")


def taf(loc: dict, tf) -> str:
    if tf is None:
        return _cap(f"{place_label(loc)}: no TAF for nearby stations")
    head = "" if tf.distance_km == 0 else f"({tf.station} {tf.distance_km:.0f}km) "
    return _cap(head + tf.text)


def rain(label: str, ro) -> str:
    if ro is None or not ro.cities:
        return _cap(f"No rain reported {label}")
    items = [f"{c['name'].title()} {c['rain_text'].lower()} {c['temp_f']}F" for c in ro.cities]
    return fit_list(f"Rain {label} ({len(ro.cities)}): ", items)
