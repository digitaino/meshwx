"""Convert the bot's parsed weather data into MeshWX v2 binary messages.

Each function takes the existing Python dicts/strings that the bot's text
commands already produce and turns them into compact binary for broadcast.
"""

import logging
import re
from datetime import datetime, timezone

from meshcore_weather.protocol.meshwx import (
    LOC_ZONE, LOC_STATION,
    SKY_CLEAR, SKY_FEW, SKY_SCATTERED, SKY_BROKEN, SKY_OVERCAST,
    SKY_FOG, SKY_SMOKE, SKY_HAZE, SKY_RAIN, SKY_SNOW,
    SKY_THUNDERSTORM, SKY_DRIZZLE, SKY_MIST, SKY_OTHER,
    HAZARD_THUNDERSTORM, HAZARD_SEVERE_THUNDER, HAZARD_TORNADO,
    HAZARD_FLOOD, HAZARD_FLASH_FLOOD, HAZARD_EXCESSIVE_HEAT,
    HAZARD_WINTER_STORM, HAZARD_BLIZZARD, HAZARD_ICE,
    HAZARD_HIGH_WIND, HAZARD_FIRE_WEATHER, HAZARD_DENSE_FOG,
    HAZARD_RIP_CURRENT, HAZARD_HURRICANE, HAZARD_MARINE, HAZARD_OTHER,
    RISK_SLIGHT, RISK_LIMITED, RISK_ENHANCED, RISK_MODERATE, RISK_HIGH,
    EVENT_TORNADO, EVENT_FUNNEL, EVENT_HAIL, EVENT_WIND_DAMAGE,
    EVENT_NON_TSTM_WIND, EVENT_TSTM_WIND, EVENT_FLOOD, EVENT_FLASH_FLOOD,
    EVENT_HEAVY_RAIN, EVENT_SNOW, EVENT_ICE, EVENT_LIGHTNING, EVENT_OTHER,
    RAIN_LIGHT, RAIN_MODERATE, RAIN_HEAVY, RAIN_SHOWER, RAIN_TSTORM,
    RAIN_DRIZZLE, RAIN_SNOW, RAIN_FREEZING,
    pack_observation,
    pack_forecast,
    pack_outlook,
    pack_storm_reports,
    pack_rain_obs,
    pack_warning_zones,
    pack_fire_weather,
    pack_daily_climate,
    pack_nowcast,
    wind_dir_to_nibble,
)

logger = logging.getLogger(__name__)


# -- Sky condition mapping --

# Free-text weather words → sky code. Longer/more specific matches first.
_SKY_KEYWORDS = [
    ("thunderstorm", SKY_THUNDERSTORM),
    ("tstorm", SKY_THUNDERSTORM),
    ("tstm", SKY_THUNDERSTORM),
    ("snow", SKY_SNOW),
    ("drizzle", SKY_DRIZZLE),
    ("rain", SKY_RAIN),
    ("shower", SKY_RAIN),
    ("mist", SKY_MIST),
    ("fog", SKY_FOG),
    ("haze", SKY_HAZE),
    ("smoke", SKY_SMOKE),
    ("overcast", SKY_OVERCAST),
    ("ovc", SKY_OVERCAST),
    ("cloudy", SKY_OVERCAST),
    ("mocldy", SKY_OVERCAST),
    ("broken", SKY_BROKEN),
    ("bkn", SKY_BROKEN),
    ("ptcldy", SKY_BROKEN),
    ("scattered", SKY_SCATTERED),
    ("sct", SKY_SCATTERED),
    ("few", SKY_FEW),
    ("ptsunny", SKY_FEW),
    ("mosunny", SKY_FEW),
    ("sunny", SKY_CLEAR),
    ("clear", SKY_CLEAR),
    ("clr", SKY_CLEAR),
    ("fair", SKY_CLEAR),
]


def classify_sky(text: str) -> int:
    """Infer a sky code from free-form weather text."""
    if not text:
        return SKY_OTHER
    t = text.lower()
    for keyword, code in _SKY_KEYWORDS:
        if keyword in t:
            return code
    return SKY_OTHER


# -- METAR observation encoding --

def encode_metar(
    station_icao: str,
    metar_text: str,
    ts_minutes_utc: int,
    loc_type: int | None = None,
    loc_id=None,
) -> bytes | None:
    """Build a 0x30 observation from a raw METAR string.

    Example METAR: "KAUS 082151Z 17010KT 10SM SCT040 BKN070 28/18 A3010"

    If loc_type/loc_id are provided, uses them as the response location
    instead of LOC_STATION/station_icao (for echoing LOC_PFM_POINT back
    to the client on on-demand requests).
    """
    try:
        parts = metar_text.split()
    except AttributeError:
        return None

    temp_f = None
    dewpoint_f = None
    wind_dir_deg = None
    wind_speed_mph = 0
    wind_gust_mph = 0
    visibility_mi = 10
    sky_code = SKY_CLEAR
    pressure_inhg = 29.92

    # Stop at RMK — everything after is remarks, not weather data.
    # Tokens like DSNT (distant) contain "SN" and cause false snow matches.
    try:
        rmk_idx = parts.index("RMK")
        parts = parts[:rmk_idx]
    except ValueError:
        pass

    for p in parts:
        # Wind: dddffKT or dddffGggKT (dir in degrees, speed in knots)
        m_wind = re.match(r"^(\d{3})(\d{2,3})(?:G(\d{2,3}))?KT$", p)
        if m_wind:
            wind_dir_deg = int(m_wind.group(1))
            wind_speed_mph = round(int(m_wind.group(2)) * 1.15078)
            if m_wind.group(3):
                wind_gust_mph = round(int(m_wind.group(3)) * 1.15078)
            continue
        # Variable wind: VRBxxKT
        m_vrb = re.match(r"^VRB(\d{2,3})KT$", p)
        if m_vrb:
            wind_dir_deg = 0
            wind_speed_mph = round(int(m_vrb.group(1)) * 1.15078)
            continue
        # Visibility: NNSM or N/NSM
        m_vis = re.match(r"^(\d{1,2})(?:/(\d))?SM$", p)
        if m_vis:
            visibility_mi = int(m_vis.group(1))
            continue
        # Temp/dewpoint: TT/DD or MTT/DD (M = negative)
        m_td = re.match(r"^(M?\d{2})/(M?\d{2})$", p)
        if m_td:
            t_c = int(m_td.group(1).replace("M", "-"))
            d_c = int(m_td.group(2).replace("M", "-"))
            temp_f = round(t_c * 9 / 5 + 32)
            dewpoint_f = round(d_c * 9 / 5 + 32)
            continue
        # Altimeter: AXXXX (inches of Hg * 100)
        m_alt = re.match(r"^A(\d{4})$", p)
        if m_alt:
            pressure_inhg = int(m_alt.group(1)) / 100
            continue
        # Sky condition
        for code, sky in [("CLR", SKY_CLEAR), ("SKC", SKY_CLEAR),
                          ("FEW", SKY_FEW), ("SCT", SKY_SCATTERED),
                          ("BKN", SKY_BROKEN), ("OVC", SKY_OVERCAST)]:
            if p.startswith(code):
                if sky > sky_code:  # pick worst conditions for summary
                    sky_code = sky
                break
        # Present weather codes: standalone or combined (e.g. TS, RA, SN,
        # -RA, +SN, TSRA, RASN).  Match as regex to avoid substring false
        # positives (e.g. DSNT matching "SN").
        if re.match(r"^[+-]?(?:VC)?(?:TS|SH|FZ|MI|PR|BC|DR|BL)*(?:RA|SN|DZ|GR|GS|PL|IC|SG|UP)+(?:RA|SN|DZ|GR|GS|PL|IC|SG|UP)*$", p):
            if "TS" in p:
                sky_code = SKY_THUNDERSTORM
            elif "SN" in p and sky_code < SKY_SNOW:
                sky_code = SKY_SNOW
            elif "RA" in p and sky_code < SKY_RAIN:
                sky_code = SKY_RAIN
            elif "DZ" in p and sky_code < SKY_DRIZZLE:
                sky_code = SKY_DRIZZLE
        elif p in ("TS", "+TS", "-TS", "VCTS"):
            sky_code = SKY_THUNDERSTORM
        elif re.match(r"^[+-]?(?:VC)?(?:FG|BR|HZ|FU|SA|DU)$", p):
            if "FG" in p and sky_code < SKY_FOG:
                sky_code = SKY_FOG
            elif "HZ" in p and sky_code < SKY_HAZE:
                sky_code = SKY_HAZE
            elif "FU" in p and sky_code < SKY_SMOKE:
                sky_code = SKY_SMOKE

    if temp_f is None:
        return None

    return pack_observation(
        loc_type if loc_type is not None else LOC_STATION,
        loc_id if loc_id is not None else station_icao,
        timestamp_utc_min=ts_minutes_utc,
        temp_f=temp_f,
        dewpoint_f=dewpoint_f if dewpoint_f is not None else temp_f,
        wind_dir_deg=wind_dir_deg if wind_dir_deg is not None else 0,
        sky_code=sky_code,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
        visibility_mi=visibility_mi,
        pressure_inhg=pressure_inhg,
    )


