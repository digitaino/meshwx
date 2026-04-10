"""MeshWX binary wire format: pack/unpack radar grids, warning polygons, refresh requests.

Wire format reference: Weather_Protocol.md
All multi-byte integers are big-endian unless noted.
All binary payloads are COBS-encoded before transmission to avoid null bytes
which the MeshCore firmware's companion protocol truncates at.
"""

import json
import struct
from pathlib import Path


# -- COBS (Consistent Overhead Byte Stuffing) --

def cobs_encode(data: bytes) -> bytes:
    """COBS-encode data to eliminate all 0x00 bytes.

    Overhead: at most 1 byte per 254 input bytes.
    """
    output = bytearray()
    block_start = len(output)
    output.append(0)  # placeholder for first code byte
    run_length = 1

    for byte in data:
        if byte == 0x00:
            output[block_start] = run_length
            block_start = len(output)
            output.append(0)  # placeholder for next code byte
            run_length = 1
        else:
            output.append(byte)
            run_length += 1
            if run_length == 0xFF:
                output[block_start] = run_length
                block_start = len(output)
                output.append(0)
                run_length = 1

    output[block_start] = run_length
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    """Decode COBS-encoded data back to original bytes."""
    output = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        i += 1
        for _ in range(code - 1):
            if i >= len(data):
                break
            output.append(data[i])
            i += 1
        # Append a zero delimiter between blocks, but not after the last block
        if code < 0xFF and i < len(data):
            output.append(0x00)
    return bytes(output)

# -- Message type bytes --
MSG_REFRESH = 0x01       # v1: client → bot refresh request (DM)
MSG_DATA_REQUEST = 0x02  # v2: client → bot data request (DM)
MSG_RADAR = 0x10         # v1: radar grid (broadcast)
MSG_WARNING = 0x20       # v1: warning polygon (broadcast)
MSG_OBSERVATION = 0x30   # v2: current conditions (wx reply)
MSG_FORECAST = 0x31      # v2: multi-period forecast
MSG_OUTLOOK = 0x32       # v2: HWO 1-7 day hazards
MSG_STORM_REPORTS = 0x33 # v2: LSR reports
MSG_RAIN_OBS = 0x34      # v2: rain city list
MSG_METAR = 0x35         # v2: raw METAR
MSG_TAF = 0x36           # v2: TAF forecast
MSG_WARNINGS_NEAR = 0x37 # v2: warnings near location summary
MSG_TEXT_CHUNK = 0x40    # v2: compressed text fallback

# -- Warning type nibbles (high nibble of byte 1) --
WARN_TORNADO = 0x1
WARN_SEVERE_TSTORM = 0x2
WARN_FLASH_FLOOD = 0x3
WARN_FLOOD = 0x4
WARN_WINTER_STORM = 0x5
WARN_HIGH_WIND = 0x6
WARN_FIRE = 0x7
WARN_MARINE = 0x8
WARN_SPECIAL = 0x9
WARN_OTHER = 0xF

# -- Severity nibbles (low nibble of byte 1) --
SEV_ADVISORY = 0x1
SEV_WATCH = 0x2
SEV_WARNING = 0x3
SEV_EMERGENCY = 0x4

# -- NWS product type → warning type nibble --
PRODUCT_TYPE_MAP = {
    "TOR": WARN_TORNADO,
    "SVS": WARN_SEVERE_TSTORM,
    "SVR": WARN_SEVERE_TSTORM,
    "FFW": WARN_FLASH_FLOOD,
    "FLS": WARN_FLASH_FLOOD,
    "FLW": WARN_FLOOD,
    "WSW": WARN_WINTER_STORM,
    "NPW": WARN_HIGH_WIND,
    "FWS": WARN_FIRE,
    "RFW": WARN_FIRE,
    "MWS": WARN_MARINE,
    "SMW": WARN_MARINE,
    "SPS": WARN_SPECIAL,
    "DSW": WARN_OTHER,
    "EWW": WARN_OTHER,
    "SQW": WARN_OTHER,
    "SWO": WARN_OTHER,
}

# -- VTEC significance → severity nibble --
VTEC_SEVERITY_MAP = {
    "W": SEV_WARNING,
    "A": SEV_WATCH,
    "Y": SEV_ADVISORY,
    "S": SEV_ADVISORY,
}

