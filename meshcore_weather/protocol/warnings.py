"""Extract active warnings from the EMWIN store using pyIEM (canonical NWS parser).

pyIEM (Iowa Environmental Mesonet) is the reference Python implementation for
parsing NWS text products. It handles VTEC (NWSI 10-1703), UGC (NWSI 10-1702),
polygon extraction with correct winding, both Z (zone) and C (county FIPS)
codes, and non-VTEC products like SPS via UGC-line expiry.

We build the UGCProvider from our bundled `geodata/zones.json` at module init
so the parser runs fully offline with no database or network dependency.

Each warning dict carries an `expires_at` (tz-aware datetime). That's the
NWS-authoritative expiry — the source of truth. `expiry_minutes` is a
convenience value (minutes from *now*) recomputed at extraction time.

A hand-rolled fallback path is retained for the edge case where pyIEM cannot
parse a product.
"""

import json
import logging
import re
from datetime import timedelta, datetime, timezone
from pathlib import Path

from pyiem.nws.products import parser as pyiem_parser
from pyiem.nws.ugc import UGC, UGCProvider

from meshcore_weather.core.vtec_names import event_code
from meshcore_weather.parser.weather import (
    WeatherStore,
    _expand_zone_ranges,
    extract_warning_polygon,
)
from meshcore_weather.protocol.coverage import Coverage
from meshcore_weather.protocol.meshwx import (
    PRODUCT_TYPE_MAP,
    SEV_ADVISORY,
    SEV_WARNING,
    VTEC_SEVERITY_MAP,
    WARN_FIRE,
    WARN_FLASH_FLOOD,
    WARN_FLOOD,
    WARN_HIGH_WIND,
    WARN_MARINE,
    WARN_OTHER,
    WARN_SEVERE_TSTORM,
    WARN_SPECIAL,
    WARN_TORNADO,
    WARN_WINTER_STORM,
    pack_warning_polygon,
    pack_warning_zones,
)

logger = logging.getLogger(__name__)


# -- UGCProvider built once from bundled zones.json (fully offline) -----------

_GEODATA = Path(__file__).resolve().parent.parent / "geodata"
_UGC_PROVIDER: UGCProvider | None = None


def _build_ugc_provider() -> UGCProvider:
    """Build pyIEM's UGCProvider from bundled zones.json.

    pyIEM defaults to querying a PostGIS database for UGC metadata; the
    `legacy_dict` kwarg lets us pass a pre-built dict so it runs offline.
    """
    try:
        zones_data = json.loads((_GEODATA / "zones.json").read_text())
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not load zones.json for pyIEM UGCProvider: %s", exc)
        return UGCProvider(legacy_dict={})

    legacy: dict = {}
    for code, z in zones_data.items():
        if len(code) < 6:
            continue
        try:
            legacy[code] = UGC(
                state=z.get("s", ""),
                geoclass=code[2],
                number=int(code[3:]),
                name=z.get("n", ""),
                wfos=[z.get("w", "")] if z.get("w") else [],
            )
        except (ValueError, IndexError):
            continue
    logger.debug("pyIEM UGCProvider initialized with %d zones", len(legacy))
    return UGCProvider(legacy_dict=legacy)


def _get_ugc_provider() -> UGCProvider:
    global _UGC_PROVIDER
    if _UGC_PROVIDER is None:
        _UGC_PROVIDER = _build_ugc_provider()
    return _UGC_PROVIDER


# -- VTEC phenomenon → warning_type nibble ------------------------------------