# -- RWR (Regional Weather Roundup) observation encoding --

# Column positions in a typical RWR line after the city:
# SKY/WX  TMP DP RH  WIND    PRES  RMK
# "SUNNY  85  55 40  S10     30.05"
_RWR_SPLIT_RE = re.compile(r"\s+")


def encode_rwr_city(
    zone_code: str,
    rwr_line: str,
    ts_minutes_utc: int,
    loc_type: int | None = None,
    loc_id=None,
) -> bytes | None:
    """Build a 0x30 observation from an RWR line for a specific city.

    The caller is responsible for finding the correct line — we parse it.

    If loc_type/loc_id are provided, uses them as the response location
    instead of LOC_ZONE/zone_code (for echoing LOC_PFM_POINT back to
    the client on on-demand requests).
    """
    if not rwr_line:
        return None
    parts = _RWR_SPLIT_RE.split(rwr_line.strip())
    if len(parts) < 4:
        return None

    # Find first numeric (temp). Everything before it is the sky/wx description.
    temp_idx = None
    for i, p in enumerate(parts):
        if p.lstrip("-").isdigit():
            temp_idx = i
            break
    if temp_idx is None or temp_idx + 1 >= len(parts):
        return None

    try:
        temp_f = int(parts[temp_idx])
        dewpoint_f = int(parts[temp_idx + 1])
    except (ValueError, IndexError):
        return None

    sky_text = " ".join(parts[:temp_idx])
    sky_code = classify_sky(sky_text)

    # Wind: find something like S10 or SE15G25 or CALM
    wind_dir_deg = 0
    wind_speed_mph = 0
    wind_gust_mph = 0
    for p in parts[temp_idx + 2:]:
        if p.upper() == "CALM":
            break
        m = re.match(r"^([NESW]{1,3})(\d+)(?:G(\d+))?$", p.upper())
        if m:
            dir_map = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
                       "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
                       "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
                       "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}
            wind_dir_deg = int(dir_map.get(m.group(1), 0))
            wind_speed_mph = int(m.group(2))
            if m.group(3):
                wind_gust_mph = int(m.group(3))
            break

    return pack_observation(
        loc_type if loc_type is not None else LOC_ZONE,
        loc_id if loc_id is not None else zone_code,
        timestamp_utc_min=ts_minutes_utc,
        temp_f=temp_f,
        dewpoint_f=dewpoint_f,
        wind_dir_deg=wind_dir_deg,
        sky_code=sky_code,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
    )


# -- Forecast period encoding --

# Period name → period_id mapping
_PERIOD_IDS = {
    "TONIGHT": 0,
    "TODAY": 1,
    "THIS AFTERNOON": 14,
    "THIS EVENING": 15,
    "LATE TONIGHT": 16,
    "TOMORROW": 2,
    "TOMORROW NIGHT": 3,
    "MONDAY": 4,
    "MONDAY NIGHT": 5,
    "TUESDAY": 6,
    "TUESDAY NIGHT": 7,
    "WEDNESDAY": 8,
    "WEDNESDAY NIGHT": 9,
    "THURSDAY": 10,
    "THURSDAY NIGHT": 11,
    "FRIDAY": 12,
    "FRIDAY NIGHT": 13,
    "SATURDAY": 4,
    "SATURDAY NIGHT": 5,
    "SUNDAY": 6,
    "SUNDAY NIGHT": 7,
}