# -- Region definitions (from protocol spec) --
REGIONS = {
    0x0: {"name": "Northeast", "n": 48.0, "s": 37.0, "w": -82.0, "e": -67.0, "scale": 55},
    0x1: {"name": "Southeast", "n": 37.0, "s": 24.0, "w": -92.0, "e": -75.0, "scale": 55},
    0x2: {"name": "Upper Midwest", "n": 50.0, "s": 40.0, "w": -98.0, "e": -82.0, "scale": 55},
    0x3: {"name": "Southern", "n": 37.0, "s": 25.0, "w": -105.0, "e": -88.0, "scale": 55},
    0x4: {"name": "Central", "n": 44.0, "s": 34.0, "w": -105.0, "e": -90.0, "scale": 55},
    0x5: {"name": "Mountain", "n": 49.0, "s": 31.0, "w": -117.0, "e": -102.0, "scale": 55},
    0x6: {"name": "Pacific", "n": 49.0, "s": 32.0, "w": -125.0, "e": -114.0, "scale": 40},
    0x7: {"name": "Alaska", "n": 72.0, "s": 51.0, "w": -180.0, "e": -130.0, "scale": 175},
    0x8: {"name": "Hawaii", "n": 23.0, "s": 18.0, "w": -161.0, "e": -154.0, "scale": 28},
    0x9: {"name": "Puerto Rico", "n": 19.5, "s": 17.0, "w": -68.0, "e": -65.0, "scale": 12},
}


def region_for_location(lat: float, lon: float) -> int | None:
    """Find which region contains a lat/lon point. Returns region_id or None."""
    for rid, r in REGIONS.items():
        if r["s"] <= lat <= r["n"] and r["w"] <= lon <= r["e"]:
            return rid
    return None


# -- Radar Grid (0x10) -- 133 bytes --

def pack_radar_grid(
    region_id: int,
    frame_seq: int,
    timestamp_utc_min: int,
    scale_km: int,
    grid: list[list[int]],
) -> bytes:
    """Pack a 16x16 radar grid into 133-byte wire format.

    grid: 16x16 array of 4-bit reflectivity values (0x0-0xE).
    """
    msg = bytearray(133)
    msg[0] = MSG_RADAR
    msg[1] = ((region_id & 0x0F) << 4) | (frame_seq & 0x0F)
    struct.pack_into(">H", msg, 2, timestamp_utc_min & 0xFFFF)
    msg[4] = scale_km & 0xFF
    idx = 5
    for row in range(16):
        for col in range(0, 16, 2):
            high = grid[row][col] & 0x0F
            low = grid[row][col + 1] & 0x0F
            msg[idx] = (high << 4) | low
            idx += 1
    return bytes(msg)


def unpack_radar_grid(data: bytes) -> dict:
    """Unpack a 133-byte radar grid message."""
    if len(data) < 133 or data[0] != MSG_RADAR:
        raise ValueError("Invalid radar grid message")
    region_id = (data[1] >> 4) & 0x0F
    frame_seq = data[1] & 0x0F
    timestamp = struct.unpack_from(">H", data, 2)[0]
    scale_km = data[4]
    grid = [[0] * 16 for _ in range(16)]
    idx = 5
    for row in range(16):
        for col in range(0, 16, 2):
            grid[row][col] = (data[idx] >> 4) & 0x0F
            grid[row][col + 1] = data[idx] & 0x0F
            idx += 1
    return {
        "type": MSG_RADAR,
        "region_id": region_id,
        "frame_seq": frame_seq,
        "timestamp_utc_min": timestamp,
        "scale_km": scale_km,
        "grid": grid,
    }


# -- Warning Polygon (0x20) -- variable, max 136 bytes --

def pack_warning_polygon(
    warning_type: int,
    severity: int,
    expiry_minutes: int,
    vertices: list[tuple[float, float]],
    headline: str,
) -> bytes:
    """Pack a warning polygon into wire format (max 136 bytes).

    vertices: list of (lat, lon) in decimal degrees.
    """
    msg = bytearray()
    msg.append(MSG_WARNING)
    msg.append(((warning_type & 0x0F) << 4) | (severity & 0x0F))
    msg.extend(struct.pack(">H", min(expiry_minutes, 0xFFFF)))
    msg.append(len(vertices) & 0xFF)

    if not vertices:
        remaining = 136 - len(msg)
        if remaining > 0:
            msg.extend(headline.encode("utf-8")[:remaining])
        return bytes(msg)

    # First vertex: 24-bit signed, degrees * 10000 (~11m precision)
    lat0, lon0 = vertices[0]
    lat_i = int(lat0 * 10000)
    lon_i = int(lon0 * 10000)
    msg.extend(lat_i.to_bytes(3, "big", signed=True))
    msg.extend(lon_i.to_bytes(3, "big", signed=True))

    # Remaining vertices: int8 delta pairs (0.01 degree units)
    for lat, lon in vertices[1:]:
        dlat = max(-128, min(127, int((lat - lat0) / 0.01)))
        dlon = max(-128, min(127, int((lon - lon0) / 0.01)))
        msg.extend(struct.pack("bb", dlat, dlon))

    # Headline fills remaining space
    remaining = 136 - len(msg)
    if remaining > 0:
        msg.extend(headline.encode("utf-8")[:remaining])
    return bytes(msg)


