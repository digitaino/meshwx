"""MeshWX v5 wire codec — reference encoder and decoder.

This module is the normative implementation of ``docs/MeshWX_v5_Spec.md``.
It is deliberately dependency-free (standard library only, nothing from
``meshcore_weather``) so that an app developer can read it top to bottom and
port it to Swift, Kotlin or C without chasing imports.

Every v5 message is the ``data`` field of one MeshCore ``GRP_DATA`` packet on
the ``#meshwx`` channel with ``data_type = 0xFF10``.  ``data`` is at most 165
bytes and every message fits in one packet; only Text carries chunk numbers.

Every type but one travels bot -> app.  Request (type 9, spec 7B, new in
revision 6) is the app's `>` request flooded on the same channel: the bot
decodes it and never sends one.

Layout of the 4-byte common header::

    offset 0  u8   seq    per-bot sequence number, wraps 255 -> 0
    offset 1  u16  bot    first two bytes of the bot's public key, LE
    offset 3  u8   type   high nibble = message type, low nibble = flags

All multi-byte integers are little-endian.  Times are Unix minutes
(``seconds // 60``) as u32.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "DATA_TYPE",
    "MAX_DATA",
    "HEADER_SIZE",
    "TYPE_WARNING",
    "TYPE_CANCEL",
    "TYPE_DIGEST",
    "TYPE_OBS",
    "TYPE_FORECAST",
    "TYPE_TEXT",
    "TYPE_NOT_AVAILABLE",
    "TYPE_COVERAGE",
    "TYPE_REQUEST",
    "TYPE_NAMES",
    "SUBJECT_WARNING",
    "SUBJECT_AFD",
    "SUBJECT_SPACE",
    "SUBJECT_STORM_REPORTS",
    "SUBJECT_RAINFALL",
    "SUBJECT_METAR",
    "SUBJECT_HWO",
    "SUBJECT_NOWCAST",
    "SUBJECT_GENERAL",
    "REASON_NO_DATA",
    "REASON_UNKNOWN_LOCATION",
    "REASON_UNSUPPORTED",
    "REASON_BOT_ERROR",
    "REASON_RATE_LIMITED",
    "CANCEL_CANCELLED",
    "CANCEL_EXPIRED",
    "CANCEL_UPGRADED",
    "TAG_TORNADO_NONE",
    "TAG_TORNADO_POSSIBLE",
    "TAG_TORNADO_RADAR_INDICATED",
    "TAG_TORNADO_OBSERVED",
    "FLOOD_SOURCE_NONE",
    "FLOOD_SOURCE_RADAR",
    "FLOOD_SOURCE_RADAR_AND_GAUGE",
    "FLOOD_SOURCE_OBSERVED",
    "FLOOD_DAMAGE_NONE",
    "FLOOD_DAMAGE_CONSIDERABLE",
    "FLOOD_DAMAGE_CATASTROPHIC",
    "FLAG_WARNING_UPDATE",
    "FLAG_WARNING_ISSUED",
    "FLAG_OBS_AGES",
    "FLAG_COVERAGE_ZONES_CUT",
    "FLAG_COVERAGE_OFFICES_CUT",
    "MAX_TEXT_BYTES",
    "MAX_TEXT_CHUNKS",
    "MAX_POLYGON_VERTICES",
    "MAX_AREA_RUNS",
    "MAX_DIGEST_ENTRIES",
    "MAX_STATIONS",
    "MAX_STATIONS_WITH_AGES",
    "MAX_ISSUED_BEFORE_EXPIRY",
    "OBS_AGE_STEP_MIN",
    "OBS_AGE_MAX_MIN",
    "MAX_PERIODS",
    "MAX_COVERAGE_OFFICES",
    "MAX_COVERAGE_RUNS",
    "REQUEST_SENDER_BYTES",
    "REQUEST_BOT_ANY",
    "MAX_REQUEST_TEXT",
    "MIN_REQUEST_SIZE",
    "Header",
    "Request",
    "encode_header",
    "encode_warning",
    "encode_cancel",
    "encode_digest",
    "encode_obs",
    "encode_forecast",
    "encode_coverage",
    "encode_text",
    "text_chunks",
    "encode_not_available",
    "encode_request",
    "decode_request",
    "decode",
    "areas_from_ugcs",
    "wind_dir_nibble",
    "nibble_to_compass",
]

# --------------------------------------------------------------------------
# Transport constants (spec 2)
# --------------------------------------------------------------------------

#: MeshCore ``data_type`` carrying a v5 message (development range).
DATA_TYPE = 0xFF10

#: Largest ``data`` payload the transport accepts, in bytes.
MAX_DATA = 165

#: Size of the common header.
HEADER_SIZE = 4

# Message types (high nibble of the type byte), spec 2.2.
TYPE_WARNING = 1
TYPE_CANCEL = 2
TYPE_DIGEST = 3
TYPE_OBS = 4
TYPE_FORECAST = 5
TYPE_TEXT = 6
TYPE_NOT_AVAILABLE = 7
TYPE_COVERAGE = 8
#: App -> bot: a `>` request flooded on #meshwx (spec 7B, revision 6).  It is
#: the only type an app sends; the bot never transmits one.
TYPE_REQUEST = 9

TYPE_NAMES = {
    TYPE_WARNING: "warning",
    TYPE_CANCEL: "cancel",
    TYPE_DIGEST: "digest",
    TYPE_OBS: "observations",
    TYPE_FORECAST: "forecast",
    TYPE_TEXT: "text",
    TYPE_NOT_AVAILABLE: "not_available",
    TYPE_COVERAGE: "coverage",
    TYPE_REQUEST: "request",
}

# Text subjects (spec 8.1).
SUBJECT_WARNING = 0
SUBJECT_AFD = 1
SUBJECT_SPACE = 2
SUBJECT_STORM_REPORTS = 3
SUBJECT_RAINFALL = 4
SUBJECT_METAR = 5
SUBJECT_HWO = 6
SUBJECT_NOWCAST = 7
SUBJECT_GENERAL = 8

# Not-available reasons (spec 8.3).
REASON_NO_DATA = 0
REASON_UNKNOWN_LOCATION = 1
REASON_UNSUPPORTED = 2
REASON_BOT_ERROR = 3
REASON_RATE_LIMITED = 4

# Cancel flags nibble (spec 4).  Distinct from the not-available reasons.
CANCEL_CANCELLED = 0
CANCEL_EXPIRED = 1
CANCEL_UPGRADED = 2

# Warning tag byte, bits 7-6 (spec 3).
TAG_TORNADO_NONE = 0
TAG_TORNADO_POSSIBLE = 1
TAG_TORNADO_RADAR_INDICATED = 2
TAG_TORNADO_OBSERVED = 3

# Warning tag byte, bits 5-4.
FLOOD_SOURCE_NONE = 0
FLOOD_SOURCE_RADAR = 1
FLOOD_SOURCE_RADAR_AND_GAUGE = 2
FLOOD_SOURCE_OBSERVED = 3

# Warning tag byte, bits 3-2.
FLOOD_DAMAGE_NONE = 0
FLOOD_DAMAGE_CONSIDERABLE = 1
FLOOD_DAMAGE_CATASTROPHIC = 2

# Warning tag byte, bits 1-0 (structural, set by the encoder).
_TAG_POLYGON = 0x02
_TAG_AREAS = 0x01

#: Warning flags nibble, bit 0: this identity was already sent.
FLAG_WARNING_UPDATE = 0x1

# Warning flags nibble, bit 1: the issue time follows the polygon and the area
# list (spec 3, revision 5).  The bit lives in the flags nibble and not in the
# tag byte because that byte has no spare bit: 7-6 tornado, 5-4 flood source,
# 3-2 flood damage, 1 polygon, 0 areas.
FLAG_WARNING_ISSUED = 0x2

#: Observations flags nibble, bit 0: per-station ages follow the station
#: records (spec 6, revision 5).
FLAG_OBS_AGES = 0x1

# Coverage flags nibble (spec 7A).  A set bit means the list on the wire is
# shorter than what the bot really covers, so absence proves nothing.
FLAG_COVERAGE_ZONES_CUT = 0x1
FLAG_COVERAGE_OFFICES_CUT = 0x2

# Counts and limits.
MAX_TEXT_BYTES = MAX_DATA - 8  # 157
MAX_TEXT_CHUNKS = 8
MIN_POLYGON_VERTICES = 3
MAX_POLYGON_VERTICES = 30
MAX_AREA_RUNS = 30
MAX_DIGEST_ENTRIES = 25
MAX_STATIONS = 14
# A batch of 14 stations is already 163 bytes, so the age nibbles (one per
# station, packed two to a byte) do not fit beside a full one: 9 + 11 x 14 + 7
# is 170.  Thirteen stations with their ages are 159 (spec 6, revision 5).
MAX_STATIONS_WITH_AGES = 13
#: One age nibble step, in minutes.
OBS_AGE_STEP_MIN = 10
#: The largest age a nibble carries: 15 steps, read as "150 minutes or more".
OBS_AGE_MAX_MIN = 15 * OBS_AGE_STEP_MIN
#: The largest issue-to-expiry gap the u16 carries (45.5 days), saturating.
MAX_ISSUED_BEFORE_EXPIRY = 0xFFFF
MAX_PERIODS = 14
# Coverage: both maxima at once are 14 + 24 + 1 + 120 = 159 bytes, inside
# one packet, so a full office list never costs a zone run or the reverse.
MAX_COVERAGE_OFFICES = 24
MAX_COVERAGE_RUNS = 30
# Request (spec 7B): six bytes of the sender's public key — the prefix a DM
# identifies the same phone by — then the sender's own Unix seconds, then the
# `>` text, which ends the packet.
REQUEST_SENDER_BYTES = 6
#: `bot` in a Request: every bot on the channel answers it.
REQUEST_BOT_ANY = 0xFFFF
MAX_REQUEST_TEXT = 40
#: Header + sender + ts + the one `>` byte a request cannot do without.
MIN_REQUEST_SIZE = HEADER_SIZE + REQUEST_SENDER_BYTES + 4 + 1   # 15

# Sentinels.
_TEMP_UNKNOWN = -128
_U8_UNKNOWN = 255
_FORECAST_TEMP_UNKNOWN = 127
_SKY_OTHER = 15

_COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    """The decoded 4-byte common header."""

    seq: int
    bot: int
    type: int
    flags: int

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "bot": self.bot,
            "type": self.type,
            "name": TYPE_NAMES.get(self.type, "unknown"),
            "flags": self.flags,
        }


def _check_header(seq: int, bot: int, mtype: int, flags: int) -> None:
    if not (0 <= seq <= 255):
        raise ValueError(f"seq must be 0..255, got {seq}")
    if not (0 <= bot <= 0xFFFF):
        raise ValueError(f"bot must be 0..65535, got {bot}")
    if not (0 <= mtype <= 15):
        raise ValueError(f"type must be 0..15, got {mtype}")
    if not (0 <= flags <= 15):
        raise ValueError(f"flags must be 0..15, got {flags}")


def _check_size(data: bytes, what: str) -> bytes:
    if len(data) > MAX_DATA:
        raise ValueError(
            f"{what} is {len(data)} bytes, over the {MAX_DATA}-byte limit"
        )
    return data


def _u8(value: int, name: str) -> int:
    if not (0 <= value <= 255):
        raise ValueError(f"{name} must be 0..255, got {value}")
    return value


def _u16(value: int, name: str) -> int:
    if not (0 <= value <= 0xFFFF):
        raise ValueError(f"{name} must be 0..65535, got {value}")
    return value


def _u32(value: int, name: str) -> int:
    if not (0 <= value <= 0xFFFFFFFF):
        raise ValueError(f"{name} must be 0..4294967295, got {value}")
    return value


def _pack_i24(value: int, name: str) -> bytes:
    """Three-byte little-endian two's complement."""
    if not (-(1 << 23) <= value < (1 << 23)):
        raise ValueError(f"{name} does not fit in i24: {value}")
    return (value & 0xFFFFFF).to_bytes(3, "little")