def _extract_temp(text: str, pattern: str) -> int | None:
    """Extract a temperature from text like 'HIGHS AROUND 85' or 'LOWS IN THE UPPER 60S'."""
    m = re.search(pattern + r".*?(\d{2,3})", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _extract_wind(text: str) -> tuple[int, int]:
    """Return (wind_dir_nibble, wind_speed_5mph_units) from period text."""
    # Example: "Southeast winds 5 to 10 mph" or "WEST WINDS 10 TO 15 MPH"
    m = re.search(
        r"(north|south|east|west|northeast|northwest|southeast|southwest)\s+winds?\s+(\d+)(?:\s+to\s+(\d+))?\s+mph",
        text,
        re.IGNORECASE,
    )
    if not m:
        return 0, 0
    dir_map = {
        "n": 0, "north": 0, "ne": 2, "northeast": 2,
        "e": 4, "east": 4, "se": 6, "southeast": 6,
        "s": 8, "south": 8, "sw": 10, "southwest": 10,
        "w": 12, "west": 12, "nw": 14, "northwest": 14,
    }
    direction = m.group(1).lower()
    speed = int(m.group(3) or m.group(2))  # use upper bound if range
    return (
        dir_map.get(direction, 0),
        min(15, speed // 5),
    )


def _extract_precip(text: str) -> int:
    """Extract precipitation chance from text like '40 percent chance of...'"""
    m = re.search(r"(\d{1,3})\s*percent", text, re.IGNORECASE)
    if m:
        return min(100, int(m.group(1)))
    # Fall back: check for precip keywords
    t = text.lower()
    if "slight chance" in t:
        return 15
    if "chance" in t:
        return 40
    if "likely" in t:
        return 70
    if "occasional" in t or "scattered showers" in t:
        return 50
    return 0


def encode_forecast_from_zfp(
    zone_code: str,
    zfp_text: str,
    issued_hours_ago: int,
    loc_type: int | None = None,
    loc_id=None,
) -> bytes | None:
    """Parse a ZFP zone forecast and encode it as a 0x31 forecast message.

    ZFP format has periods like:
        .TONIGHT...Mostly clear. Lows around 60. Southeast winds around 5 mph.
        .THURSDAY...Sunny. Highs in the upper 80s. ...

    `zone_code` is always used to LOOK UP the forecast text from the ZFP.
    `loc_type`/`loc_id` let the caller control what location reference
    appears in the ENCODED response — defaults to (LOC_ZONE, zone_code) so
    existing zone-based requests are unchanged, but a LOC_PFM_POINT request
    can ask the encoder to echo back the PFM point index instead so the
    client can correlate the broadcast with its original request.
    """
    if not zfp_text:
        return None

    periods = []
    current_name = None
    current_text: list[str] = []

    for line in zfp_text.splitlines():
        s = line.strip()
        if not s:
            if current_name and current_text:
                periods.append((current_name, " ".join(current_text)))
                current_name = None
                current_text = []
            continue
        if s.startswith(".") and "..." in s:
            # Flush previous
            if current_name and current_text:
                periods.append((current_name, " ".join(current_text)))
            # Extract period name between first . and first ...
            head, _, rest = s.lstrip(".").partition("...")
            current_name = head.strip().upper()
            current_text = [rest.strip()] if rest.strip() else []
        elif current_name:
            current_text.append(s)

    if current_name and current_text:
        periods.append((current_name, " ".join(current_text)))

    if not periods:
        return None

    encoded_periods = []
    for name, text in periods[:7]:  # up to 7 periods
        period_id = _PERIOD_IDS.get(name, 0)
        sky_code = classify_sky(text)

        is_night = "NIGHT" in name or name == "TONIGHT"
        if is_night:
            high_f = 127  # N/A
            low_f = _extract_temp(text, r"lows?")
        else:
            high_f = _extract_temp(text, r"highs?")
            low_f = _extract_temp(text, r"lows?")

        wind_dir, wind_speed = _extract_wind(text)
        precip = _extract_precip(text)

        flags = 0
        t_lower = text.lower()
        if "thunder" in t_lower:
            flags |= 0x01
        if "frost" in t_lower:
            flags |= 0x02
        if "fog" in t_lower:
            flags |= 0x04
        if "high wind" in t_lower:
            flags |= 0x08
        if "freezing rain" in t_lower or "sleet" in t_lower:
            flags |= 0x10
        if "heavy rain" in t_lower:
            flags |= 0x20
        if "heavy snow" in t_lower:
            flags |= 0x40

        encoded_periods.append({
            "period_id": period_id,
            "high_f": high_f if high_f is not None else 127,
            "low_f": low_f if low_f is not None else 127,
            "sky_code": sky_code,
            "precip_pct": precip,
            "wind_dir_nibble": wind_dir,
            "wind_speed_5mph": wind_speed,
            "condition_flags": flags,
        })

    # Default location reference is the zone itself; caller can override
    # (e.g. LOC_PFM_POINT with the pfm index so the client can correlate).
    if loc_type is None:
        loc_type = LOC_ZONE
        loc_id = zone_code
    return pack_forecast(
        loc_type, loc_id,
        issued_hours_ago=issued_hours_ago,
        periods=encoded_periods,
    )


def now_utc_minutes() -> int:
    """Minutes since midnight UTC (uint16)."""
    now = datetime.now(timezone.utc)
    return now.hour * 60 + now.minute


# -- Place lookup (uses resolver.places for nearest match) --

def find_nearest_place_id(lat: float, lon: float, max_km: float = 50) -> int | None:
    """Find the index into resolver._places closest to a lat/lon point.

    Returns None if no place within max_km.
    """
    from meshcore_weather.geodata import resolver
    resolver.load()
    import math

    best_d = float("inf")
    best_idx = None
    for i, p in enumerate(resolver._places):
        dlat = lat - p[2]
        dlon = lon - p[3]
        # Quick squared distance (fine for nearest within small area)
        d2 = dlat * dlat + dlon * dlon
        if d2 < best_d:
            best_d = d2
            best_idx = i

    if best_idx is None:
        return None
    # Convert squared to rough km (1 degree ≈ 111 km)
    approx_km = math.sqrt(best_d) * 111
    if approx_km > max_km:
        return None
    return best_idx


def find_place_id_by_name(name: str, state: str = "") -> int | None:
    """Find the index of a place by name + optional state."""
    from meshcore_weather.geodata import resolver
    resolver.load()
    name_upper = name.upper().strip()
    state_upper = state.upper().strip() if state else ""
    for i, p in enumerate(resolver._places):
        if p[0].upper() == name_upper:
            if not state_upper or p[1] == state_upper:
                return i
    return None


# -- HWO (Hazardous Weather Outlook) encoding --

# Map NWS HWO hazard text → hazard code
_HAZARD_KEYWORDS = [
    ("tornado", HAZARD_TORNADO),
    ("severe thunderstorm", HAZARD_SEVERE_THUNDER),
    ("severe thunder", HAZARD_SEVERE_THUNDER),
    ("severe storm", HAZARD_SEVERE_THUNDER),
    ("severe weather", HAZARD_SEVERE_THUNDER),
    ("flash flood", HAZARD_FLASH_FLOOD),
    ("flood", HAZARD_FLOOD),
    ("excessive heat", HAZARD_EXCESSIVE_HEAT),
    ("heat", HAZARD_EXCESSIVE_HEAT),
    ("blizzard", HAZARD_BLIZZARD),
    ("winter storm", HAZARD_WINTER_STORM),
    ("winter weather", HAZARD_WINTER_STORM),
    ("snow", HAZARD_WINTER_STORM),
    ("ice", HAZARD_ICE),
    ("freezing rain", HAZARD_ICE),
    ("high wind", HAZARD_HIGH_WIND),
    ("wind", HAZARD_HIGH_WIND),
    ("fire weather", HAZARD_FIRE_WEATHER),
    ("fire", HAZARD_FIRE_WEATHER),
    ("dense fog", HAZARD_DENSE_FOG),
    ("fog", HAZARD_DENSE_FOG),
    ("rip current", HAZARD_RIP_CURRENT),
    ("hurricane", HAZARD_HURRICANE),
    ("tropical", HAZARD_HURRICANE),
    ("marine", HAZARD_MARINE),
    ("thunderstorm", HAZARD_THUNDERSTORM),
]

# Risk level text → code
_RISK_KEYWORDS = {
    "extreme": RISK_HIGH,
    "high": RISK_HIGH,
    "moderate": RISK_MODERATE,
    "enhanced": RISK_ENHANCED,
    "elevated": RISK_ENHANCED,
    "limited": RISK_LIMITED,
    "slight": RISK_SLIGHT,
    "low": RISK_SLIGHT,
}


def _classify_hazards(text: str) -> list[tuple[int, int]]:
    """Extract (hazard_code, risk_level) tuples from HWO section text."""
    t = text.lower()
    found = {}

    # Look for explicit RISK... patterns per section
    risk_matches = re.finditer(r"risk[.\s]+(\w+)", t, re.IGNORECASE)
    risks_in_text = []
    for m in risk_matches:
        word = m.group(1).lower()
        if word in _RISK_KEYWORDS:
            risks_in_text.append(_RISK_KEYWORDS[word])

    # Default risk from first mention, fall back to slight if hazard present
    default_risk = risks_in_text[0] if risks_in_text else RISK_SLIGHT

    # Find hazard mentions
    for keyword, code in _HAZARD_KEYWORDS:
        if keyword in t and code not in found:
            found[code] = default_risk

    return list(found.items())


def encode_hwo(
    zone_code: str,
    hwo_text: str,
    issued_utc_min: int,
) -> bytes | None:
    """Parse an HWO product text and encode as 0x32 outlook."""
    if not hwo_text:
        return None

    # Split into .DAY ONE / .DAYS TWO THROUGH SEVEN sections
    lines = hwo_text.splitlines()
    sections: dict[int, str] = {}  # day_offset -> text
    current_day = None
    current_text: list[str] = []

    for line in lines:
        s = line.strip()
        up = s.upper()
        if up.startswith(".DAY ONE") or up.startswith(".DAY 1"):
            if current_day is not None:
                sections[current_day] = " ".join(current_text)
            current_day = 1
            current_text = []
            continue
        if "DAYS TWO THROUGH SEVEN" in up or "DAYS 2" in up:
            if current_day is not None:
                sections[current_day] = " ".join(current_text)
            current_day = 2  # represents day range 2-7
            current_text = []
            continue
        if up.startswith(".SPOTTER") or s.startswith("$$"):
            break
        if current_day is not None and s:
            if not (s.startswith("&&") or re.match(r"^[A-Z]{2}Z\d{3}", s)):
                current_text.append(s)

    if current_day is not None and current_text:
        sections[current_day] = " ".join(current_text)

    if not sections:
        return None

    days = []
    for day_offset, text in sorted(sections.items()):
        hazards = _classify_hazards(text)
        if day_offset == 1:
            days.append({"day_offset": 1, "hazards": hazards})
        else:
            # "Days 2-7" gets replicated as day 2 with the same hazards
            # (rough but practical; clients can display as "Days 2-7")
            days.append({"day_offset": 2, "hazards": hazards})

    if not days:
        return None

    return pack_outlook(LOC_ZONE, zone_code, issued_utc_min, days)


# -- LSR (Local Storm Reports) encoding --

_LSR_EVENT_MAP = {
    "tornado": EVENT_TORNADO,
    "funnel cloud": EVENT_FUNNEL,
    "funnel": EVENT_FUNNEL,
    "hail": EVENT_HAIL,
    "tstm wnd dmg": EVENT_WIND_DAMAGE,
    "thunderstorm wind damage": EVENT_WIND_DAMAGE,
    "tstm wnd gst": EVENT_TSTM_WIND,
    "thunderstorm wind": EVENT_TSTM_WIND,
    "non-tstm wnd gst": EVENT_NON_TSTM_WIND,
    "high wind": EVENT_NON_TSTM_WIND,
    "flash flood": EVENT_FLASH_FLOOD,
    "flood": EVENT_FLOOD,
    "heavy rain": EVENT_HEAVY_RAIN,
    "snow": EVENT_SNOW,
    "ice storm": EVENT_ICE,
    "sleet": EVENT_ICE,
    "lightning": EVENT_LIGHTNING,
}


def _classify_lsr_event(event_text: str) -> int:
    """Map an LSR event string to an event code."""
    t = event_text.lower().strip()
    for keyword, code in _LSR_EVENT_MAP.items():
        if keyword in t:
            return code
    return EVENT_OTHER


def _parse_lsr_magnitude(mag_str: str, event_type: int) -> int:
    """Parse an LSR magnitude string into a single byte.

    Hail: size in inches * 4 (so 1.5 inch → 6)
    Wind: mph directly
    Rain: 0.01 in units (so 2.5" → 250, clamped)
    """
    if not mag_str:
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)", mag_str)
    if not m:
        return 0
    value = float(m.group(1))
    if event_type == EVENT_HAIL:
        return min(255, int(round(value * 4)))
    if event_type in (EVENT_TSTM_WIND, EVENT_NON_TSTM_WIND, EVENT_WIND_DAMAGE):
        return min(255, int(round(value)))
    if event_type in (EVENT_HEAVY_RAIN, EVENT_FLASH_FLOOD, EVENT_FLOOD):
        return min(255, int(round(value * 100 / 10)))  # coarse inches
    return min(255, int(round(value)))


def encode_lsr_reports(
    zone_code: str,
    lsr_entries: list[dict],
    now_min: int,
) -> bytes | None:
    """Convert a list of LSR entries (from _parse_lsr_entries) to a 0x33 message.

    Entries come from weather.py's _parse_lsr_entries and have:
      time, event, location (text), mag, state
    """
    if not lsr_entries:
        return None

    from meshcore_weather.geodata import resolver
    resolver.load()

    reports = []
    for entry in lsr_entries[:16]:  # cap for size
        event_type = _classify_lsr_event(entry.get("event", ""))
        magnitude = _parse_lsr_magnitude(entry.get("mag", ""), event_type)

        # Extract a place name from the location text.
        # Location text looks like "5 S Hoover" or "8 NNE Austin"
        loc_text = entry.get("location", "")
        name_match = re.search(r"([A-Z][A-Za-z][A-Za-z\s]+?)$", loc_text)
        place_id = 0
        if name_match:
            place_name = name_match.group(1).strip()
            state = entry.get("state", "")
            pid = find_place_id_by_name(place_name, state)
            if pid is not None:
                place_id = pid

        # Parse event time like "1249 PM" into minutes ago (rough)
        time_str = entry.get("time", "")
        minutes_ago = 0
        m_time = re.match(r"(\d{4})\s+([AP]M)", time_str)
        if m_time:
            hhmm = int(m_time.group(1))
            hour = hhmm // 100
            minute = hhmm % 100
            if m_time.group(2) == "PM" and hour != 12:
                hour += 12
            elif m_time.group(2) == "AM" and hour == 12:
                hour = 0
            report_minutes = hour * 60 + minute
            diff = now_min - report_minutes
            minutes_ago = diff % 1440  # handle rollover

        reports.append({
            "event_type": event_type,
            "magnitude": magnitude,
            "minutes_ago": minutes_ago,
            "place_id": place_id,
        })

    if not reports:
        return None
    return pack_storm_reports(LOC_ZONE, zone_code, reports)


# -- RWR rain city list → 0x34 --

_RAIN_TYPE_MAP = {
    "lgt rain": RAIN_LIGHT,
    "light rain": RAIN_LIGHT,
    "rain": RAIN_MODERATE,
    "hvy rain": RAIN_HEAVY,
    "heavy rain": RAIN_HEAVY,
    "showers": RAIN_SHOWER,
    "shower": RAIN_SHOWER,
    "tstorm": RAIN_TSTORM,
    "t-storm": RAIN_TSTORM,
    "drizzle": RAIN_DRIZZLE,
    "snow": RAIN_SNOW,
    "fz rain": RAIN_FREEZING,
    "frz rain": RAIN_FREEZING,
}


def _classify_rain(text: str) -> int:
    t = text.lower()
    for keyword, code in _RAIN_TYPE_MAP.items():
        if keyword in t:
            return code
    return RAIN_LIGHT


def encode_rain_cities(
    region_zone: str,
    rainy_cities: list[dict],
    now_min: int,
) -> bytes | None:
    """Convert a list of rainy city observations to a 0x34 message.

    rainy_cities: list of {"name": str, "state": str, "rain_text": str, "temp_f": int}
    """
    if not rainy_cities:
        return None

    cities = []
    for c in rainy_cities[:20]:  # cap
        place_id = find_place_id_by_name(c["name"], c.get("state", ""))
        if place_id is None:
            continue
        cities.append({
            "place_id": place_id,
            "rain_type": _classify_rain(c.get("rain_text", "")),
            "temp_f": int(c.get("temp_f", 60)),
        })

    if not cities:
        return None
    return pack_rain_obs(LOC_ZONE, region_zone, now_min, cities)


# -- Warning → zone-coded (0x21) from NWS warning product --

def encode_warning_zones(
    warning_type: int,
    severity: int,
    expires_unix_min: int,
    zones: list[str],
    headline: str,
) -> bytes:
    """Thin wrapper around pack_warning_zones for symmetry with polygon encoder."""
    return pack_warning_zones(warning_type, severity, expires_unix_min, zones, headline)


# -- TAF (0x36) — Terminal Aerodrome Forecast → snapshot --

# TAF weather code → bit flag in the 0x36 weather_flags byte
_TAF_WX_BITS = {
    "RA": 0x01, "DZ": 0x01,                          # rain / drizzle
    "SN": 0x02, "SG": 0x02, "PL": 0x02,              # snow / snow grains / ice pellets
    "TS": 0x04,                                       # thunderstorm
    "FZ": 0x08,                                       # freezing
    "BR": 0x10, "FG": 0x10, "HZ": 0x10, "FU": 0x10,  # mist / fog / haze / smoke
    "SH": 0x20,                                       # showers
    "+":  0x40,                                       # heavy intensity
    "-":  0x80,                                       # light intensity
}

# TAF cloud cover code → 0x30 sky nibble (matches obs sky_code table)
_TAF_CLOUD_TO_SKY = {
    "SKC": 0x0, "CLR": 0x0, "NSC": 0x0,
    "FEW": 0x1,
    "SCT": 0x2,
    "BKN": 0x3,
    "OVC": 0x4,
    "VV":  0x4,    # vertical visibility = effectively obscured / overcast
}


def _parse_taf_block(taf_text: str, station: str) -> str | None:
    """Find the TAF text block for a specific station inside a multi-station
    TAF product. Returns the block (issue line through next blank/$$/next
    station header) or None if the station isn't in this product.
    """
    lines = taf_text.splitlines()
    block: list[str] = []
    capturing = False
    for i, line in enumerate(lines):
        s = line.strip()
        if not capturing:
            # Look for "TAF <station>" or "TAF AMD <station>" or just
            # "<station> DDHHMMZ ..." as the issue header
            if (s.startswith(f"TAF {station}")
                or s.startswith(f"TAF AMD {station}")
                or s.startswith(f"TAF COR {station}")
                or (s.startswith(station + " ") and " " in s and len(s) > 12)):
                capturing = True
                block.append(s)
                continue
            # Some TAF products list the station on its own line
            if s == station and i + 1 < len(lines):
                capturing = True
                continue
        else:
            if not s or s.startswith("$$"):
                break
            # Heuristic for the start of the next station's TAF block
            if (s.startswith("TAF ") and station not in s[:30]) or (
                len(s) >= 4 and s[0].isupper() and s[:4].isalpha() and s[:4] != station
                and i + 1 < len(lines)
                and ("TEMPO" not in s and "BECMG" not in s and "FM" not in s[:3])
                and " " in s and "Z" in s.split()[1] if len(s.split()) > 1 else False
            ):
                break
            block.append(s)
    if not block:
        return None
    return " ".join(block)


def encode_taf(
    station_icao: str,
    taf_text: str,
    issued_hours_ago: int,
) -> bytes | None:
    """Parse a TAF product and pack the BASE forecast group as 0x36.

    Multi-period TAFs (FROM/BECMG/TEMPO change groups) are not yet
    expressed on the wire — this encoder extracts the BASE group only,
    which represents the conditions valid from the start of the TAF
    period until the first change group.
    """
    from meshcore_weather.protocol.meshwx import (
        pack_taf, wind_dir_to_nibble,
    )

    block = _parse_taf_block(taf_text, station_icao)
    if not block:
        return None

    # Validity period: e.g. "2618/2718" (DDHH/DDHH) — extract HH parts
    valid_from_hour = 0
    valid_to_hour = 0
    m_validity = re.search(r"\b\d{2}(\d{2})/\d{2}(\d{2})\b", block)
    if m_validity:
        valid_from_hour = int(m_validity.group(1))
        valid_to_hour = int(m_validity.group(2)) % 24

    # Wind: "27015G25KT" or "VRB05KT" or "21008KT"
    wind_dir_nibble = 0
    wind_speed_5kt = 0
    wind_gust_kt = 0
    m_wind = re.search(
        r"\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b", block
    )
    if m_wind:
        dir_str = m_wind.group(1)
        if dir_str == "VRB":
            wind_dir_nibble = 0
        else:
            try:
                wind_dir_nibble = wind_dir_to_nibble(int(dir_str))
            except ValueError:
                wind_dir_nibble = 0
        try:
            speed_kt = int(m_wind.group(2))
            wind_speed_5kt = max(0, min(15, int(round(speed_kt / 5))))
        except ValueError:
            pass
        if m_wind.group(3):
            try:
                wind_gust_kt = max(0, min(255, int(m_wind.group(3))))
            except ValueError:
                pass

    # Visibility: "6SM", "P6SM" (>6), "1 1/2SM", "10SM"
    visibility_qsm = 40  # default 10 sm
    m_vis = re.search(r"\b(P)?(\d{1,2})(?:\s+(\d)/(\d))?SM\b", block)
    if m_vis:
        whole = int(m_vis.group(2))
        if m_vis.group(3) and m_vis.group(4):
            num, den = int(m_vis.group(3)), int(m_vis.group(4))
            if den:
                whole_q = whole * 4 + int(round(num * 4 / den))
                visibility_qsm = whole_q
            else:
                visibility_qsm = whole * 4
        else:
            visibility_qsm = whole * 4
        if m_vis.group(1) == "P":  # "P6SM" means greater than
            visibility_qsm = max(visibility_qsm, 64)
        visibility_qsm = max(0, min(255, visibility_qsm))

    # Cloud layers: "BKN025", "OVC008", "FEW100", "SKC", "CLR", "VV001"
    # Pick the lowest broken/overcast layer for ceiling, default no ceiling
    ceiling_100ft = 0
    sky_code = 0  # clear
    cloud_layers = re.findall(r"\b(SKC|CLR|NSC|FEW|SCT|BKN|OVC|VV)(\d{3})?\b", block)
    if cloud_layers:
        # Find the most significant cloud cover code
        cover_priority = {"OVC": 4, "VV": 4, "BKN": 3, "SCT": 2, "FEW": 1, "SKC": 0, "CLR": 0, "NSC": 0}
        most_sig = max(cloud_layers, key=lambda c: cover_priority.get(c[0], 0))
        sky_code = _TAF_CLOUD_TO_SKY.get(most_sig[0], 0)
        # Ceiling: lowest BKN/OVC/VV layer
        for code, height in cloud_layers:
            if code in ("BKN", "OVC", "VV") and height:
                try:
                    h = int(height)  # in hundreds of feet
                    if ceiling_100ft == 0 or h < ceiling_100ft:
                        ceiling_100ft = h
                except ValueError:
                    pass
        ceiling_100ft = max(0, min(255, ceiling_100ft))

    # Weather flags: scan for known TAF weather codes
    weather_flags = 0
    # Strip wind/vis/cloud codes from the search area to reduce false matches
    search_text = re.sub(
        r"\b(?:VRB|\d{3})\d{2,3}(?:G\d{2,3})?KT\b|\bP?\d{1,2}(?:\s+\d/\d)?SM\b|"
        r"\b(?:SKC|CLR|NSC|FEW|SCT|BKN|OVC|VV)\d{3}?\b",
        " ", block,
    )
    for code, bit in _TAF_WX_BITS.items():
        if re.search(rf"(?:^|\s|\+|-)({re.escape(code)})", search_text):
            weather_flags |= bit
    # Special case: leading "+"/"-" intensity prefixes
    if re.search(r"\s\+(?:RA|SN|TS|SH)", search_text):
        weather_flags |= 0x40
    if re.search(r"\s-(?:RA|SN|DZ)", search_text):
        weather_flags |= 0x80

    # Override sky_code if there's a TS / heavy convective signature
    if weather_flags & 0x04:
        sky_code = 0xA  # SKY_THUNDERSTORM
    elif weather_flags & 0x01:
        sky_code = 0x8  # SKY_RAIN
    elif weather_flags & 0x02:
        sky_code = 0x9  # SKY_SNOW

    return pack_taf(
        station_icao=station_icao,
        issued_hours_ago=issued_hours_ago,
        valid_from_hour=valid_from_hour,
        valid_to_hour=valid_to_hour,
        wind_dir_nibble=wind_dir_nibble,
        wind_speed_5kt=wind_speed_5kt,
        wind_gust_kt=wind_gust_kt,
        visibility_qsm=visibility_qsm,
        ceiling_100ft=ceiling_100ft,
        sky_code=sky_code,
        weather_flags=weather_flags,
    )


# -- PFM-sourced forecast (preferred over ZFP narrative parsing) --

def encode_forecast_from_pfm(
    pfm_text: str,
    zone_code: str,
    issued_hours_ago: int,
    loc_type: int | None = None,
    loc_id=None,
) -> bytes | None:
    """Parse a PFM product, find the forecast point for `zone_code`, and
    encode its 7-day forecast as a 0x31 message.

    PFM gives us hard numeric values from the canonical NWS Point Forecast
    Matrix table — high/low temps, wind, sky cover, PoP, etc. — instead of
    the regex-extracted-from-narrative-English approach used by
    encode_forecast_from_zfp(). Same wire format on the output side, but the
    field accuracy is much better and we get more days of data.

    Returns None if:
      - The PFM can't be parsed
      - No forecast point in the PFM matches `zone_code`
      - The matched point has insufficient data (no full days available)

    `loc_type` / `loc_id` let the caller override the location reference
    encoded into the response (e.g. echo back LOC_PFM_POINT for a city
    forecast request); defaults to LOC_ZONE + zone_code.
    """
    # Local import to avoid a hard cycle between encoders → parser → encoders
    from meshcore_weather.parser.pfm import (
        parse_pfm, find_point, downsample_to_daily,
    )

    try:
        points = parse_pfm(pfm_text)
    except Exception as exc:
        logger.debug("PFM parse failed: %s", exc)
        return None

    point = find_point(points, zone=zone_code)
    if point is None:
        logger.debug("PFM has no forecast point for %s (has %d points)",
                     zone_code, len(points))
        return None

    daily_periods = downsample_to_daily(point, max_days=7)
    if not daily_periods:
        logger.debug("PFM downsampler returned no periods for %s", zone_code)
        return None

    if loc_type is None:
        loc_type = LOC_ZONE
        loc_id = zone_code

    return pack_forecast(
        loc_type, loc_id,
        issued_hours_ago=issued_hours_ago,
        periods=[p.to_encoder_dict() for p in daily_periods],
    )


# -- Text Chunk (0x40) — AFD, space weather, and other text products ----------

from meshcore_weather.protocol.meshwx import (
    pack_text_chunks,
    TEXT_SUBJECT_AFD,
    TEXT_SUBJECT_SPACE_WEATHER,
    TEXT_SUBJECT_TROPICAL,
    TEXT_SUBJECT_RIVER,
    TEXT_SUBJECT_FIRE,
    TEXT_SUBJECT_MARINE,
    TEXT_SUBJECT_GENERAL,
    TEXT_SUBJECT_CLIMATE,
    TEXT_SUBJECT_NOWCAST,
    LOC_WFO,
)


def _extract_afd_key_sections(afd_text: str, max_chars: int = 1800) -> str:
    """Extract the most valuable sections from an AFD product.

    AFDs are structured with sections like:
      .SYNOPSIS...
      .SHORT TERM... (days 1-3)
      .LONG TERM... (days 4-7)
      .AVIATION...
      .MARINE...

    For LoRa airtime, we prioritize .SYNOPSIS + .SHORT TERM which is the
    meteorologist's reasoning behind today's and tomorrow's forecast.
    If there's room, we append .LONG TERM.
    """
    lines = afd_text.splitlines()
    sections: dict[str, list[str]] = {}
    current_section = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".") and "..." in stripped:
            # Save previous section
            if current_section and current_lines:
                sections[current_section] = current_lines
            # Start new section
            header = stripped.lstrip(".").split("...")[0].strip().upper()
            current_section = header
            current_lines = []
            # Include any text after the ... on the same line
            after = stripped.split("...", 1)[1].strip() if "..." in stripped else ""
            if after:
                current_lines.append(after)
        elif stripped.startswith("&&") or stripped.startswith("$$"):
            if current_section and current_lines:
                sections[current_section] = current_lines
            current_section = ""
            current_lines = []
        elif current_section and stripped:
            current_lines.append(stripped)

    if current_section and current_lines:
        sections[current_section] = current_lines

    # Priority order of sections to include
    priority = [
        "SYNOPSIS", "WHAT HAS CHANGED",
        "SHORT TERM", "NEAR TERM",
        "LONG TERM", "EXTENDED",
    ]

    output_parts: list[str] = []
    total_len = 0
    for section_name in priority:
        for key in sections:
            if section_name in key:
                text = " ".join(sections[key])
                # Clean up common NWS formatting noise
                text = re.sub(r"\s+", " ", text).strip()
                if not text:
                    continue
                label = key.split("/")[0].strip()[:20]
                entry = f"[{label}] {text}"
                if total_len + len(entry) > max_chars:
                    # Truncate this section to fit
                    remaining = max_chars - total_len - len(label) - 4
                    if remaining > 50:
                        entry = f"[{label}] {text[:remaining]}..."
                        output_parts.append(entry)
                    break
                output_parts.append(entry)
                total_len += len(entry) + 1
                break
        if total_len >= max_chars:
            break

    return "\n".join(output_parts) if output_parts else afd_text[:max_chars]