# VTEC phenomenon codes (NWSI 10-1703) → our 4-bit wire-format warning type.
# Only common ones are distinguished; everything else becomes WARN_OTHER.
VTEC_PHENOMENON_TO_WARN_TYPE: dict[str, int] = {
    # Convective
    "TO": WARN_TORNADO,
    "SV": WARN_SEVERE_TSTORM,
    "SQ": WARN_SEVERE_TSTORM,  # Snow Squall
    "EW": WARN_SEVERE_TSTORM,  # Extreme Wind (tropical)
    # Flood
    "FF": WARN_FLASH_FLOOD,
    "FA": WARN_FLOOD,
    "FL": WARN_FLOOD,
    # Winter
    "WS": WARN_WINTER_STORM,
    "WW": WARN_WINTER_STORM,
    "BZ": WARN_WINTER_STORM,
    "IS": WARN_WINTER_STORM,
    "LE": WARN_WINTER_STORM,
    "ZR": WARN_WINTER_STORM,
    "ZF": WARN_WINTER_STORM,
    "SN": WARN_WINTER_STORM,
    # Wind
    "HW": WARN_HIGH_WIND,
    "WI": WARN_HIGH_WIND,
    # Marine
    "MA": WARN_MARINE,
    "SC": WARN_MARINE,
    "SE": WARN_MARINE,
    "SR": WARN_MARINE,
    "HF": WARN_MARINE,
    "GL": WARN_MARINE,
    "SW": WARN_MARINE,
    # Fire
    "FW": WARN_FIRE,
    # Everything else → WARN_OTHER
}


# -- Product types to inspect for active warnings ----------------------------

_WARNING_PRODUCT_TYPES = {
    # Warnings / watches / statements
    "TOR", "SVR", "SVS", "SPS", "FFW", "FLW", "FLS",
    "FFA",  # Flood Watch (FA.A / FF.A) — missing from the bot in 6 states on 2026-09-14
    "WSW", "NPW", "RFW", "FWW", "MWW", "MWS",
    "SMW", "SWO", "DSW", "EWW", "SQW", "CFW",
    # River products (VTEC-based)
    "RVA",  # River Watch/Warning — flood stage warnings with VTEC
    # SPC watch products (VTEC-based). NOTE: "AWW" is the Airport Weather
    # Warning (no VTEC, airport ops only) — not an SPC watch; do not add it.
    "WCN",  # Watch County Notification — counties in a watch
    "WOU",  # Watch Outline Update — zone/county list for an SPC watch
    # Hurricane products (VTEC-based)
    "HLS",  # Hurricane Local Statement
}

# SPS products don't carry VTEC; they get the WARN_SPECIAL type and
# advisory-level severity by default.
_NON_VTEC_TYPES = {"SPS"}


# -- Cancellation actions (VTEC significance) --------------------------------

_CANCEL_ACTIONS = {"CAN", "EXP", "UPG"}


def _vtec_key(vtec) -> tuple:
    """Identity of a VTEC event: what dedup and retirement key on."""
    return (vtec.phenomena, vtec.significance, vtec.office, vtec.etn)


# -- Headline shortening for LoRa display ------------------------------------


def _shorten_headline(h: str) -> str:
    """Compact a raw NWS headline for LoRa display.

    Replaces verbose templates with abbreviations. Does NOT truncate to a byte
    limit — that's done later at pack time with word-boundary awareness.
    """
    if not h:
        return "Active warning"
    if any(j in h.lower() for j in ("www.", "http", "graphic product")):
        return "Active warning"
    h = h.strip().rstrip(".")
    for prefix in ("A ", "AN "):
        if h.startswith(prefix):
            h = h[len(prefix):]
    h = (h.replace("REMAINS IN EFFECT UNTIL ", "til ")
          .replace("IN EFFECT UNTIL ", "til ")
          .replace("REMAINS IN EFFECT", "")
          .replace("IN EFFECT ", "")
          .replace("WILL IMPACT", "for")
          .replace("UNTIL ", "til ")
          .replace("THROUGH ", "thru ")
          .replace("THIS EVENING", "eve")
          .replace("THIS AFTERNOON", "aft")
          .replace("THIS MORNING", "morn"))
    return re.sub(r"  +", " ", h).strip()