def unpack_warning_polygon(data: bytes) -> dict:
    """Unpack a warning polygon message."""
    if len(data) < 5 or data[0] != MSG_WARNING:
        raise ValueError("Invalid warning polygon message")
    warning_type = (data[1] >> 4) & 0x0F
    severity = data[1] & 0x0F
    expiry = struct.unpack_from(">H", data, 2)[0]
    vertex_count = data[4]

    vertices = []
    offset = 5
    if vertex_count > 0 and offset + 6 <= len(data):
        lat0 = int.from_bytes(data[offset : offset + 3], "big", signed=True) / 10000
        lon0 = int.from_bytes(data[offset + 3 : offset + 6], "big", signed=True) / 10000
        vertices.append((lat0, lon0))
        offset += 6
        for _ in range(vertex_count - 1):
            if offset + 2 > len(data):
                break
            dlat, dlon = struct.unpack_from("bb", data, offset)
            vertices.append((lat0 + dlat * 0.01, lon0 + dlon * 0.01))
            offset += 2

    headline = data[offset:].decode("utf-8", errors="replace").rstrip("\x00")
    return {
        "type": MSG_WARNING,
        "warning_type": warning_type,
        "severity": severity,
        "expiry_minutes": expiry,
        "vertices": vertices,
        "headline": headline,
    }


# -- Refresh Request (0x01) -- 4 bytes --

def pack_refresh_request(
    region_id: int, request_type: int, client_newest: int
) -> bytes:
    """Pack a 4-byte refresh request.

    request_type: 0x1=radar, 0x2=warnings, 0x3=both.
    client_newest: minutes since midnight UTC of newest cached data.
    """
    msg = bytearray(4)
    msg[0] = MSG_REFRESH
    msg[1] = ((region_id & 0x0F) << 4) | (request_type & 0x0F)
    struct.pack_into(">H", msg, 2, client_newest & 0xFFFF)
    return bytes(msg)


def unpack_refresh_request(data: bytes) -> dict:
    """Unpack a 4-byte refresh request."""
    if len(data) < 4 or data[0] != MSG_REFRESH:
        raise ValueError("Invalid refresh request")
    return {
        "type": MSG_REFRESH,
        "region_id": (data[1] >> 4) & 0x0F,
        "request_type": data[1] & 0x0F,
        "client_newest": struct.unpack_from(">H", data, 2)[0],
    }


# -- v2: Data types, sky codes, location encoding --

# Data type codes for MSG_DATA_REQUEST (0x02)
DATA_WX = 0x0
DATA_FORECAST = 0x1
DATA_OUTLOOK = 0x2
DATA_STORM_REPORTS = 0x3
DATA_RAIN_OBS = 0x4
DATA_METAR = 0x5
DATA_TAF = 0x6
DATA_WARNINGS_NEAR = 0x7

# Sky condition codes (low nibble in observation messages)
SKY_CLEAR = 0x0
SKY_FEW = 0x1
SKY_SCATTERED = 0x2
SKY_BROKEN = 0x3
SKY_OVERCAST = 0x4
SKY_FOG = 0x5
SKY_SMOKE = 0x6
SKY_HAZE = 0x7
SKY_RAIN = 0x8
SKY_SNOW = 0x9
SKY_THUNDERSTORM = 0xA
SKY_DRIZZLE = 0xB
SKY_MIST = 0xC
SKY_SQUALL = 0xD
SKY_SAND = 0xE
SKY_OTHER = 0xF

# 16-point compass wind direction
COMPASS_POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def wind_dir_to_nibble(degrees: int) -> int:
    """Map 0-360° to a 4-bit compass point (0=N, 4=E, 8=S, 12=W)."""
    if degrees < 0 or degrees > 360:
        return 0
    return int(round(degrees / 22.5)) % 16