def _extract_space_weather_summary(text: str, max_chars: int = 1800) -> str:
    """Extract a readable summary from a SWPC space weather product.

    DAY (Daily Space Weather Indices) products have tabular Kp index,
    solar flux, etc. We extract the key lines.
    """
    lines = text.splitlines()
    summary_lines: list[str] = []
    total = 0

    # Skip header lines (product ID, blank lines)
    in_data = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_data:
                summary_lines.append("")
            continue
        # Skip comment/header lines
        if stripped.startswith("#") or stripped.startswith(":"):
            # But include :Product and :Issued lines
            if stripped.startswith(":Product:") or stripped.startswith(":Issued:"):
                clean = stripped.lstrip(":").strip()
                summary_lines.append(clean)
                total += len(clean)
            continue
        in_data = True
        if total + len(stripped) > max_chars:
            break
        summary_lines.append(stripped)
        total += len(stripped)

    return "\n".join(summary_lines) if summary_lines else text[:max_chars]


def encode_afd(
    wfo: str,
    afd_text: str,
) -> list[bytes] | None:
    """Encode an Area Forecast Discussion as 0x40 text chunks.

    Extracts the key sections (synopsis + short term) and packs them
    into one or more text chunk messages addressed by WFO.

    Returns a list of chunk messages, or None if the AFD is empty.
    """
    summary = _extract_afd_key_sections(afd_text)
    if not summary.strip():
        return None
    return pack_text_chunks(
        subject_type=TEXT_SUBJECT_AFD,
        loc_type=LOC_WFO,
        loc_id=wfo,
        text=summary,
    )