def _unpack_i24(data: bytes, off: int) -> int:
    raw = int.from_bytes(data[off:off + 3], "little")
    return raw - (1 << 24) if raw & 0x800000 else raw


def _need(data: bytes, end: int, what: str) -> None:
    if len(data) < end:
        raise ValueError(
            f"truncated {what}: need {end} bytes, have {len(data)}"
        )


def wind_dir_nibble(deg) -> int:
    """Degrees true -> 16-point compass nibble (0 N, 4 E, 8 S, 12 W).

    ``None`` means calm or unknown and maps to 0 (spec 6: "0 with speed 0 =
    calm").
    """
    if deg is None:
        return 0
    return int(round(float(deg) / 22.5)) % 16


def nibble_to_compass(n: int) -> str:
    """16-point compass nibble -> ``"N"``, ``"NNE"``, ..."""
    return _COMPASS[int(n) & 0x0F]


def _sky(value) -> int:
    """Sky code, ``None`` -> 15 (other)."""
    if value is None:
        return _SKY_OTHER
    return _u8(int(value), "sky") & 0x0F


# --------------------------------------------------------------------------
# Header (spec 2.2)
# --------------------------------------------------------------------------


def encode_header(seq: int, bot: int, mtype: int, flags: int = 0) -> bytes:
    """Pack the 4-byte common header."""
    _check_header(seq, bot, mtype, flags)
    return struct.pack("<BHB", seq, bot, (mtype << 4) | flags)