def nibble_to_wind_dir(nibble: int) -> str:
    """Return the compass abbreviation for a 4-bit wind direction."""
    return COMPASS_POINTS[nibble & 0x0F]


# -- State index (bundled with both server and client) --

_STATE_INDEX_PATH = Path(__file__).parent.parent / "geodata" / "state_index.json"
_STATE_LIST: list[str] | None = None
_STATE_TO_IDX: dict[str, int] | None = None


def _load_state_index() -> None:
    global _STATE_LIST, _STATE_TO_IDX
    if _STATE_LIST is not None:
        return
    with open(_STATE_INDEX_PATH) as f:
        data = json.load(f)
    _STATE_LIST = data["states"]
    _STATE_TO_IDX = {s: i for i, s in enumerate(_STATE_LIST)}


def state_to_idx(state: str) -> int:
    """Map 2-letter state code to 1-byte index. Returns 0xFF if unknown."""
    _load_state_index()
    return _STATE_TO_IDX.get(state.upper(), 0xFF)


def idx_to_state(idx: int) -> str:
    """Map 1-byte index back to 2-letter state code."""
    _load_state_index()
    if 0 <= idx < len(_STATE_LIST):
        return _STATE_LIST[idx]
    return "??"


# -- Location reference encoding --

LOC_ZONE = 0x01      # 3 bytes: state_idx + zone_num (uint16)
LOC_STATION = 0x02   # 4 bytes: ICAO ASCII
LOC_PLACE = 0x03     # 3 bytes: uint24 place_id
LOC_LATLON = 0x04    # 6 bytes: 2x int24 * 10000
LOC_WFO = 0x05       # 3 bytes: WFO ASCII


def pack_location(loc_type: int, loc_id) -> bytes:
    """Pack a location reference as type tag + payload.

    loc_type values and expected loc_id formats:
      LOC_ZONE:    str "TXZ192" → 3 bytes (state_idx byte + uint16 zone_num BE)
      LOC_STATION: str "KAUS" → 4 bytes ASCII
      LOC_PLACE:   int place index → 3 bytes uint24 BE
      LOC_LATLON:  (lat, lon) tuple → 6 bytes (2x int24 * 10000 BE)
      LOC_WFO:     str "EWX" → 3 bytes ASCII
    """
    if loc_type == LOC_ZONE:
        if not isinstance(loc_id, str) or len(loc_id) != 6 or loc_id[2] != "Z":
            raise ValueError(f"Zone code must be like 'TXZ192', got: {loc_id!r}")
        state = loc_id[:2]
        zone_num = int(loc_id[3:])
        idx = state_to_idx(state)
        return bytes([LOC_ZONE, idx]) + struct.pack(">H", zone_num)

    if loc_type == LOC_STATION:
        if not isinstance(loc_id, str) or len(loc_id) != 4:
            raise ValueError(f"Station ICAO must be 4 chars, got: {loc_id!r}")
        return bytes([LOC_STATION]) + loc_id.upper().encode("ascii")

    if loc_type == LOC_PLACE:
        if not isinstance(loc_id, int) or loc_id < 0 or loc_id >= (1 << 24):
            raise ValueError(f"Place ID must be uint24, got: {loc_id}")
        return bytes([LOC_PLACE]) + loc_id.to_bytes(3, "big")

    if loc_type == LOC_LATLON:
        lat, lon = loc_id
        lat_i = int(round(lat * 10000))
        lon_i = int(round(lon * 10000))
        return bytes([LOC_LATLON]) + lat_i.to_bytes(3, "big", signed=True) + lon_i.to_bytes(3, "big", signed=True)

    if loc_type == LOC_WFO:
        if not isinstance(loc_id, str) or len(loc_id) != 3:
            raise ValueError(f"WFO must be 3 chars, got: {loc_id!r}")
        return bytes([LOC_WFO]) + loc_id.upper().encode("ascii")

    raise ValueError(f"Unknown loc_type: {loc_type}")