def encode_afd_fec(
    wfo: str,
    afd_text: str,
    seq_counter,
    group_id: int = 0,
) -> list[bytes] | None:
    """Encode an AFD with FEC: each section is a separate unit.

    Base layer = synopsis (readable immediately).
    Data units = short term, long term, etc.
    Parity = XOR recovery of any single missing section.

    Returns v4-framed messages, or None if the AFD is empty.
    """
    from meshcore_weather.protocol.fec import fec_build_group
    from meshcore_weather.protocol.meshwx import MSG_TEXT_CHUNK

    sections = _extract_afd_sections_list(afd_text)
    if not sections:
        return None

    # First section (synopsis) becomes the base layer
    base_text = sections[0]
    base_msg = pack_text_chunks(
        subject_type=TEXT_SUBJECT_AFD,
        loc_type=LOC_WFO,
        loc_id=wfo,
        text=base_text,
    )
    base_layer = base_msg[0] if base_msg else None

    # Remaining sections become data units
    data_units: list[bytes] = []
    for section_text in sections[1:]:
        msgs = pack_text_chunks(
            subject_type=TEXT_SUBJECT_AFD,
            loc_type=LOC_WFO,
            loc_id=wfo,
            text=section_text,
        )
        if msgs:
            data_units.append(msgs[0])

    if not data_units:
        # Only synopsis — no FEC needed, just return the base
        return base_msg

    return fec_build_group(
        data_units=data_units,
        msg_type=MSG_TEXT_CHUNK,
        group_id=group_id % 4,
        seq_counter=seq_counter,
        base_layer=base_layer,
    )