def _extract_headline_from_body(text: str) -> str:
    """Extract a headline from the ...TEXT... block when pyIEM has no headline.

    Looks for lines wrapped in triple-dots like:
      ...RED FLAG WARNING REMAINS IN EFFECT FROM NOON TO 8 PM CDT...
    """
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("...") and len(s) > 10:
            # Strip the dots and return
            clean = s.strip(".")
            if clean and not any(x in clean.lower() for x in ("www.", "http")):
                return clean
    return ""


def _extract_warning_description(text: str) -> str:
    """Extract structured detail from a warning product.

    Pulls the bullet-point lines like:
      * Timing...12 PM CDT through 8 PM CDT Monday.
      * Wind...Southwest at 20 to 30 mph with gusts to 40 mph.
      * Humidity...As low as 12 percent.

    Also grabs WHAT/WHERE/WHEN/IMPACTS blocks from newer NWS format.
    Returns a compact multiline string, max ~500 chars.
    """
    lines: list[str] = []
    in_bullets = False

    for line in text.splitlines():
        s = line.strip()

        # Bullet-point lines: "* Wind...Southwest 20-30 mph"
        if s.startswith("* "):
            in_bullets = True
            # Clean up: "* Wind...text" → "Wind: text"
            content = s[2:]
            if "..." in content:
                label, _, detail = content.partition("...")
                content = f"{label.strip()}: {detail.strip()}"
            lines.append(content)
            continue

        # Continuation of a bullet (indented)
        if in_bullets and s and not s.startswith("*") and not s.startswith("PRECAUTIONARY"):
            if s.startswith("&&") or s.startswith("$$"):
                break
            # Append to previous line
            if lines:
                lines[-1] += " " + s
            continue

        # WHAT/WHERE/WHEN/IMPACTS blocks (newer NWS format)
        for tag in ("WHAT", "WHERE", "WHEN", "IMPACTS", "ADDITIONAL DETAILS"):
            if s.startswith(tag + "..."):
                content = s[len(tag) + 3:].strip()
                lines.append(f"{tag.title()}: {content}")
                in_bullets = True
                break

        if s.startswith("PRECAUTIONARY") or s.startswith("&&"):
            break

    result = "\n".join(lines)
    return result[:500] if result else ""


def _polygon_from_sbw(sbw) -> list[tuple[float, float]]:
    """Convert a pyIEM/Shapely polygon to (lat, lon) vertex list.

    Shapely stores coordinates as (lon, lat); our MeshWX protocol wants
    (lat, lon). Drops the closing vertex (first == last).
    """
    if sbw is None or sbw.geom_type != "Polygon":
        return []
    coords = list(sbw.exterior.coords)
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [(lat, lon) for lon, lat in coords]


# -- pyIEM-backed extraction --------------------------------------------------


_parsed_cache: dict[str, object] = {}
_PARSE_FAILED = object()