def _decode_header(data: bytes) -> Header:
    _need(data, HEADER_SIZE, "header")
    seq, bot, tb = struct.unpack_from("<BHB", data, 0)
    return Header(seq=seq, bot=bot, type=tb >> 4, flags=tb & 0x0F)


# --------------------------------------------------------------------------
# Warning (type 1, spec 3)
# --------------------------------------------------------------------------


def encode_warning(
    seq: int,
    bot: int,
    *,
    event: int,
    office: int,
    etn: int,
    expires_min: int,
    tornado: int = 0,
    flood_source: int = 0,
    flood_damage: int = 0,
    hail_qin: int = 0,
    wind_mph: int = 0,
    polygon: "list[tuple[float, float]] | None" = None,
    areas: "list[tuple[int, bool, int, int]] | None" = None,
    update: bool = False,
    issued_min: "int | None" = None,
) -> bytes:
    """Encode a Warning.

    ``polygon`` is a list of ``(lat, lon)`` in degrees; the first vertex is
    absolute at 0.0001 deg and the rest are deltas at 0.001 deg.  ``areas`` is
    a list of ``(state_index, is_county, start, run)`` runs of UGC numbers.
    ``hail_qin`` is the hail tag in quarter inches (4 = 1.00 in).

    ``issued_min`` is when the product was issued, in Unix minutes.  It goes
    on the wire as the minutes between the issue time and ``expires_min``
    (u16, two bytes rather than four), saturating at
    ``MAX_ISSUED_BEFORE_EXPIRY``; a warning issued after its own expiry, which
    no real product is, encodes as 0 rather than failing.  The two bytes are
    appended last so that a decoder written before revision 5 stops after the
    area list and never sees them.
    """
    if not (0 <= tornado <= 3):
        raise ValueError(f"tornado tag must be 0..3, got {tornado}")
    if not (0 <= flood_source <= 3):
        raise ValueError(f"flood_source must be 0..3, got {flood_source}")
    if not (0 <= flood_damage <= 3):
        raise ValueError(f"flood_damage must be 0..3, got {flood_damage}")

    tags = (tornado << 6) | (flood_source << 4) | (flood_damage << 2)
    if polygon:
        tags |= _TAG_POLYGON
    if areas:
        tags |= _TAG_AREAS

    flags = FLAG_WARNING_UPDATE if update else 0
    if issued_min is not None:
        flags |= FLAG_WARNING_ISSUED

    out = bytearray(encode_header(seq, bot, TYPE_WARNING, flags))
    out += struct.pack(
        "<BBHIBBB",
        _u8(event, "event"),
        _u8(office, "office"),
        _u16(etn, "etn"),
        _u32(expires_min, "expires_min"),
        tags,
        _u8(hail_qin, "hail_qin"),
        _u8(wind_mph, "wind_mph"),
    )

    if polygon:
        out += _encode_polygon(polygon)
    if areas:
        out += _encode_areas(areas)
    if issued_min is not None:
        before = _u32(expires_min, "expires_min") - _u32(issued_min, "issued_min")
        out += struct.pack("<H", max(0, min(MAX_ISSUED_BEFORE_EXPIRY, before)))

    return _check_size(bytes(out), "warning")


def _encode_polygon(polygon) -> bytes:
    n = len(polygon)
    if not (MIN_POLYGON_VERTICES <= n <= MAX_POLYGON_VERTICES):
        raise ValueError(
            f"polygon needs {MIN_POLYGON_VERTICES}..{MAX_POLYGON_VERTICES} "
            f"vertices, got {n}"
        )
    lat0, lon0 = polygon[0]
    lat0_i = int(round(float(lat0) * 10000))
    lon0_i = int(round(float(lon0) * 10000))
    out = bytearray()
    out.append(n)
    out += _pack_i24(lat0_i, "polygon lat0")
    out += _pack_i24(lon0_i, "polygon lon0")

    # Deltas are taken against the *reconstructed* previous vertex so that a
    # decoder walking the chain never accumulates drift.
    prev_lat = lat0_i / 10000.0
    prev_lon = lon0_i / 10000.0
    for i, (lat, lon) in enumerate(polygon[1:], start=1):
        dlat = int(round((float(lat) - prev_lat) * 1000))
        dlon = int(round((float(lon) - prev_lon) * 1000))
        for value, axis in ((dlat, "lat"), (dlon, "lon")):
            if not (-32768 <= value <= 32767):
                raise ValueError(
                    f"polygon vertex {i} {axis} delta {value / 1000.0:+.3f} deg "
                    f"does not fit in i16 at 0.001 deg resolution "
                    f"(max +/-32.767 deg); split the polygon or re-anchor it"
                )
        out += struct.pack("<hh", dlat, dlon)
        prev_lat += dlat / 1000.0
        prev_lon += dlon / 1000.0
    return bytes(out)