def _extract_afd_sections_list(afd_text: str) -> list[str]:
    """Extract AFD sections as a list of labeled text strings.

    Returns: ["[SYNOPSIS] ...", "[SHORT TERM] ...", "[LONG TERM] ...", ...]
    Each entry is a standalone readable section.
    """
    lines = afd_text.splitlines()
    sections: dict[str, list[str]] = {}
    current_section = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".") and "..." in stripped:
            if current_section and current_lines:
                sections[current_section] = current_lines
            header = stripped.lstrip(".").split("...")[0].strip().upper()
            current_section = header
            current_lines = []
            after = stripped.split("...", 1)[1].strip() if "..." in stripped else ""
            if after:
                current_lines.append(after)
        elif stripped.startswith("&&") or stripped.startswith("$$"):
            if current_section and current_lines:
                sections[current_section] = current_lines
            current_section = ""
            current_lines = []
        elif current_section and stripped:
            current_lines.append(stripped)

    if current_section and current_lines:
        sections[current_section] = current_lines

    # Priority order — synopsis first (base layer), then others
    priority = [
        "SYNOPSIS", "WHAT HAS CHANGED",
        "SHORT TERM", "NEAR TERM",
        "LONG TERM", "EXTENDED",
    ]

    result: list[str] = []
    used: set[str] = set()
    for section_name in priority:
        for key in sections:
            if section_name in key and key not in used:
                text = re.sub(r"\s+", " ", " ".join(sections[key])).strip()
                if text:
                    label = key.split("/")[0].strip()[:20]
                    result.append(f"[{label}] {text}")
                    used.add(key)
                break

    return result


def encode_space_weather(
    text: str,
    subject_type: int = TEXT_SUBJECT_SPACE_WEATHER,
) -> list[bytes] | None:
    """Encode a space weather product as 0x40 text chunks.

    Addressed by WFO "SPC" (Storm Prediction Center) as a convention,
    since space weather isn't location-specific.
    """
    summary = _extract_space_weather_summary(text)
    if not summary.strip():
        return None
    return pack_text_chunks(
        subject_type=subject_type,
        loc_type=LOC_WFO,
        loc_id="SPC",  # convention for non-location-specific products
        text=summary,
    )


def encode_generic_text(
    subject_type: int,
    loc_type: int,
    loc_id,
    text: str,
    max_chars: int = 1800,
) -> list[bytes] | None:
    """Encode any NWS text product as 0x40 text chunks.

    Generic wrapper — trims to max_chars and packs. Use the more
    specific encoders (encode_afd, encode_space_weather) when the
    product type is known, since they extract the most valuable sections.
    """
    trimmed = text.strip()[:max_chars]
    if not trimmed:
        return None
    return pack_text_chunks(
        subject_type=subject_type,
        loc_type=loc_type,
        loc_id=loc_id,
        text=trimmed,
    )


# -- Fire Weather Forecast (FWF) encoding --


def encode_fwf(
    zone_code: str,
    fwf_text: str,
    issued_hours_ago: int,
) -> bytes | None:
    """Parse an FWF product and encode as a 0x38 fire weather message.

    FWF format has periods like:
        .TODAY...
        SKY/WEATHER............MOSTLY SUNNY.
        MAX TEMPERATURE........95 TO 100.
        MIN HUMIDITY...........10 TO 15 PERCENT.
        20 FT WINDS............WEST 10 TO 20 MPH.
        HAINES INDEX...........6.
        TRANSPORT WINDS........WEST 15 TO 25 MPH.
        MIXING HEIGHT..........8000 TO 10000 FT AGL.
    """
    if not fwf_text:
        return None

    periods = _parse_fwf_periods(fwf_text)
    if not periods:
        return None

    return pack_fire_weather(
        LOC_ZONE, zone_code,
        issued_hours_ago=issued_hours_ago,
        periods=periods,
    )