def unpack_location(data: bytes, offset: int = 0) -> tuple[dict, int]:
    """Unpack a location reference starting at offset.

    Returns (location_dict, new_offset).
    location_dict has 'type' and type-specific fields.
    """
    if offset >= len(data):
        raise ValueError("Location data empty")
    loc_type = data[offset]

    if loc_type == LOC_ZONE:
        if offset + 4 > len(data):
            raise ValueError("Zone location truncated")
        state_idx = data[offset + 1]
        zone_num = struct.unpack_from(">H", data, offset + 2)[0]
        state = idx_to_state(state_idx)
        return (
            {"type": LOC_ZONE, "zone": f"{state}Z{zone_num:03d}"},
            offset + 4,
        )

    if loc_type == LOC_STATION:
        if offset + 5 > len(data):
            raise ValueError("Station location truncated")
        icao = data[offset + 1 : offset + 5].decode("ascii", errors="replace")
        return {"type": LOC_STATION, "station": icao}, offset + 5

    if loc_type == LOC_PLACE:
        if offset + 4 > len(data):
            raise ValueError("Place location truncated")
        place_id = int.from_bytes(data[offset + 1 : offset + 4], "big")
        return {"type": LOC_PLACE, "place_id": place_id}, offset + 4

    if loc_type == LOC_LATLON:
        if offset + 7 > len(data):
            raise ValueError("Lat/lon location truncated")
        lat = int.from_bytes(data[offset + 1 : offset + 4], "big", signed=True) / 10000
        lon = int.from_bytes(data[offset + 4 : offset + 7], "big", signed=True) / 10000
        return {"type": LOC_LATLON, "lat": lat, "lon": lon}, offset + 7

    if loc_type == LOC_WFO:
        if offset + 4 > len(data):
            raise ValueError("WFO location truncated")
        wfo = data[offset + 1 : offset + 4].decode("ascii", errors="replace")
        return {"type": LOC_WFO, "wfo": wfo}, offset + 4

    raise ValueError(f"Unknown location type: 0x{loc_type:02x}")


# -- Data Request (0x02) --

def pack_data_request(
    data_type: int,
    loc_type: int,
    loc_id,
    client_newest: int = 0,
    flags: int = 0,
) -> bytes:
    """Pack a data request to be sent as a DM to the bot."""
    header = bytes([
        MSG_DATA_REQUEST,
        ((data_type & 0x0F) << 4) | (flags & 0x0F),
    ]) + struct.pack("<H", client_newest & 0xFFFF)
    return header + pack_location(loc_type, loc_id)


def unpack_data_request(data: bytes) -> dict:
    """Unpack a data request."""
    if len(data) < 5 or data[0] != MSG_DATA_REQUEST:
        raise ValueError("Invalid data request")
    data_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    client_newest = struct.unpack_from("<H", data, 2)[0]
    location, _ = unpack_location(data, 4)
    return {
        "type": MSG_DATA_REQUEST,
        "data_type": data_type,
        "flags": flags,
        "client_newest": client_newest,
        "location": location,
    }


# -- Observation (0x30) -- current conditions --

def pack_observation(
    loc_type: int,
    loc_id,
    timestamp_utc_min: int,
    temp_f: int,
    dewpoint_f: int,
    wind_dir_deg: int,
    sky_code: int,
    wind_speed_mph: int,
    wind_gust_mph: int = 0,
    visibility_mi: int = 10,
    pressure_inhg: float = 29.92,
    feels_like_delta: int = 0,
) -> bytes:
    """Pack a current-conditions observation message."""

    def _clamp_i8(v: int) -> int:
        return max(-128, min(127, int(v)))

    def _clamp_u8(v: int) -> int:
        return max(0, min(255, int(v)))

    loc_bytes = pack_location(loc_type, loc_id)
    pressure_byte = max(0, min(255, int(round((pressure_inhg - 29.00) * 100))))
    wind_dir_nib = wind_dir_to_nibble(wind_dir_deg) if wind_dir_deg is not None else 0
    packed_dir_sky = ((wind_dir_nib & 0x0F) << 4) | (sky_code & 0x0F)

    msg = bytearray()
    msg.append(MSG_OBSERVATION)
    msg.extend(loc_bytes)
    msg.extend(struct.pack("<H", timestamp_utc_min & 0xFFFF))
    msg.append(_clamp_i8(temp_f) & 0xFF)
    msg.append(_clamp_i8(dewpoint_f) & 0xFF)
    msg.append(packed_dir_sky)
    msg.append(_clamp_u8(wind_speed_mph))
    msg.append(_clamp_u8(wind_gust_mph))
    msg.append(_clamp_u8(visibility_mi))
    msg.append(pressure_byte)
    msg.append(_clamp_i8(feels_like_delta) & 0xFF)
    return bytes(msg)


