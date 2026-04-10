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
    pack_observation,
    pack_forecast,
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

def encode_metar(station_icao: str, metar_text: str, ts_minutes_utc: int) -> bytes | None:
    """Build a 0x30 observation from a raw METAR string.

    Example METAR: "KAUS 082151Z 17010KT 10SM SCT040 BKN070 28/18 A3010"
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
        # Precipitation codes (RA=rain, SN=snow, TS=thunder, etc.)
        if "TS" in p:
            sky_code = SKY_THUNDERSTORM
        elif "RA" in p and sky_code < SKY_RAIN:
            sky_code = SKY_RAIN
        elif "SN" in p and sky_code < SKY_SNOW:
            sky_code = SKY_SNOW
        elif "FG" in p and sky_code < SKY_FOG:
            sky_code = SKY_FOG

    if temp_f is None:
        return None

    return pack_observation(
        LOC_STATION, station_icao,
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
) -> bytes | None:
    """Build a 0x30 observation from an RWR line for a specific city.

    The caller is responsible for finding the correct line — we parse it.
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
        LOC_ZONE, zone_code,
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
) -> bytes | None:
    """Parse a ZFP zone forecast and encode it as a 0x31 forecast message.

    ZFP format has periods like:
        .TONIGHT...Mostly clear. Lows around 60. Southeast winds around 5 mph.
        .THURSDAY...Sunny. Highs in the upper 80s. ...
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

    return pack_forecast(
        LOC_ZONE, zone_code,
        issued_hours_ago=issued_hours_ago,
        periods=encoded_periods,
    )


def now_utc_minutes() -> int:
    """Minutes since midnight UTC (uint16)."""
    now = datetime.now(timezone.utc)
    return now.hour * 60 + now.minute