def _encode_areas(
    areas, minimum: int = 1, maximum: int = MAX_AREA_RUNS
) -> bytes:
    """A counted list of UGC runs: ``k``, then 4 bytes per run (spec 3).

    Shared by the Warning's area list, which must carry at least one run when
    the tag bit says it is there, and by Coverage, where an empty list is a
    legitimate answer (spec 7A).
    """
    k = len(areas)
    if not (minimum <= k <= maximum):
        raise ValueError(f"area list needs {minimum}..{maximum} runs, got {k}")
    out = bytearray()
    out.append(k)
    for state, is_county, start, run in areas:
        if not (0 <= state <= 127):
            raise ValueError(f"state index must be 0..127, got {state}")
        if not (1 <= run <= 255):
            raise ValueError(f"run length must be 1..255, got {run}")
        out += struct.pack(
            "<BHB",
            (0x80 if is_county else 0) | state,
            _u16(start, "area start"),
            run,
        )
    return bytes(out)


def _decode_warning(data: bytes, hdr: Header) -> dict:
    _need(data, 15, "warning")
    event, office, etn, expires, tags, hail, wind = struct.unpack_from(
        "<BBHIBBB", data, 4
    )
    out = hdr.as_dict()
    out.update(
        event=event,
        office=office,
        etn=etn,
        expires_min=expires,
        tornado=(tags >> 6) & 0x3,
        flood_source=(tags >> 4) & 0x3,
        flood_damage=(tags >> 2) & 0x3,
        hail_qin=hail,
        wind_mph=wind,
        update=bool(hdr.flags & FLAG_WARNING_UPDATE),
        polygon=None,
        areas=None,
        issued_min=None,
    )

    off = 15
    if tags & _TAG_POLYGON:
        _need(data, off + 1, "polygon count")
        n = data[off]
        off += 1
        _need(data, off + 6 + 4 * (n - 1), "polygon")
        lat = _unpack_i24(data, off) / 10000.0
        lon = _unpack_i24(data, off + 3) / 10000.0
        off += 6
        points = [[round(lat, 4), round(lon, 4)]]
        for _ in range(n - 1):
            dlat, dlon = struct.unpack_from("<hh", data, off)
            off += 4
            lat += dlat / 1000.0
            lon += dlon / 1000.0
            points.append([round(lat, 4), round(lon, 4)])
        out["polygon"] = points

    if tags & _TAG_AREAS:
        out["areas"], off = _decode_areas(data, off)

    # Revision 5: the issue time, as minutes before `expires`, after the
    # variable blocks.  A decoder that does not know the flag stops above.
    if hdr.flags & FLAG_WARNING_ISSUED:
        _need(data, off + 2, "warning issue time")
        out["issued_min"] = expires - struct.unpack_from("<H", data, off)[0]
        off += 2

    return out


def _decode_areas(data: bytes, off: int) -> "tuple[list[dict], int]":
    """Read a counted list of UGC runs, returning it and the new offset."""
    _need(data, off + 1, "area count")
    k = data[off]
    off += 1
    _need(data, off + 4 * k, "area runs")
    runs = []
    for _ in range(k):
        state, start, run = struct.unpack_from("<BHB", data, off)
        off += 4
        runs.append(
            {
                "state": state & 0x7F,
                "county": bool(state & 0x80),
                "start": start,
                "run": run,
            }
        )
    return runs, off


# --------------------------------------------------------------------------
# Cancel (type 2, spec 4)
# --------------------------------------------------------------------------


def encode_cancel(
    seq: int, bot: int, *, event: int, office: int, etn: int, reason: int = 0
) -> bytes:
    """Encode a Cancel.  ``reason`` rides in the flags nibble (spec 4:
    0 cancelled, 1 expired early, 2 upgraded)."""
    if not (0 <= reason <= 15):
        raise ValueError(f"cancel reason must be 0..15, got {reason}")
    out = encode_header(seq, bot, TYPE_CANCEL, reason) + struct.pack(
        "<BBH",
        _u8(event, "event"),
        _u8(office, "office"),
        _u16(etn, "etn"),
    )
    return _check_size(out, "cancel")


def _decode_cancel(data: bytes, hdr: Header) -> dict:
    _need(data, 8, "cancel")
    event, office, etn = struct.unpack_from("<BBH", data, 4)
    out = hdr.as_dict()
    out.update(event=event, office=office, etn=etn, reason=hdr.flags)
    return out


# --------------------------------------------------------------------------
# Digest (type 3, spec 5)
# --------------------------------------------------------------------------


def encode_digest(
    seq: int,
    bot: int,
    *,
    now_min: int,
    feed_health: int,
    entries: "list[tuple[int, int, int, int]]",
) -> bytes:
    """Encode a Digest.

    ``entries`` are ``(event, office, etn, expires_min)`` with an *absolute*
    expiry; the wire carries ``expires_rel = expires_min - now_min`` clamped
    into a u16.
    """
    if len(entries) > MAX_DIGEST_ENTRIES:
        raise ValueError(
            f"digest holds at most {MAX_DIGEST_ENTRIES} entries, "
            f"got {len(entries)}"
        )
    out = bytearray(encode_header(seq, bot, TYPE_DIGEST))
    out += struct.pack(
        "<IBB",
        _u32(now_min, "now_min"),
        _u8(feed_health, "feed_health"),
        len(entries),
    )
    for event, office, etn, expires_min in entries:
        rel = max(0, int(expires_min) - int(now_min))
        rel = min(rel, 0xFFFF)
        out += struct.pack(
            "<BBHH",
            _u8(event, "event"),
            _u8(office, "office"),
            _u16(etn, "etn"),
            rel,
        )
    return _check_size(bytes(out), "digest")