def _extract_warnings_pyiem(store: WeatherStore, now: datetime | None = None) -> list[dict]:
    """Every active warning, from a replay of all warning-class products.

    VTEC products go through `VtecTracker` (per-zone lifecycle, products in
    issue order); the entry for an active event is built from the newest
    segment that carried it, with the tracker's zone set and expiry.
    Non-VTEC products (SPS) are one entry per segment, newest product wins.
    """
    from meshcore_weather.protocol.vtec_events import VtecTracker

    provider = _get_ugc_provider()
    now = now or datetime.now(timezone.utc)

    products = list(store._products.values())      # snapshot: the warm-up runs off-loop
    live = {p.filename for p in products if p.product_type in _WARNING_PRODUCT_TYPES}
    for stale in [k for k in _parsed_cache if k not in live]:
        del _parsed_cache[stale]

    parsed_items: list[tuple] = []
    for prod in products:
        if prod.product_type not in _WARNING_PRODUCT_TYPES:
            continue
        parsed = _parsed_cache.get(prod.filename)
        if parsed is None:
            try:
                parsed = pyiem_parser(prod.raw_text, ugc_provider=provider)
            except Exception as exc:  # pyIEM can raise on malformed input
                logger.debug("pyIEM parse failed for %s: %s", prod.filename, exc)
                parsed = _PARSE_FAILED
            _parsed_cache[prod.filename] = parsed
        if parsed is _PARSE_FAILED:
            continue
        parsed_items.append((parsed, prod))

    results: list[dict] = []

    # -- VTEC events: replay the lifecycle -----------------------------------
    tracker = VtecTracker()
    tracker.replay(parsed_items, until=now)
    for ev in tracker.active(now):
        entry = _segment_to_entry(ev.last_seg, ev.last_parsed, ev.last_prod, now,
                                  vtec=ev.last_vtec, vtec_index=ev.last_vtec_index,
                                  tracker_state=ev)
        if entry is None:
            continue
        del entry["_dedup_key"]
        results.append(entry)

    # -- Non-VTEC (SPS): newest product per (office, type, valid) -------------
    seen: set[tuple] = set()
    for parsed, prod in sorted(parsed_items, key=lambda x: x[1].timestamp, reverse=True):
        if prod.product_type not in _NON_VTEC_TYPES:
            continue
        for seg in parsed.segments:
            if seg.vtec:
                continue
            entry = _segment_to_entry(seg, parsed, prod, now)
            if entry is None or entry["_dedup_key"] in seen:
                continue
            seen.add(entry["_dedup_key"])
            del entry["_dedup_key"]
            results.append(entry)
    return results