def _parse_fwf_periods(text: str) -> list[dict]:
    """Extract structured fire weather periods from FWF text."""
    raw_periods: list[tuple[str, str]] = []
    current_name = None
    current_lines: list[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            if current_name and current_lines:
                raw_periods.append((current_name, "\n".join(current_lines)))
                current_name = None
                current_lines = []
            continue
        if s.startswith(".") and "..." in s:
            if current_name and current_lines:
                raw_periods.append((current_name, "\n".join(current_lines)))
            head, _, rest = s.lstrip(".").partition("...")
            current_name = head.strip().upper()
            current_lines = [rest.strip()] if rest.strip() else []
        elif current_name:
            current_lines.append(s)

    if current_name and current_lines:
        raw_periods.append((current_name, "\n".join(current_lines)))

    periods = []
    for name, body in raw_periods[:7]:
        period_id = _PERIOD_IDS.get(name, 0)
        p = _extract_fwf_fields(body)
        p["period_id"] = period_id
        periods.append(p)

    return periods


def _extract_fwf_fields(text: str) -> dict:
    """Extract fire weather fields from a single FWF period."""
    result: dict = {
        "max_temp_f": 127,
        "min_rh_pct": 0,
        "transport_wind_dir_nibble": 0,
        "transport_wind_speed_5mph": 0,
        "mixing_height_500ft": 0,
        "haines_index": 2,
        "lightning_risk": 0,
        "cloud_cover": 0,
        "weather_byte": 0,
    }

    # MAX TEMPERATURE
    m = re.search(r"MAX TEMPERATURE[.\s]*(\d{2,3})", text, re.IGNORECASE)
    if m:
        result["max_temp_f"] = int(m.group(1))

    # MIN HUMIDITY
    m = re.search(r"MIN HUMIDITY[.\s]*(\d{1,3})", text, re.IGNORECASE)
    if m:
        result["min_rh_pct"] = int(m.group(1))

    # TRANSPORT WINDS (prefer over 20FT WINDS — transport is what matters
    # for smoke/fire spread). Fall back to 20 FT WINDS if no transport.
    m = re.search(
        r"TRANSPORT WINDS?[.\s]*(NORTH|SOUTH|EAST|WEST|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST)\w*\s+(\d+)(?:\s+TO\s+(\d+))?\s*MPH",
        text, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"20\s*FT\s*WINDS?[.\s]*(NORTH|SOUTH|EAST|WEST|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST)\w*\s+(\d+)(?:\s+TO\s+(\d+))?\s*MPH",
            text, re.IGNORECASE,
        )
    if m:
        dir_map = {
            "north": 0, "northeast": 2, "east": 4, "southeast": 6,
            "south": 8, "southwest": 10, "west": 12, "northwest": 14,
        }
        result["transport_wind_dir_nibble"] = dir_map.get(m.group(1).lower(), 0)
        speed = int(m.group(3) or m.group(2))
        result["transport_wind_speed_5mph"] = min(15, speed // 5)

    # MIXING HEIGHT
    m = re.search(r"MIXING HEIGHT[.\s]*(\d{3,6})", text, re.IGNORECASE)
    if m:
        result["mixing_height_500ft"] = min(255, int(m.group(1)) // 500)

    # HAINES INDEX
    m = re.search(r"HAINES\s*(?:INDEX)?[.\s]*(\d)", text, re.IGNORECASE)
    if m:
        result["haines_index"] = max(2, min(6, int(m.group(1))))

    # LIGHTNING (DRY vs WET)
    t = text.lower()
    if "dry lightning" in t or "dry tstm" in t or "dry thunder" in t:
        result["lightning_risk"] = 1
    elif "lightning" in t or "thunder" in t:
        result["lightning_risk"] = 2

    # SKY/WEATHER
    m = re.search(r"SKY/WEATHER[.\s]*(.*?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        result["cloud_cover"] = _classify_cloud_cover(m.group(1))
    else:
        result["cloud_cover"] = _classify_cloud_cover(text)

    return result


def _classify_cloud_cover(text: str) -> int:
    """Map free-form text to v4 cloud cover code (0-5)."""
    t = text.lower()
    if "clear" in t or "sunny" in t:
        return 0  # CLR
    if "few" in t:
        return 1  # FEW
    if "scatter" in t or "partly" in t:
        return 2  # SCT
    if "broken" in t or "mostly cloudy" in t:
        return 3  # BKN
    if "overcast" in t or "cloudy" in t:
        return 4  # OVC
    if "obscur" in t or "fog" in t:
        return 5  # VV
    return 0


# -- Regional Temp/Precip (RTP) encoding --


def encode_rtp(
    rtp_text: str,
) -> bytes | None:
    """Parse an RTP product and encode as a 0x3A daily climate message.

    RTP format is a tabular city list like:
        CITY              MAX  MIN  PCPN  SNOW
        AUSTIN            95   72   0.00
        SAN ANTONIO       93   71   T
    """
    if not rtp_text:
        return None

    cities = _parse_rtp_cities(rtp_text)
    if not cities:
        return None

    return pack_daily_climate(
        report_day_offset=1,  # RTP is typically yesterday's data
        cities=cities,
    )


def _parse_rtp_cities(text: str) -> list[dict]:
    """Extract city climate data from RTP tabular format."""
    cities: list[dict] = []
    # Find the data section — look for lines with city name + numbers
    # Typical format:
    #   CITY NAME         95   72   0.15   0.0
    # or:
    #   CITY NAME         95   72   T
    in_data = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("...") or s.startswith("$$"):
            if in_data:
                break  # End of data section
            continue

        # Skip header lines
        if "MAX" in s and "MIN" in s:
            in_data = True
            continue
        if not in_data:
            # Also start if we see a line that looks like data
            if re.match(r"^[A-Z][A-Z\s]+\s+\d{1,3}\s+", s):
                in_data = True
            else:
                continue

        # Parse: CITY NAME    max   min   precip  snow
        m = re.match(
            r"^([A-Z][A-Z\s.'-]+?)\s{2,}(-?\d{1,3})\s+(-?\d{1,3})"
            r"(?:\s+([\d.]+|T|M))?"
            r"(?:\s+([\d.]+|T|M))?",
            s,
        )
        if not m:
            continue

        city_name = m.group(1).strip()
        max_temp = int(m.group(2))
        min_temp = int(m.group(3))

        # Precip
        precip_raw = m.group(4) if m.group(4) else "M"
        if precip_raw == "T":
            precip_hundredths = 0xFF  # trace
        elif precip_raw == "M":
            precip_hundredths = 0xFE  # missing
        else:
            try:
                precip_hundredths = min(254, int(round(float(precip_raw) * 100)))
            except ValueError:
                precip_hundredths = 0xFE

        # Snow
        snow_raw = m.group(5) if m.group(5) else "M"
        if snow_raw == "T":
            snow_tenths = 0xFF
        elif snow_raw == "M":
            snow_tenths = 0xFE
        else:
            try:
                snow_tenths = min(254, int(round(float(snow_raw) * 10)))
            except ValueError:
                snow_tenths = 0xFE

        # Resolve city to place_id
        place_id = find_place_id_by_name(city_name)
        if place_id is None:
            # Try without trailing state abbreviation or period
            cleaned = re.sub(r"\s+[A-Z]{2}$", "", city_name).strip()
            place_id = find_place_id_by_name(cleaned)
        if place_id is None:
            continue  # skip unresolvable cities

        cities.append({
            "place_id": place_id,
            "max_temp_f": max_temp,
            "min_temp_f": min_temp,
            "precip_hundredths": precip_hundredths,
            "snow_tenths": snow_tenths,
        })

    return cities[:18]  # max 18 cities per frame


# -- Nowcast (NOW) encoding --


def encode_nowcast(
    wfo_code: str,
    now_text: str,
) -> list[bytes]:
    """Parse a NOW product and encode as 0x3C nowcast + optional 0x40 overflow.

    Returns a list of messages: first is the 0x3C structured message,
    remaining (if any) are 0x40 text chunk overflow.
    """
    if not now_text:
        return []

    # Extract the forecast body (skip header lines)
    body = _extract_now_body(now_text)
    if not body.strip():
        return []

    # Scan urgency flags from the full text
    urgency = _scan_urgency_flags(now_text)

    # Estimate valid hours from the text
    valid_hours = _extract_now_valid_hours(now_text)

    # Pack the structured message (truncates text to fit single frame)
    msg = pack_nowcast(
        LOC_WFO, wfo_code,
        valid_hours=valid_hours,
        urgency_flags=urgency,
        text=body,
    )
    messages = [msg]

    # If the body exceeds what fits in the 0x3C frame, send overflow
    # as text chunks. Estimate how much fit in the primary message.
    # WFO loc = 4 bytes, header overhead = 1+4+2 = 7, max text ≈ 129 bytes
    if len(body.encode("utf-8")) > 129:
        overflow = body[120:]  # approximate overlap to ensure continuity
        overflow_msgs = pack_text_chunks(
            subject_type=TEXT_SUBJECT_NOWCAST,
            loc_type=LOC_WFO,
            loc_id=wfo_code,
            text=overflow,
        )
        messages.extend(overflow_msgs)

    return messages


def _extract_now_body(text: str) -> str:
    """Extract the forecast body from a NOW product, skipping headers."""
    lines = text.splitlines()
    body_lines: list[str] = []
    past_header = False

    for line in lines:
        s = line.strip()
        # Skip blank lines and header boilerplate
        if not s:
            if past_header:
                body_lines.append("")
            continue
        # Header ends at the first "..." line (topic/headline) or
        # after the issuing office + time lines
        if s.startswith("...") and s.endswith("..."):
            # Headline — include it
            body_lines.append(s.strip(".").strip())
            past_header = True
            continue
        if s.startswith("$$"):
            break
        if past_header:
            body_lines.append(s)
        elif re.match(r"^\d{3,4}\s+(AM|PM)\s+\w+\s+\w+", s):
            # Timestamp line like "330 PM CDT SAT APR 12 2025"
            past_header = True

    return "\n".join(body_lines).strip()


def _scan_urgency_flags(text: str) -> int:
    """Scan NOW text for urgency keywords → flags byte."""
    t = text.lower()
    flags = 0
    if "thunder" in t or "lightning" in t or "tstm" in t:
        flags |= 0x01  # thunder
    if "flood" in t or "rising water" in t:
        flags |= 0x02  # flooding
    if "snow" in t or "ice" in t or "sleet" in t or "freez" in t or "blizzard" in t:
        flags |= 0x04  # winter
    if "fire" in t or "smoke" in t:
        flags |= 0x08  # fire
    if "wind" in t and ("gust" in t or "mph" in t):
        flags |= 0x10  # wind
    return flags


def _extract_now_valid_hours(text: str) -> int:
    """Estimate how many hours the nowcast covers."""
    t = text.lower()
    # Look for patterns like "through 5 pm", "next 2 hours", "until 8 pm"
    m = re.search(r"next\s+(\d+)\s+hours?", t)
    if m:
        return min(12, int(m.group(1)))
    # Default: NOW products typically cover 1-3 hours
    return 3


# -- State Forecast Table (SFT) encoding --
# SFT uses the existing 0x31 forecast wire format. This parser extracts
# per-city per-period data from the SFT tabular format and feeds it into
# pack_forecast(). See _build_forecast() in executor.py for integration.


def encode_forecast_from_sft(
    sft_text: str,
    city_name: str,
    issued_hours_ago: int,
    loc_type: int | None = None,
    loc_id=None,
) -> bytes | None:
    """Parse an SFT product for a specific city and encode as 0x31 forecast.

    SFT format:
        CITY              WED      WED NIGHT THU
                          HI  WX   LO  WX    HI  WX
        AUSTIN            95  SU   72  CL    93  SU
    """
    if not sft_text:
        return None

    periods = _parse_sft_city(sft_text, city_name)
    if not periods:
        return None

    if loc_type is None:
        loc_type = LOC_ZONE
        loc_id = ""  # SFT doesn't have zone codes; caller must provide

    return pack_forecast(
        loc_type, loc_id,
        issued_hours_ago=issued_hours_ago,
        periods=periods,
    )


# SFT weather abbreviations → sky code
_SFT_WX_MAP = {
    "SU": SKY_CLEAR,      # Sunny
    "CL": SKY_CLEAR,      # Clear
    "FW": SKY_FEW,        # Few clouds
    "SC": SKY_SCATTERED,  # Scattered
    "PC": SKY_SCATTERED,  # Partly cloudy
    "BK": SKY_BROKEN,     # Broken
    "MC": SKY_BROKEN,     # Mostly cloudy
    "OV": SKY_OVERCAST,   # Overcast
    "FG": SKY_FOG,        # Fog
    "HZ": SKY_HAZE,       # Haze
    "SM": SKY_SMOKE,      # Smoke
    "RA": SKY_RAIN,       # Rain
    "SN": SKY_SNOW,       # Snow
    "TS": SKY_THUNDERSTORM,  # Thunderstorm
    "SH": SKY_RAIN,       # Showers
    "RS": SKY_SNOW,       # Rain/Snow
    "IP": SKY_SNOW,       # Ice pellets
    "ZR": SKY_RAIN,       # Freezing rain
    "DR": SKY_DRIZZLE,    # Drizzle
}


def _parse_sft_city(text: str, city_name: str) -> list[dict]:
    """Extract forecast periods for a city from SFT tabular data."""
    city_upper = city_name.upper().strip()
    periods: list[dict] = []

    # Find the period header line to know column meanings
    period_names: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        # Period header line: "WED      WED NIGHT THU      THU NIGHT ..."
        if re.match(r"^[A-Z]{3}\s", s) and "NIGHT" in s:
            # Split on 2+ spaces to get period names
            period_names = re.split(r"\s{2,}", s.strip())
            break
        # Alternative: day names on a line
        if re.match(r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s", s):
            period_names = re.split(r"\s{2,}", s.strip())
            break

    # Find the city data line
    for line in text.splitlines():
        s = line.strip()
        if not s.upper().startswith(city_upper):
            continue
        # City line: "AUSTIN            95  SU   72  CL    93  SU"
        # Extract numbers and wx codes after the city name
        after_city = s[len(city_upper):].strip()
        tokens = after_city.split()

        # Parse pairs of (temp, wx_code) for each period
        i = 0
        period_idx = 0
        while i < len(tokens) and period_idx < 7:
            try:
                temp = int(tokens[i])
            except ValueError:
                i += 1
                continue

            wx_code = tokens[i + 1].upper() if i + 1 < len(tokens) else ""
            sky = _SFT_WX_MAP.get(wx_code, SKY_OTHER)

            # Determine period_id from period_names
            if period_idx < len(period_names):
                pname = period_names[period_idx].upper()
                period_id = _PERIOD_IDS.get(pname, period_idx)
            else:
                period_id = period_idx

            is_night = period_idx < len(period_names) and \
                       "NIGHT" in period_names[period_idx].upper()

            periods.append({
                "period_id": period_id,
                "high_f": 127 if is_night else temp,
                "low_f": temp if is_night else 127,
                "sky_code": sky,
                "precip_pct": 0,  # SFT doesn't include precip %
                "wind_dir_nibble": 0,
                "wind_speed_5mph": 0,
                "condition_flags": 0,
            })

            i += 2  # skip temp + wx code
            period_idx += 1

        break  # found our city

    return periods


# -- SPC Watch (WOU/SEL) encoding --
# Watch products feed into existing warning polygon (0x20) and warning
# zone (0x21) encoders. The parser extracts watch type, polygon, and
# zone list from SEL/WOU text, then the executor calls existing
# pack_warning_polygon() / pack_warning_zones().


def parse_sel_watch(text: str) -> dict | None:
    """Parse an SPC SEL (watch definition) product.

    Returns dict with:
      watch_type: "tornado" or "severe_thunderstorm"
      watch_number: int
      polygon: list of (lat, lon) tuples
      expires: str (VTEC-style end time)
    or None if parsing fails.
    """
    if not text:
        return None

    result: dict = {
        "watch_type": "",
        "watch_number": 0,
        "polygon": [],
        "expires": "",
    }

    # Watch type from header
    t = text.upper()
    if "TORNADO WATCH" in t:
        result["watch_type"] = "tornado"
    elif "SEVERE THUNDERSTORM WATCH" in t:
        result["watch_type"] = "severe_thunderstorm"
    else:
        return None

    # Watch number
    m = re.search(r"WATCH\s+(?:NUMBER\s+)?(\d+)", t)
    if m:
        result["watch_number"] = int(m.group(1))

    # Polygon from LAT...LON line
    from meshcore_weather.parser.weather import extract_warning_polygon
    result["polygon"] = extract_warning_polygon(text)
    if not result["polygon"]:
        return None

    # Expiration from VTEC or text
    m = re.search(r"VALID\s+(\d{6})Z\s*-\s*(\d{6})Z", t)
    if m:
        result["expires"] = m.group(2)

    return result


def parse_wou_zones(text: str) -> dict | None:
    """Parse an SPC WOU (Watch Outline Update) for zone list.

    Returns dict with:
      watch_number: int
      zones: list of zone code strings (e.g. ["TXZ192", "TXZ193"])
      action: "NEW" | "EXT" | "CAN" (new/extended/cancelled)
    or None if parsing fails.
    """
    if not text:
        return None

    result: dict = {
        "watch_number": 0,
        "zones": [],
        "action": "NEW",
    }

    t = text.upper()
    m = re.search(r"WATCH\s+(\d+)", t)
    if m:
        result["watch_number"] = int(m.group(1))

    # Extract zones using the existing zone range expander
    from meshcore_weather.parser.weather import _expand_zone_ranges
    zones = _expand_zone_ranges(text)
    result["zones"] = sorted(zones)

    if not result["zones"]:
        return None

    # Action
    if "CANCELLED" in t or "CAN " in t:
        result["action"] = "CAN"
    elif "EXTENDED" in t or "EXT " in t:
        result["action"] = "EXT"

    return result