def _decode_digest(data: bytes, hdr: Header) -> dict:
    _need(data, 10, "digest")
    now, feed_health, count = struct.unpack_from("<IBB", data, 4)
    _need(data, 10 + 6 * count, "digest entries")
    entries = []
    off = 10
    for _ in range(count):
        event, office, etn, rel = struct.unpack_from("<BBHH", data, off)
        off += 6
        entries.append(
            {
                "event": event,
                "office": office,
                "etn": etn,
                "expires_rel": rel,
                "expires_min": now + rel,
            }
        )
    out = hdr.as_dict()
    out.update(now_min=now, feed_health=feed_health, entries=entries)
    return out


# --------------------------------------------------------------------------
# Observations (type 4, spec 6)
# --------------------------------------------------------------------------


def _i8_or(value, sentinel: int, name: str) -> int:
    if value is None:
        return sentinel
    v = int(round(float(value)))
    if not (-128 <= v <= 127):
        raise ValueError(f"{name} must be -128..127, got {v}")
    return v


def _u8_or(value, sentinel: int, name: str) -> int:
    if value is None:
        return sentinel
    v = int(round(float(value)))
    if not (0 <= v <= 255):
        raise ValueError(f"{name} must be 0..255, got {v}")
    return v


def _age_nibble(age_min) -> int:
    """One station's age as a 10-minute step, 0..15, rounding half up.

    Rounding to the nearest step rather than down keeps the error symmetric:
    a report is never shown as more than 4 minutes fresher than it is.
    """
    steps = (int(round(float(age_min))) + OBS_AGE_STEP_MIN // 2) // OBS_AGE_STEP_MIN
    return max(0, min(15, steps))


def _encode_ages(ages) -> bytes:
    """The per-station age block: one nibble each, two stations to a byte,
    station i in the low nibble of byte i // 2 when i is even and the high
    nibble when it is odd.  An odd station count leaves the last high nibble 0.
    """
    block = bytearray((len(ages) + 1) // 2)
    for i, age in enumerate(ages):
        nib = _age_nibble(age)
        block[i // 2] |= (nib << 4) if i % 2 else nib
    return bytes(block)


def encode_obs(
    seq: int, bot: int, *, ts_min: int, stations: "list[dict]"
) -> bytes:
    """Encode an Observations batch (1..14 stations, 11 bytes each).

    A station carrying ``age_min`` — how many minutes older than ``ts_min``
    its own report is — puts the batch into the revision 5 form: flags nibble
    bit 0 set and a trailing block of age nibbles.  The ages are all or
    nothing, so a batch where only some stations know their age is refused
    rather than sent with the rest guessed at; and because the block costs
    ``ceil(n / 2)`` bytes on top of an already 163-byte full batch, 14
    stations with ages do not fit in one packet (see ``MAX_STATIONS_WITH_AGES``).
    """
    n = len(stations)
    if not (1 <= n <= MAX_STATIONS):
        raise ValueError(f"observations need 1..{MAX_STATIONS} stations, got {n}")
    ages = [s.get("age_min") for s in stations]
    known = [a is not None for a in ages]
    if any(known) and not all(known):
        raise ValueError(
            "per-station ages must cover every station in the batch or none: "
            f"{sum(known)} of {n} carry age_min"
        )
    out = bytearray(encode_header(seq, bot, TYPE_OBS, FLAG_OBS_AGES if all(known) else 0))
    out += struct.pack("<IB", _u32(ts_min, "ts_min"), n)
    for s in stations:
        pressure = s.get("pressure_inhg")
        if pressure is None:
            pressure_b = _U8_UNKNOWN
        else:
            pressure_b = int(round((float(pressure) - 29.00) * 100))
            if not (0 <= pressure_b <= 254):
                raise ValueError(
                    f"pressure_inhg {pressure} out of encodable range "
                    f"29.00..31.54 inHg"
                )
        out += struct.pack(
            "<HbbBBBBBBb",
            _u16(int(s["station"]), "station"),
            _i8_or(s.get("temp_f"), _TEMP_UNKNOWN, "temp_f"),
            _i8_or(s.get("dewpoint_f"), _TEMP_UNKNOWN, "dewpoint_f"),
            (wind_dir_nibble(s.get("wind_dir_deg")) << 4) | _sky(s.get("sky")),
            _u8_or(s.get("wind_mph"), _U8_UNKNOWN, "wind_mph"),
            _u8_or(s.get("gust_mph"), 0, "gust_mph"),
            _u8_or(s.get("visibility_mi"), _U8_UNKNOWN, "visibility_mi"),
            pressure_b,
            _u8_or(s.get("humidity_pct"), _U8_UNKNOWN, "humidity_pct"),
            _i8_or(s.get("feels_delta_f"), 0, "feels_delta_f"),
        )
    if all(known):
        out += _encode_ages(ages)
    return _check_size(bytes(out), "observations")


def _decode_obs(data: bytes, hdr: Header) -> dict:
    _need(data, 9, "observations")
    ts, n = struct.unpack_from("<IB", data, 4)
    _need(data, 9 + 11 * n, "observation stations")

    # Revision 5: the age nibbles sit after the station records, so a decoder
    # that does not know the flag reads the batch exactly as it always did.
    ages: "list[int | None]" = [None] * n
    if hdr.flags & FLAG_OBS_AGES:
        base = 9 + 11 * n
        _need(data, base + (n + 1) // 2, "observation ages")
        for i in range(n):
            byte = data[base + i // 2]
            ages[i] = ((byte >> 4) if i % 2 else (byte & 0x0F)) * OBS_AGE_STEP_MIN

    stations = []
    off = 9
    for i in range(n):
        (
            station,
            temp,
            dewpoint,
            dir_sky,
            wind,
            gust,
            vis,
            pressure,
            humidity,
            feels,
        ) = struct.unpack_from("<HbbBBBBBBb", data, off)
        off += 11
        nib = dir_sky >> 4
        stations.append(
            {
                "station": station,
                "temp_f": None if temp == _TEMP_UNKNOWN else temp,
                "dewpoint_f": None if dewpoint == _TEMP_UNKNOWN else dewpoint,
                "wind_dir_deg": nib * 22.5,
                "wind_dir": nibble_to_compass(nib),
                "sky": dir_sky & 0x0F,
                "wind_mph": None if wind == _U8_UNKNOWN else wind,
                "gust_mph": gust,
                "visibility_mi": None if vis == _U8_UNKNOWN else vis,
                "pressure_inhg": (
                    None
                    if pressure == _U8_UNKNOWN
                    else round(29.00 + pressure / 100.0, 2)
                ),
                "humidity_pct": None if humidity == _U8_UNKNOWN else humidity,
                "feels_delta_f": feels,
                # Minutes this station's own report is older than `ts_min`;
                # None when the batch predates revision 5 and does not say.
                "age_min": ages[i],
            }
        )
    out = hdr.as_dict()
    out.update(ts_min=ts, stations=stations)
    return out


# --------------------------------------------------------------------------
# Forecast (type 5, spec 7)
# --------------------------------------------------------------------------


def encode_forecast(
    seq: int,
    bot: int,
    *,
    point: int,
    issued_min: int,
    first_period: int,
    periods: "list[dict]",
) -> bytes:
    """Encode a point Forecast (1..14 periods, 5 bytes each)."""
    n = len(periods)
    if not (1 <= n <= MAX_PERIODS):
        raise ValueError(f"forecast needs 1..{MAX_PERIODS} periods, got {n}")
    out = bytearray(encode_header(seq, bot, TYPE_FORECAST))
    out += struct.pack(
        "<HIBB",
        _u16(point, "point"),
        _u32(issued_min, "issued_min"),
        _u8(first_period, "first_period"),
        n,
    )
    for p in periods:
        cond = _sky(p.get("sky"))
        if p.get("thunder"):
            cond |= 0x10
        if p.get("wintry"):
            cond |= 0x20
        if p.get("windy"):
            cond |= 0x40
        if p.get("fog"):
            cond |= 0x80
        speed = p.get("wind_mph")
        speed_nib = 0 if speed is None else min(15, int(round(float(speed) / 5)))
        out += struct.pack(
            "<bbBBB",
            _i8_or(p.get("high_f"), _FORECAST_TEMP_UNKNOWN, "high_f"),
            _i8_or(p.get("low_f"), _FORECAST_TEMP_UNKNOWN, "low_f"),
            _u8_or(p.get("pop_pct"), _U8_UNKNOWN, "pop_pct"),
            cond,
            (wind_dir_nibble(p.get("wind_dir_deg")) << 4) | speed_nib,
        )
    return _check_size(bytes(out), "forecast")


def _decode_forecast(data: bytes, hdr: Header) -> dict:
    _need(data, 12, "forecast")
    point, issued, first, n = struct.unpack_from("<HIBB", data, 4)
    _need(data, 12 + 5 * n, "forecast periods")
    periods = []
    off = 12
    for _ in range(n):
        high, low, pop, cond, wind = struct.unpack_from("<bbBBB", data, off)
        off += 5
        nib = wind >> 4
        periods.append(
            {
                "high_f": None if high == _FORECAST_TEMP_UNKNOWN else high,
                "low_f": None if low == _FORECAST_TEMP_UNKNOWN else low,
                "pop_pct": None if pop == _U8_UNKNOWN else pop,
                "sky": cond & 0x0F,
                "thunder": bool(cond & 0x10),
                "wintry": bool(cond & 0x20),
                "windy": bool(cond & 0x40),
                "fog": bool(cond & 0x80),
                "wind_dir_deg": nib * 22.5,
                "wind_dir": nibble_to_compass(nib),
                "wind_mph": (wind & 0x0F) * 5,
            }
        )
    out = hdr.as_dict()
    out.update(
        point=point,
        issued_min=issued,
        first_period=first,
        periods=periods,
    )
    return out


# --------------------------------------------------------------------------
# Coverage (type 8, spec 7A)
# --------------------------------------------------------------------------


def encode_coverage(
    seq: int,
    bot: int,
    *,
    lat: float,
    lon: float,
    radius_km: int = 0,
    stations: int = 0,
    offices: "list[int] | None" = None,
    areas: "list[tuple[int, bool, int, int]] | None" = None,
    zones_cut: bool = False,
    offices_cut: bool = False,
) -> bytes:
    """Encode a Coverage message: what the bot carries, stated by the bot.

    ``lat`` and ``lon`` are the coverage centre in degrees, ``radius_km`` the
    circle around it (0 = no circle), ``stations`` the most stations one
    hourly Observations packet may carry (0 = none).  ``offices`` are indices
    into ``index.json`` ``offices``; ``areas`` are the same ``(state_index,
    is_county, start, run)`` runs a Warning's area list uses.  A cut flag says
    that list is shorter than what the bot really covers, so a code missing
    from it means "not stated", never "not covered".
    """
    offices = list(offices or [])
    areas = list(areas or [])
    if len(offices) > MAX_COVERAGE_OFFICES:
        raise ValueError(
            f"coverage holds at most {MAX_COVERAGE_OFFICES} offices, "
            f"got {len(offices)}"
        )
    flags = (FLAG_COVERAGE_ZONES_CUT if zones_cut else 0) | (
        FLAG_COVERAGE_OFFICES_CUT if offices_cut else 0
    )
    out = bytearray(encode_header(seq, bot, TYPE_COVERAGE, flags))
    out += _pack_i24(int(round(float(lat) * 10000)), "coverage lat")
    out += _pack_i24(int(round(float(lon) * 10000)), "coverage lon")
    out += struct.pack(
        "<HBB",
        _u16(int(radius_km), "radius_km"),
        _u8(int(stations), "stations"),
        len(offices),
    )
    for office in offices:
        out.append(_u8(int(office), "office"))
    out += _encode_areas(areas, minimum=0, maximum=MAX_COVERAGE_RUNS)
    return _check_size(bytes(out), "coverage")


def _decode_coverage(data: bytes, hdr: Header) -> dict:
    _need(data, 14, "coverage")
    lat = _unpack_i24(data, 4) / 10000.0
    lon = _unpack_i24(data, 7) / 10000.0
    radius, stations, n = struct.unpack_from("<HBB", data, 10)
    _need(data, 14 + n, "coverage offices")
    offices = list(data[14:14 + n])
    areas, _end = _decode_areas(data, 14 + n)
    out = hdr.as_dict()
    out.update(
        lat=round(lat, 4),
        lon=round(lon, 4),
        radius_km=radius,
        stations=stations,
        offices=offices,
        areas=areas,
        zones_cut=bool(hdr.flags & FLAG_COVERAGE_ZONES_CUT),
        offices_cut=bool(hdr.flags & FLAG_COVERAGE_OFFICES_CUT),
    )
    return out


# --------------------------------------------------------------------------
# Text (type 6, spec 8.1)
# --------------------------------------------------------------------------


def encode_text(
    seq: int,
    bot: int,
    *,
    subject: int,
    group: int,
    idx: int,
    total: int,
    text: str,
) -> bytes:
    """Encode one Text chunk."""
    if not (1 <= total <= MAX_TEXT_CHUNKS):
        raise ValueError(f"total must be 1..{MAX_TEXT_CHUNKS}, got {total}")
    if not (0 <= idx < total):
        raise ValueError(f"idx must be 0..{total - 1}, got {idx}")
    body = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if len(body) > MAX_TEXT_BYTES:
        raise ValueError(
            f"text chunk is {len(body)} bytes, over the "
            f"{MAX_TEXT_BYTES}-byte limit"
        )
    out = encode_header(seq, bot, TYPE_TEXT) + struct.pack(
        "<BBBB",
        _u8(subject, "subject"),
        _u8(group, "group"),
        idx,
        total,
    ) + body
    return _check_size(out, "text")


def text_chunks(
    seq_start: int, bot: int, *, subject: int, text: str
) -> "list[bytes]":
    """Split ``text`` into Text messages.

    Chunks never split a UTF-8 code point, carry at most 157 text bytes,
    share ``group = seq_start & 0xFF``, and use consecutive sequence numbers
    starting at ``seq_start`` (wrapping 255 -> 0).
    """
    _check_header(seq_start, bot, TYPE_TEXT, 0)
    body = text.encode("utf-8")
    parts: "list[bytes]" = []
    pos = 0
    while pos < len(body) or not parts:
        end = min(pos + MAX_TEXT_BYTES, len(body))
        # Back off to a UTF-8 code point boundary: continuation bytes are
        # 0b10xxxxxx.
        while end > pos and end < len(body) and (body[end] & 0xC0) == 0x80:
            end -= 1
        if end == pos and pos < len(body):
            raise ValueError("a single code point exceeds the chunk size")
        parts.append(body[pos:end])
        pos = end
    if len(parts) > MAX_TEXT_CHUNKS:
        raise ValueError(
            f"text needs {len(parts)} chunks, over the "
            f"{MAX_TEXT_CHUNKS}-chunk limit ({MAX_TEXT_CHUNKS * MAX_TEXT_BYTES}"
            f" bytes of UTF-8)"
        )
    group = seq_start & 0xFF
    total = len(parts)
    return [
        encode_text(
            (seq_start + i) & 0xFF,
            bot,
            subject=subject,
            group=group,
            idx=i,
            total=total,
            text=part.decode("utf-8"),
        )
        for i, part in enumerate(parts)
    ]


def _decode_text(data: bytes, hdr: Header) -> dict:
    _need(data, 8, "text")
    subject, group, idx, total = struct.unpack_from("<BBBB", data, 4)
    out = hdr.as_dict()
    out.update(
        subject=subject,
        group=group,
        idx=idx,
        total=total,
        text=data[8:].decode("utf-8"),
    )
    return out


# --------------------------------------------------------------------------
# Not available (type 7, spec 8.3)
# --------------------------------------------------------------------------


def encode_not_available(seq: int, bot: int, *, request: str, reason: int) -> bytes:
    """Encode a Not-available reply.

    ``request`` is the request string (or just its first letter); the wire
    carries the ASCII code of that first letter.
    """
    if not request:
        raise ValueError("request must not be empty")
    letter = request.lstrip(">").lstrip()
    if not letter:
        raise ValueError("request has no letter after the '>' prefix")
    code = ord(letter[0])
    if not (0 <= code <= 255):
        raise ValueError(f"request letter {letter[0]!r} is not ASCII")
    out = encode_header(seq, bot, TYPE_NOT_AVAILABLE) + struct.pack(
        "<BB", code, _u8(reason, "reason")
    )
    return _check_size(out, "not_available")


def _decode_not_available(data: bytes, hdr: Header) -> dict:
    _need(data, 6, "not_available")
    code, reason = struct.unpack_from("<BB", data, 4)
    out = hdr.as_dict()
    out.update(request=chr(code), request_code=code, reason=reason)
    return out


# --------------------------------------------------------------------------
# Request (type 9, spec 7B) — app -> bot, the only message a bot receives
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """One decoded Request datagram.

    ``sender_prefix`` is the first six bytes of the sender's public key as 12
    lower-case hex characters: exactly the prefix a DM from the same phone
    carries, so a request by datagram and one by DM are one sender to the
    bot's limits and its copy rule (spec 7B).
    """

    seq: int
    bot: int
    sender_prefix: str
    ts: int
    text: str
    flags: int = 0

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "bot": self.bot,
            "type": TYPE_REQUEST,
            "name": TYPE_NAMES[TYPE_REQUEST],
            "flags": self.flags,
            "sender": self.sender_prefix,
            "ts": self.ts,
            "text": self.text,
        }

    @property
    def for_any_bot(self) -> bool:
        return self.bot == REQUEST_BOT_ANY


def _sender_bytes(sender_prefix) -> bytes:
    """The six sender bytes, from raw bytes or from 12 hex characters."""
    if isinstance(sender_prefix, str):
        try:
            sender_prefix = bytes.fromhex(sender_prefix)
        except ValueError as exc:
            raise ValueError(f"sender is not hex: {sender_prefix!r}") from exc
    sender_prefix = bytes(sender_prefix)
    if len(sender_prefix) != REQUEST_SENDER_BYTES:
        raise ValueError(
            f"sender must be {REQUEST_SENDER_BYTES} bytes, got {len(sender_prefix)}"
        )
    return sender_prefix


def encode_request(
    seq: int,
    bot: int,
    sender_prefix,
    ts: int,
    text: str,
) -> bytes:
    """Encode an app's Request datagram (spec 7B).

    ``sender_prefix`` is six bytes of the sender's public key (or the same
    twelve hex characters), ``ts`` the sender's own Unix seconds — repeated on
    a resend, which is what makes it a copy — and ``text`` the `>` request of
    section 8.2.  Nothing here is ever sent by the bot; it exists so the
    decoder has a matching encoder and the test vectors can be built.
    """
    sender = _sender_bytes(sender_prefix)
    body = text.encode("utf-8")
    if not body.startswith(b">"):
        raise ValueError(f"a request must start with '>', got {text!r}")
    if len(body) > MAX_REQUEST_TEXT:
        raise ValueError(
            f"request text is {len(body)} bytes, over the {MAX_REQUEST_TEXT}-byte limit"
        )
    out = (
        encode_header(seq, bot, TYPE_REQUEST)
        + sender
        + struct.pack("<I", _u32(ts, "ts"))
        + body
    )
    return _check_size(out, "request")


def decode_request(data: bytes) -> Request:
    """Decode a Request datagram, or raise ``ValueError``.

    Everything the spec calls a shape is checked here, because this is the one
    message the bot takes from strangers: the length, the type nibble, the
    sender's six bytes, and a text that is valid UTF-8, starts with `>` and is
    at most 40 bytes.  The flags nibble is reserved and ignored.
    """
    if isinstance(data, str):
        data = bytes.fromhex(data)
    data = bytes(data)
    if len(data) < MIN_REQUEST_SIZE:
        raise ValueError(
            f"truncated request: need {MIN_REQUEST_SIZE} bytes, have {len(data)}"
        )
    hdr = _decode_header(data)
    if hdr.type != TYPE_REQUEST:
        raise ValueError(f"not a request: type {hdr.type}")
    sender = data[HEADER_SIZE:HEADER_SIZE + REQUEST_SENDER_BYTES]
    off = HEADER_SIZE + REQUEST_SENDER_BYTES
    (ts,) = struct.unpack_from("<I", data, off)
    raw = data[off + 4:]
    if len(raw) > MAX_REQUEST_TEXT:
        raise ValueError(
            f"request text is {len(raw)} bytes, over the {MAX_REQUEST_TEXT}-byte limit"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"request text is not UTF-8: {exc}") from exc
    if not text.startswith(">"):
        raise ValueError(f"a request must start with '>', got {text[:8]!r}")
    return Request(seq=hdr.seq, bot=hdr.bot, sender_prefix=sender.hex(), ts=ts,
                   text=text, flags=hdr.flags)


def _decode_request(data: bytes, hdr: Header) -> dict:
    return decode_request(data).as_dict()


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

_DECODERS = {
    TYPE_WARNING: _decode_warning,
    TYPE_CANCEL: _decode_cancel,
    TYPE_DIGEST: _decode_digest,
    TYPE_OBS: _decode_obs,
    TYPE_FORECAST: _decode_forecast,
    TYPE_TEXT: _decode_text,
    TYPE_NOT_AVAILABLE: _decode_not_available,
    TYPE_COVERAGE: _decode_coverage,
    TYPE_REQUEST: _decode_request,
}


def decode(data: bytes) -> dict:
    """Decode one v5 message into a plain dict.

    Field names and units follow the spec.  An unknown message type yields
    just the header with ``name = "unknown"``; truncated input raises
    ``ValueError``.
    """
    if isinstance(data, str):
        data = bytes.fromhex(data)
    data = bytes(data)
    hdr = _decode_header(data)
    handler = _DECODERS.get(hdr.type)
    if handler is None:
        return hdr.as_dict()
    return handler(data, hdr)


# --------------------------------------------------------------------------
# UGC helper
# --------------------------------------------------------------------------


def areas_from_ugcs(
    ugcs: "list[str]", states: "list[str]"
) -> "list[tuple[int, bool, int, int]]":
    """Turn NWS UGC codes into area runs.

    ``"TXC453"`` is Travis county, ``"TXZ191"`` is forecast zone 191.  Codes
    whose state is not in ``states`` are skipped.  The result is sorted by
    ``(state index, county flag, number)`` and consecutive numbers of the same
    state and kind are merged into runs of at most 255.
    """
    index = {code: i for i, code in enumerate(states)}
    seen = set()
    for raw in ugcs:
        ugc = str(raw).strip().upper()
        if len(ugc) != 6 or ugc[2] not in ("C", "Z") or not ugc[3:].isdigit():
            continue
        state = index.get(ugc[:2])
        if state is None:
            continue
        seen.add((state, ugc[2] == "C", int(ugc[3:])))

    runs: "list[list]" = []
    for state, is_county, number in sorted(seen):
        if runs:
            last = runs[-1]
            if (
                last[0] == state
                and last[1] == is_county
                and last[2] + last[3] == number
                and last[3] < 255
            ):
                last[3] += 1
                continue
        runs.append([state, is_county, number, 1])
    return [(s, c, start, run) for s, c, start, run in runs]