def _segment_to_entry(
    seg, parsed, prod, now: datetime, vtec=None, vtec_index: int = 0, tracker_state=None,
) -> dict | None:
    """Convert one VTEC event of a pyIEM product segment to a warning dict.

    `vtec` selects which of the segment's VTEC lines this entry describes
    (segments can carry more than one); None means "the first one", or the
    non-VTEC path for products like SPS. `vtec_index` picks the matching
    headline when the segment has one headline per event.

    Returns None if the event should be skipped (cancelled, expired, no
    extractable data, etc.).
    """
    ugcs = [str(u) for u in seg.ugcs]
    if tracker_state is not None:
        # The event's current zones and times come from the lifecycle replay,
        # not from this one segment.
        ugcs = sorted(tracker_state.active_zones(now))
    if not ugcs:
        return None

    # -- VTEC path (most warnings: TOR, SVR, SVS, FFW, WSW, etc.) --
    expires_estimated = False
    if seg.vtec:
        if vtec is None:
            vtec = seg.vtec[0]
        if tracker_state is not None:
            expires_at = tracker_state.expires_at(now)
            if expires_at is None:
                # Until further notice: give the client something finite.
                expires_at = now + timedelta(hours=12)
                expires_estimated = True
            onset_at = tracker_state.onset_at(now)
        else:
            if vtec.action in _CANCEL_ACTIONS:
                return None
            expires_at = vtec.endts
            if expires_at is None:
                # Very rare: VTEC without end time. Fall back to UGC expiry.
                expires_at = getattr(seg, "ugcexpire", None)
            if expires_at is None:
                return None
            if expires_at <= now:
                return None  # already expired
            onset_at = vtec.begints  # when the warning becomes active
        wtype = VTEC_PHENOMENON_TO_WARN_TYPE.get(vtec.phenomena, WARN_OTHER)
        severity = VTEC_SEVERITY_MAP.get(vtec.significance, SEV_WARNING)
        dedup_key = (vtec.phenomena, vtec.significance, vtec.office, vtec.etn)
        vtec_meta = {
            "vtec_action": vtec.action,
            "vtec_phenomenon": vtec.phenomena,
            "vtec_significance": vtec.significance,
            "vtec_office": vtec.office,
            "vtec_etn": vtec.etn,
        }
        event = event_code(vtec.phenomena, vtec.significance)
    # -- Non-VTEC path (SPS, rare others) --
    elif prod.product_type in _NON_VTEC_TYPES:
        expires_at = getattr(seg, "ugcexpire", None)
        if expires_at is None or expires_at <= now:
            return None

        onset_at = parsed.valid  # effective immediately for non-VTEC
        wtype = WARN_SPECIAL
        severity = SEV_ADVISORY
        # Dedup by (office, type, valid time) — SPS doesn't have ETNs.
        dedup_key = (prod.office, prod.product_type, parsed.valid)
        vtec_meta = {
            "vtec_action": None,
            "vtec_phenomenon": None,
            "vtec_significance": None,
            "vtec_office": prod.office,
            "vtec_etn": None,
        }
        event = event_code(None, None)   # SPS
    else:
        return None

    # Polygon and zone splitting
    vertices = _polygon_from_sbw(seg.sbw)
    zones = [u for u in ugcs if len(u) == 6 and u[2] == "Z"]

    # Need something renderable
    if len(vertices) < 3 and not zones:
        return None

    # Headline: prefer pyIEM's canonical ...HEADLINE... block.
    # Fall back to extracting ...TEXT... from the raw product if empty.
    headlines = list(seg.headlines) if seg.headlines else []
    if len(headlines) > vtec_index:
        headline_raw = headlines[vtec_index]
    else:
        headline_raw = headlines[0] if headlines else ""
    if not headline_raw:
        headline_raw = _extract_headline_from_body(prod.raw_text)
    headline = _shorten_headline(headline_raw)

    # Description: structured detail lines (Wind, Humidity, Timing, Impacts)
    description = _extract_warning_description(prod.raw_text)

    expiry_minutes = max(0, int((expires_at - now).total_seconds() / 60))

    onset_unix_min = int(onset_at.timestamp() / 60) if onset_at else 0

    # When the product said it was issued, from its own header (pyIEM's
    # `valid`), never when the bot received the file: the phone shows this as
    # "issued 8:24 PM" and a radio that was out of range for three hours must
    # not turn that into the time it finally heard the warning. The lifecycle
    # replay keeps the NEW segment's time for the identity, so a continuation
    # (SVS) does not restamp a warning as newly issued.
    issued_at = getattr(tracker_state, "issued_at", None) if tracker_state is not None else None
    if issued_at is None:
        issued_at = getattr(parsed, "valid", None)

    entry = {
        # Storm-based warning tags (pyIEM reads them from the product text):
        # the actionable numbers of a severe or flash flood warning.
        **_segment_tags(seg),
        # Wire-format fields (consumed by warnings_to_binary)
        "warning_type": wtype,
        "severity": severity,
        "event_code": event,
        "onset_at": onset_at,          # when the warning becomes active (None = immediate)
        "onset_unix_min": onset_unix_min,
        "issued_at": issued_at,        # the product's own issuance (spec 3, revision 5)
        "expires_at": expires_at,      # canonical absolute expiry (NWS-authoritative)
        "expires_estimated": expires_estimated,  # invented (until further notice): moves with the clock
        "expiry_minutes": expiry_minutes,  # convenience — minutes from "now"
        "vertices": vertices,
        "headline": headline,
        "description": description,
        "zones": sorted(zones),
        # Extended metadata
        "ugcs": sorted(ugcs),
        "product_type": prod.product_type,
        "filename": prod.filename,
        # How the bot got the product this warning was read out of, for the
        # wire's source field (spec 2.2.1, revision 7).
        "source": getattr(prod, "source", ""),
        "_dedup_key": dedup_key,
        **vtec_meta,
    }
    return entry


# -- Hand-rolled fallback (legacy path) ---------------------------------------