def unpack_observation(data: bytes) -> dict:
    """Unpack an observation message."""
    if len(data) < 2 or data[0] != MSG_OBSERVATION:
        raise ValueError("Invalid observation message")
    location, offset = unpack_location(data, 1)
    if offset + 10 > len(data):
        raise ValueError("Observation truncated")
    timestamp = struct.unpack_from("<H", data, offset)[0]
    temp = struct.unpack_from("b", data, offset + 2)[0]
    dewpoint = struct.unpack_from("b", data, offset + 3)[0]
    dir_sky = data[offset + 4]
    wind_dir_nib = (dir_sky >> 4) & 0x0F
    sky_code = dir_sky & 0x0F
    wind_speed = data[offset + 5]
    wind_gust = data[offset + 6]
    visibility = data[offset + 7]
    pressure = 29.00 + data[offset + 8] / 100
    feels_like_delta = struct.unpack_from("b", data, offset + 9)[0]
    return {
        "type": MSG_OBSERVATION,
        "location": location,
        "timestamp_utc_min": timestamp,
        "temp_f": temp,
        "dewpoint_f": dewpoint,
        "wind_dir": nibble_to_wind_dir(wind_dir_nib),
        "wind_dir_nibble": wind_dir_nib,
        "sky_code": sky_code,
        "wind_speed_mph": wind_speed,
        "wind_gust_mph": wind_gust,
        "visibility_mi": visibility,
        "pressure_inhg": round(pressure, 2),
        "feels_like_delta": feels_like_delta,
        "feels_like_f": temp + feels_like_delta,
    }


# -- Forecast (0x31) -- multi-period forecast --

def pack_forecast(
    loc_type: int,
    loc_id,
    issued_hours_ago: int,
    periods: list[dict],
) -> bytes:
    """Pack a multi-period forecast message.

    periods: list of dicts with keys:
      period_id, high_f, low_f, sky_code, precip_pct,
      wind_dir_nibble, wind_speed_5mph, condition_flags
    """
    loc_bytes = pack_location(loc_type, loc_id)
    msg = bytearray()
    msg.append(MSG_FORECAST)
    msg.extend(loc_bytes)
    msg.append(max(0, min(255, int(issued_hours_ago))))
    msg.append(min(len(periods), 255))
    for p in periods:
        high = p.get("high_f", 127)
        low = p.get("low_f", 127)
        msg.append(max(0, min(255, p.get("period_id", 0))))
        msg.append(max(-128, min(127, int(high))) & 0xFF)
        msg.append(max(-128, min(127, int(low))) & 0xFF)
        msg.append(p.get("sky_code", 0) & 0x0F)
        msg.append(max(0, min(100, int(p.get("precip_pct", 0)))))
        wind_byte = ((p.get("wind_dir_nibble", 0) & 0x0F) << 4) | (
            p.get("wind_speed_5mph", 0) & 0x0F
        )
        msg.append(wind_byte)
        msg.append(p.get("condition_flags", 0) & 0xFF)
    return bytes(msg)


def unpack_forecast(data: bytes) -> dict:
    """Unpack a forecast message."""
    if len(data) < 2 or data[0] != MSG_FORECAST:
        raise ValueError("Invalid forecast message")
    location, offset = unpack_location(data, 1)
    if offset + 2 > len(data):
        raise ValueError("Forecast header truncated")
    issued_hours_ago = data[offset]
    count = data[offset + 1]
    offset += 2
    periods = []
    for _ in range(count):
        if offset + 7 > len(data):
            break
        pid = data[offset]
        high = struct.unpack_from("b", data, offset + 1)[0]
        low = struct.unpack_from("b", data, offset + 2)[0]
        sky = data[offset + 3]
        precip = data[offset + 4]
        wind_byte = data[offset + 5]
        flags = data[offset + 6]
        periods.append({
            "period_id": pid,
            "high_f": None if high == 127 else high,
            "low_f": None if low == 127 else low,
            "sky_code": sky,
            "precip_pct": precip,
            "wind_dir_nibble": (wind_byte >> 4) & 0x0F,
            "wind_dir": nibble_to_wind_dir((wind_byte >> 4) & 0x0F),
            "wind_speed_5mph": wind_byte & 0x0F,
            "wind_speed_mph": (wind_byte & 0x0F) * 5,
            "condition_flags": flags,
        })
        offset += 7
    return {
        "type": MSG_FORECAST,
        "location": location,
        "issued_hours_ago": issued_hours_ago,
        "periods": periods,
    }
