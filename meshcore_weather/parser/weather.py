"""Parse EMWIN weather products following the Vitality GOES approach.

Products are matched by their EMWIN 8-character identifier:
    chars 0-2: product type (ZFP, RWR, SVR, etc.)
    chars 3-5: NWS office code (EWX, MFL, BUF, etc.)
    chars 6-7: state abbreviation (TX, FL, NY, etc.)

For a location like "Austin TX", we resolve to:
    orig = "EWXTX" (office EWX + state TX)
    wxZone = "TXZ192"
    city = "AUSTIN"
    lat/lon for geofencing

Then find EMWIN products: ZFP+EWXTX, RWR+EWXTX, PFM+EWXTX, etc.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from meshcore_weather.geodata import resolver

logger = logging.getLogger(__name__)

def _expand_zone_ranges(text: str) -> set[str]:
    """Expand NWS zone range notation like 'TXZ021>044' into individual zones.

    Searches the first 25 lines of text for zone codes and ranges.
    """
    zones: set[str] = set()
    for line in text.splitlines()[:25]:
        s = line.strip()
        # Match explicit zones and ranges: "TXZ021>044" or "TXZ192"
        for m in re.finditer(r"([A-Z]{2}Z)(\d{3})(?:>(\d{3}))?", s):
            prefix = m.group(1)
            start = int(m.group(2))
            end = int(m.group(3)) if m.group(3) else start
            for n in range(start, end + 1):
                zones.add(f"{prefix}{n:03d}")
    return zones


def extract_warning_polygon(text: str) -> list[tuple[float, float]]:
    """Extract LAT...LON polygon vertices from an NWS warning product."""
    coords: list[tuple[float, float]] = []
    in_polygon = False
    for line in text.splitlines():
        stripped = line.strip()
        if "LAT...LON" in stripped:
            in_polygon = True
            nums = re.findall(r"\d{4,5}", stripped.split("LAT...LON")[-1])
            for j in range(0, len(nums) - 1, 2):
                try:
                    coords.append((int(nums[j]) / 100, -(int(nums[j + 1]) / 100)))
                except (ValueError, IndexError):
                    pass
            continue
        if in_polygon:
            if not stripped or stripped.startswith("TIME") or stripped.startswith("$$"):
                break
            nums = re.findall(r"\d{4,5}", stripped)
            for j in range(0, len(nums) - 1, 2):
                try:
                    coords.append((int(nums[j]) / 100, -(int(nums[j + 1]) / 100)))
                except (ValueError, IndexError):
                    pass
    return coords


def parse_vtec(text: str) -> dict | None:
    """Parse VTEC line from an NWS warning product.

    Returns dict with action, phenomenon, significance, and end time,
    or None if no VTEC line found.
    """
    for line in text.splitlines()[:30]:
        s = line.strip()
        m = re.match(
            r"/O\.(\w+)\.\w{4}\.(\w{2})\.(\w)\.\d{4}\.\d{6}T\d{4}Z-(\d{6}T\d{4}Z)/",
            s,
        )
        if m:
            return {
                "action": m.group(1),
                "phenomenon": m.group(2),
                "significance": m.group(3),
                "end": m.group(4),
            }
    return None


# EMWIN 8-char identifier regex from filename
# e.g. A_FPUS54KOUN072238_C_KWIN_20260407222840_123456-2-ZFPOUNOK.TXT
#                                                          ^^^^^^^^
EMWIN_ID_RE = re.compile(r"-([A-Z0-9]{8})\.TXT$", re.IGNORECASE)

# Timestamp from EMWIN filename (5th underscore-delimited field)
EMWIN_TS_RE = re.compile(r"_(\d{14})_")


@dataclass
class EMWINProduct:
    """A parsed EMWIN product with metadata from its filename."""
    filename: str
    emwin_id: str       # 8-char identifier e.g. "ZFPEWXTX"
    product_type: str   # first 3 chars e.g. "ZFP"
    orig: str           # last 5 chars e.g. "EWXTX"
    office: str         # chars 3-5 e.g. "EWX"
    state: str          # chars 6-7 e.g. "TX"
    timestamp: datetime
    raw_text: str


class WeatherStore:
    """EMWIN product store using Vitality GOES-style filename matching."""

    def __init__(self):
        self._products: dict[str, EMWINProduct] = {}  # keyed by filename

    def ingest(self, raw_products: list[dict]) -> int:
        count = known = 0
        for raw in raw_products:
            # The sources hand back their whole cache every poll, so this
            # used to re-parse ~14k products a minute on the event loop and
            # stall everything sharing the thread, the handler that matches
            # a repeater's echo included. A filename names one product for
            # ever, so one already in the store never needs parsing again.
            name = raw.get("filename")
            if name and name in self._products:
                known += 1
                continue
            prod = self._parse(raw)
            if prod:
                self._products[prod.filename] = prod
                count += 1
        # Expire old products from the store (warnings live longer: see
        # emwin/retention.py)
        from meshcore_weather.emwin.retention import is_expired
        now = datetime.now(timezone.utc)
        before = len(self._products)
        self._products = {
            k: v for k, v in self._products.items()
            if not is_expired(v.product_type, v.timestamp, now)
        }
        expired = before - len(self._products)
        logger.info("Ingested %d/%d products%s%s",
                     count, len(raw_products),
                     f", {known} already held" if known else "",
                     f" (expired {expired})" if expired else "")
        return count

    def _parse(self, raw: dict) -> EMWINProduct | None:
        filename = raw.get("filename", "")
        raw_text = raw.get("raw_text", "")

        # Extract 8-char EMWIN identifier from filename
        m = EMWIN_ID_RE.search(filename)
        if not m:
            return None
        emwin_id = m.group(1).upper()
        if len(emwin_id) != 8:
            return None

        product_type = emwin_id[:3]
        orig = emwin_id[3:]
        office = orig[:3]
        state = orig[3:]

        # Extract timestamp from filename
        ts = datetime.now(timezone.utc)
        m_ts = EMWIN_TS_RE.search(filename)
        if m_ts:
            try:
                ts = datetime.strptime(m_ts.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        return EMWINProduct(
            filename=filename,
            emwin_id=emwin_id,
            product_type=product_type,
            orig=orig,
            office=office,
            state=state,
            timestamp=ts,
            raw_text=raw_text,
        )

    # -- Finding products by type + orig --

    def _find(self, product_type: str, orig: str) -> EMWINProduct | None:
        """Find the newest product matching type+orig (e.g. ZFP + EWXTX)."""
        target = (product_type + orig).upper()
        best = None
        for prod in self._products.values():
            if prod.emwin_id == target:
                if best is None or prod.timestamp > best.timestamp:
                    best = prod
        return best

    def _find_any_orig(self, product_type: str, origs: list[str]) -> EMWINProduct | None:
        """Try multiple orig values, return newest match."""
        for orig in origs:
            p = self._find(product_type, orig)
            if p:
                return p
        return None


    # WFO codes that differ between zone database and AWIPS product IDs
    _WFO_AWIPS_ALIASES = {
        "SJU": "JSJ",  # San Juan PR
        "GUM": "GUA",  # Guam
    }

    def _build_origs(self, loc: dict) -> list[str]:
        """Build possible orig values (office+state) from resolver result."""
        origs = []
        state = ""
        name = loc.get("name", "")
        if ", " in name:
            state = name.split(", ")[-1].strip()

        wfos = list(loc.get("wfos", []))
        # Add AWIPS aliases (SJU->JSJ, etc.)
        for wfo in list(wfos):
            alias = self._WFO_AWIPS_ALIASES.get(wfo)
            if alias and alias not in wfos:
                wfos.append(alias)

        for wfo in wfos:
            if state and len(state) == 2:
                orig = f"{wfo}{state}"
                if orig not in origs:
                    origs.append(orig)
            for zone in loc.get("zones", []):
                zone_state = zone[:2]
                orig = f"{wfo}{zone_state}"
                if orig not in origs:
                    origs.append(orig)
        return [o.upper() for o in origs]

    # -- Public API --


    def _parse_rwr_city(self, text: str, city: str) -> str:
        """Extract city conditions from RWR fixed-width table."""
        raw = self._parse_rwr_city_raw(text, city)
        if not raw:
            return ""
        return self._format_rwr_conditions(raw)

    def _parse_rwr_city_raw(self, text: str, city: str) -> str:
        """Like _parse_rwr_city but returns the raw column data, not formatted."""
        if not city:
            return ""
        in_table = False
        for line in text.splitlines():
            stripped = line.strip()
            if "SKY/WX" in stripped and "TMP" in stripped:
                in_table = True
                continue
            if not in_table or not stripped:
                continue
            if stripped.startswith("$$"):
                in_table = False
                continue
            if not stripped.upper().startswith(city):
                continue
            return stripped[len(city):].strip()
        return ""

    def _format_rwr_conditions(self, raw: str) -> str:
        """Format raw RWR condition columns into readable text."""
        parts = raw.split()
        if len(parts) < 4:
            return raw[:100]

        # Merge sky/wx words until we hit a number (temperature)
        sky_parts = []
        temp_idx = 0
        for i, p in enumerate(parts):
            if p.lstrip("-").isdigit():
                temp_idx = i
                break
            sky_parts.append(p)

        sky_map = {
            "SUNNY": "Sunny", "MOSUNNY": "Mostly Sunny", "PTSUNNY": "Partly Sunny",
            "CLEAR": "Clear", "MOCLDY": "Mostly Cloudy", "PTCLDY": "Partly Cloudy",
            "CLOUDY": "Cloudy", "FAIR": "Fair", "HAZE": "Haze", "FOG": "Fog",
        }
        sky_raw = " ".join(sky_parts).upper()
        sky = sky_map.get(sky_raw, " ".join(sky_parts).title())

        # Handle multi-word conditions like "LGT RAIN"
        for phrase, label in [("LGT RAIN", "Lt Rain"), ("HVY RAIN", "Hvy Rain"),
                               ("RAIN", "Rain"), ("SNOW", "Snow"), ("TSTORM", "T-Storm")]:
            if phrase in sky_raw:
                sky = label
                break

        try:
            temp_f = parts[temp_idx]
            pieces = [sky, f"{temp_f}F"]
            # Wind is usually after temp, dewpoint, humidity
            for p in parts[temp_idx + 1:]:
                if re.match(r"^[NESWVRB]+\d+", p) or p == "CALM":
                    pieces.append(f"Wind {p}")
                    break
            return " | ".join(pieces)
        except (IndexError, ValueError):
            return raw[:100]


    def _find_metar_raw(self, station: str) -> tuple[str, datetime] | None:
        """Find the newest raw METAR string for a specific station."""
        if not station:
            return None
        best: tuple[str, datetime] | None = None
        for prod in sorted(self._products.values(), key=lambda p: p.timestamp, reverse=True):
            if prod.product_type != "SAH" and "METAR" not in prod.raw_text[:200]:
                continue
            for line in prod.raw_text.splitlines():
                stripped = line.strip()
                if stripped.startswith(station) and re.match(r"^[A-Z]{4}\s+\d{6}Z", stripped):
                    if best is None or prod.timestamp > best[1]:
                        best = (stripped, prod.timestamp)
                    break
        return best


    def _parse_zfp_zone(self, text: str, zone: str) -> str:
        """Extract forecast for a specific zone from a ZFP product."""
        lines = text.splitlines()
        in_zone = False
        forecast_parts = []

        for line in lines:
            stripped = line.strip()
            if not in_zone:
                # Look for zone code at start of line (e.g. "TXZ192-TXZ193-")
                if zone in stripped and re.match(r"^[A-Z]{2}Z\d{3}", stripped):
                    in_zone = True
                continue

            # End of zone section
            if stripped.startswith("$$") or stripped.startswith("&&"):
                break

            # Forecast periods start with "."
            if stripped.startswith("."):
                period = stripped.lstrip(".").strip()
                forecast_parts.append(period)
                continue

            # Continuation of current period
            if forecast_parts and stripped:
                forecast_parts[-1] += " " + stripped

        if not forecast_parts:
            return ""

        # Return first period (e.g. "TODAY...Sunny with a high of 72.")
        return forecast_parts[0]


    @staticmethod
    def _is_cancelled(text: str) -> bool:
        """Check if a warning product has been cancelled or expired via VTEC."""
        for line in text.splitlines()[:30]:
            s = line.strip()
            if s.startswith("/O."):
                # VTEC action: CAN=cancelled, EXP=expired, UPG=upgraded
                m = re.match(r"/O\.(\w+)\.", s)
                if m and m.group(1) in ("CAN", "EXP", "UPG"):
                    return True
            upper = s.upper()
            if "IS CANCELLED" in upper or "WILL EXPIRE AT" in upper:
                return True
            if "WILL EXPIRE" in upper or "HAS WEAKENED" in upper:
                return True
        return False