def _parse_vtec_end_datetime(end_str: str) -> datetime | None:
    """Parse a VTEC end string 'YYMMDDTHHMMZ' (12 chars) into a UTC datetime.

    The pre-existing `_vtec_end_to_minutes` was fundamentally broken: it was
    treating the first 6 chars as DDHHMM, when the actual captured group is
    the full 12-char `YYMMDDTHHMMZ`. That's what caused the "363h" display bug.
    """
    try:
        # Format: YYMMDDTHHMMZ  e.g. "260411T0100Z"
        if len(end_str) < 12 or end_str[6] != "T":
            return None
        year = 2000 + int(end_str[0:2])
        month = int(end_str[2:4])
        day = int(end_str[4:6])
        hour = int(end_str[7:9])
        minute = int(end_str[9:11])
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _extract_warnings_fallback(store: WeatherStore) -> list[dict]:
    """Legacy hand-rolled extraction. Only used if pyIEM parsing crashes globally."""
    from meshcore_weather.parser.weather import parse_vtec  # local import to avoid cycles

    now = datetime.now(timezone.utc)
    results: list[dict] = []
    seen: set[str] = set()

    for prod in sorted(store._products.values(), key=lambda p: p.timestamp, reverse=True):
        if prod.product_type not in _WARNING_PRODUCT_TYPES:
            continue
        key = f"{prod.product_type}_{prod.orig}"
        if key in seen:
            continue
        seen.add(key)
        if store._is_cancelled(prod.raw_text):
            continue

        vertices = extract_warning_polygon(prod.raw_text)
        zones = _expand_zone_ranges(prod.raw_text)
        if len(vertices) < 3 and not zones:
            continue

        wtype = PRODUCT_TYPE_MAP.get(prod.product_type, WARN_OTHER)
        vtec = parse_vtec(prod.raw_text)
        expires_at: datetime | None = None
        if vtec:
            severity = VTEC_SEVERITY_MAP.get(vtec["significance"], SEV_WARNING)
            expires_at = _parse_vtec_end_datetime(vtec["end"])
        else:
            severity = SEV_WARNING

        expires_estimated = expires_at is None
        if expires_at is None:
            # Last-resort default: 2 hours from now. Only applied in the
            # fallback path for products with no VTEC.
            expires_at = now.replace(second=0, microsecond=0)
            expires_at = expires_at.replace(hour=(expires_at.hour + 2) % 24)

        if expires_at <= now:
            continue  # expired

        expiry_minutes = max(0, int((expires_at - now).total_seconds() / 60))

        # Fallback path has no VTEC onset; assume effective immediately
        onset_at = now

        results.append({
            "warning_type": wtype,
            "severity": severity,
            "onset_at": onset_at,
            "onset_unix_min": int(onset_at.timestamp() / 60),
            # No parsed product header on this path, and the filename's time is
            # when the bot received the file, not when NWS issued it: say
            # nothing rather than something wrong (spec 3, revision 5).
            "issued_at": None,
            "expires_at": expires_at,
            "expires_estimated": expires_estimated,
            "expiry_minutes": expiry_minutes,
            "vertices": vertices,
            "headline": _shorten_headline(store._short_headline(prod.raw_text)),
            "description": _extract_warning_description(prod.raw_text),
            "zones": sorted(zones),
            "ugcs": sorted(zones),
            "product_type": prod.product_type,
            "filename": prod.filename,
            "source": getattr(prod, "source", ""),
            "vtec_action": (vtec or {}).get("action"),
            "vtec_phenomenon": (vtec or {}).get("phenomenon"),
            "vtec_significance": (vtec or {}).get("significance"),
            "vtec_office": None,
            "vtec_etn": None,
        })
    return results


# -- Public API ---------------------------------------------------------------


def _segment_tags(seg) -> dict:
    """hail (quarter inches), wind (mph), tornado / flood tags as v5 codes."""
    out = {"hail_qin": 0, "wind_mph": 0, "tornado_tag": 0, "flood_source": 0, "flood_damage": 0}
    try:
        hail = getattr(seg, "hailtag", None)
        if hail:
            out["hail_qin"] = max(0, min(255, int(round(float(hail) * 4))))
        wind = getattr(seg, "windtag", None)
        if wind:
            mph = float(wind)
            if str(getattr(seg, "windtagunits", "") or "").upper().startswith("KT"):
                mph *= 1.15078
            out["wind_mph"] = max(0, min(255, int(round(mph))))
        tor = str(getattr(seg, "tornadotag", "") or "").upper()
        if "OBSERVED" in tor or "EMERGENCY" in tor:
            out["tornado_tag"] = 3
        elif "RADAR" in tor:
            out["tornado_tag"] = 2
        elif "POSSIBLE" in tor:
            out["tornado_tag"] = 1
        tags = getattr(seg, "flood_tags", None) or {}
        for key, val in dict(tags).items():
            k, v = str(key).upper(), str(val).upper()
            if "DAMAGE" in k:
                out["flood_damage"] = 2 if "CATASTROPHIC" in v else 1 if "CONSIDERABLE" in v else 0
            elif "FLOOD" in k and "DAMAGE" not in k:
                out["flood_source"] = 3 if "OBSERVED" in v else 2 if "GAUGE" in v else 1 if "RADAR" in v else 0
        dmg = str(getattr(seg, "damagetag", "") or "").upper()
        if dmg and not out["flood_damage"] and ("CONSIDERABLE" in dmg or "CATASTROPHIC" in dmg or "DESTRUCTIVE" in dmg):
            out["flood_damage"] = 2 if ("CATASTROPHIC" in dmg or "DESTRUCTIVE" in dmg) else 1
    except Exception:          # tags are a bonus, never a reason to drop a warning
        pass
    return out


def extract_active_warnings(
    store: WeatherStore,
    coverage: Coverage | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Extract all active warnings from the store.

    Uses pyIEM as the primary parser. Falls back to the hand-rolled extractor
    only if pyIEM crashes globally.

    Each returned dict includes `expires_at` (tz-aware datetime) as the
    NWS-authoritative expiry source of truth, and `expiry_minutes` as a
    convenience recomputed at extraction time.
    """
    try:
        raw = _extract_warnings_pyiem(store, now)
    except Exception as exc:
        logger.warning("pyIEM warning extraction crashed; falling back: %s", exc)
        raw = _extract_warnings_fallback(store)

    filter_active = coverage is not None and not coverage.is_empty()
    results: list[dict] = []
    for w in raw:
        if filter_active:
            ugcs = w.get("ugcs") or w.get("zones", [])
            if ugcs and coverage.covers_any(ugcs):
                pass
            elif w.get("vertices") and coverage.covers_polygon(w["vertices"]):
                pass
            else:
                continue
        results.append(w)

    return results


def warnings_to_binary(warnings: list[dict], prefer_zones: bool = True) -> list[bytes]:
    """Pack a list of warning dicts into MeshWX binary messages.

    Each warning produces:
      1. A 0x20 (polygon) or 0x21 (zones) message with headline
      2. Optionally, 0x40 text chunk(s) with the detailed description
         (wind speeds, humidity, impacts, etc.)
    """
    msgs: list[bytes] = []
    for w in warnings:
        try:
            expires_at: datetime | None = w.get("expires_at")
            if expires_at is None:
                logger.debug("skip warning without expires_at: %s", w.get("headline", "?")[:40])
                continue
            expires_unix_min = int(expires_at.timestamp() / 60)

            onset_unix_min = w.get("onset_unix_min", 0)
            zones = w.get("zones", [])
            if prefer_zones and zones:
                msg = pack_warning_zones(
                    event=w.get("event_code", 0),
                    expires_unix_min=expires_unix_min,
                    zones=zones,
                    headline=w["headline"],
                    onset_unix_min=onset_unix_min,
                )
            elif w.get("vertices"):
                msg = pack_warning_polygon(
                    event=w.get("event_code", 0),
                    expires_unix_min=expires_unix_min,
                    vertices=w["vertices"],
                    headline=w["headline"],
                    onset_unix_min=onset_unix_min,
                )
            else:
                continue
            msgs.append(msg)
        except Exception:
            logger.debug("Failed to pack warning: %s", w.get("headline", "?")[:40])
    return msgs
