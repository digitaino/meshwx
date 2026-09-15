#!/usr/bin/env python3
"""Build the client preload bundle for MeshWX-aware apps.

Outputs a directory of compact JSON + GeoJSON files that iOS/web clients
ship with their app. With this preloaded, broadcasts only need to transmit
small IDs (zone codes, place indices, PFM point indices, etc.) instead of
full location names — the client looks up the full details locally.

Usage:
    python scripts/build_client_data.py [output_dir]

Defaults to `meshcore_weather/client_data/` (inside the package) so the
bundle ships as package-data and is available at runtime regardless of
deployment location. The bot's broadcaster reads from this directory via
`Path(__file__).resolve().parent.parent / "client_data"`.
"""

import io
import json
import math
import re
import sys
import zipfile
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEODATA = ROOT / "meshcore_weather" / "geodata"
EMWIN_CACHE = ROOT / "data" / "emwin_cache" / "products.jsonl"
SHAPEFILE_CACHE = ROOT / ".cache" / "nws_shapefiles"

# NWS public forecast zones shapefile (public domain US federal data).
# Filename encodes the effective date — NWS releases a new version a few
# times a year. Find current versions at https://www.weather.gov/gis/publiczones
# When NWS publishes an update, bump both the URL and the expected MD5.
NWS_ZONES_SHAPEFILE_URL = (
    "https://www.weather.gov/source/gis/Shapefiles/WSOM/z_16ap26.zip"
)
NWS_ZONES_SHAPEFILE_MD5 = "b883244e367c51f493d93ff4feaad9f0"

# US Census Bureau 2020 Gazetteer, ZIP Code Tabulation Areas (public domain).
# Not fetched by the builder: download it and unzip it into .cache/census/
# (the .zip as downloaded also works). Without it zips.json is kept as is.
ZCTA_GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/"
    "2020_Gaz_zcta_national.zip"
)
ZCTA_GAZETTEER = ROOT / ".cache" / "census" / "2020_Gaz_zcta_national.txt"
ZCTA_SOURCE = "US Census Bureau 2020 ZCTA Gazetteer"

# Fallback EMWIN bundle URL if local cache is empty (dev-box builds)
EMWIN_BUNDLE_URL = (
    "https://tgftp.nws.noaa.gov/SL.us008001/CU.EMWIN/DF.xt/DC.gsatR/OPS/txthrs01.zip"
)

# National centres that put their own office in VTEC (KNHC tropical, KWNS
# = SPC watch outlines). No zones, so they are not in zones.json; they
# follow the WFOs in wfos.json and index.json at fixed indices 125, 126.
NATIONAL_CENTRES = OrderedDict([
    ("NHC", {"states": [], "lat": 25.7543, "lon": -80.3838, "zone_count": 0,
             "name": "National Hurricane Center"}),
    ("WNS", {"states": [], "lat": 35.1812, "lon": -97.4401, "zone_count": 0,
             "name": "Storm Prediction Center"}),
])


def build_zones(out_dir: Path) -> None:
    """Compact NWS zones file indexed by zone code.

    Input:  {zone_code: {n, w, s, la, lo, c}}
    Output: {zone_code: {name, wfo, state, lat, lon}}  (drop counties list)
    """
    src = json.loads((GEODATA / "zones.json").read_text())
    out = OrderedDict()
    for code, z in sorted(src.items()):
        out[code] = {
            "name": z.get("n", ""),
            "wfo": z.get("w", ""),
            "state": z.get("s", ""),
            "lat": z.get("la", 0),
            "lon": z.get("lo", 0),
        }
    path = out_dir / "zones.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    size = path.stat().st_size / 1024
    print(f"  zones.json:    {len(out):>6} entries, {size:.0f} KB")


def build_places(out_dir: Path) -> None:
    """Place index → name, state, lat, lon.

    Input:  [[NAME, STATE, lat, lon], ...]
    Output: {"places": [["Austin", "TX", 30.27, -97.74], ...]}
    The array INDEX is the place_id used in MeshWX binary messages.
    """
    src = json.loads((GEODATA / "places.json").read_text())
    out = {"places": src}  # already in the right shape
    path = out_dir / "places.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    size = path.stat().st_size / 1024 / 1024
    print(f"  places.json:   {len(src):>6} entries, {size:.1f} MB")


def build_stations(out_dir: Path) -> None:
    """METAR stations indexed by ICAO."""
    src = json.loads((GEODATA / "stations.json").read_text())
    out = OrderedDict()
    for icao, st in sorted(src.items()):
        out[icao] = {
            "name": st.get("n", ""),
            "state": st.get("s", ""),
            "lat": st.get("la", 0),
            "lon": st.get("lo", 0),
        }
    path = out_dir / "stations.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    size = path.stat().st_size / 1024
    print(f"  stations.json: {len(out):>6} entries, {size:.0f} KB")


def build_wfos(out_dir: Path) -> None:
    """Aggregate unique WFO codes from zones.json with their served state/region."""
    zones = json.loads((GEODATA / "zones.json").read_text())
    wfos: dict[str, dict] = {}
    for z in zones.values():
        w = z.get("w")
        if not w:
            continue
        if w not in wfos:
            wfos[w] = {"states": set(), "lat_sum": 0, "lon_sum": 0, "count": 0}
        wfos[w]["states"].add(z.get("s", ""))
        wfos[w]["lat_sum"] += z.get("la", 0)
        wfos[w]["lon_sum"] += z.get("lo", 0)
        wfos[w]["count"] += 1

    out = OrderedDict()
    for code in sorted(c for c in wfos if c not in NATIONAL_CENTRES):
        data = wfos[code]
        out[code] = {
            "states": sorted(data["states"]),
            "lat": round(data["lat_sum"] / data["count"], 4),
            "lon": round(data["lon_sum"] / data["count"], 4),
            "zone_count": data["count"],
        }
    # Appended after the sorted WFOs, in this fixed order: the file order is
    # the wire's office byte (index.json), so they are never sorted in.
    out.update(NATIONAL_CENTRES)
    path = out_dir / "wfos.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    size = path.stat().st_size / 1024
    print(f"  wfos.json:     {len(out):>6} entries, {size:.0f} KB")


def build_state_index(out_dir: Path) -> None:
    """Copy the state index file used for compact zone encoding."""
    src = GEODATA / "state_index.json"
    dst = out_dir / "state_index.json"
    dst.write_text(src.read_text())
    size = dst.stat().st_size / 1024
    print(f"  state_index:   {size:.1f} KB")


def build_dictionary(out_dir: Path) -> None:
    """Dictionary of top phrases for text compression in 0x40 chunks."""
    # Top-128 phrases ordered by estimated frequency in NWS products.
    # Index = code (escape byte 0xFE followed by 1 byte code).
    phrases = [
        # VTEC / status (0-15)
        "IN EFFECT UNTIL", "REMAINS IN EFFECT", "CANCELLED", "EXPIRES AT",
        "CONTINUES", "UPGRADED TO", "DOWNGRADED TO", "EXTENDED UNTIL",
        "WILL EXPIRE", "HAS EXPIRED", "NEW", "ROUTINE", "URGENT",
        "IMMEDIATE", "EXPECTED", "POSSIBLE",

        # Warning types (16-39)
        "TORNADO WARNING", "SEVERE THUNDERSTORM WARNING",
        "FLASH FLOOD WARNING", "FLOOD WARNING", "FLOOD ADVISORY",
        "FLOOD STATEMENT", "WIND ADVISORY", "HIGH WIND WARNING",
        "HIGH WIND WATCH", "WINTER STORM WARNING", "WINTER WEATHER ADVISORY",
        "BLIZZARD WARNING", "ICE STORM WARNING", "FREEZE WARNING",
        "FROST ADVISORY", "HEAT ADVISORY", "EXCESSIVE HEAT WARNING",
        "RED FLAG WARNING", "FIRE WEATHER WATCH", "DENSE FOG ADVISORY",
        "COASTAL FLOOD ADVISORY", "RIP CURRENT STATEMENT",
        "SEVERE WEATHER STATEMENT", "SPECIAL WEATHER STATEMENT",

        # Impacts (40-63)
        "TAKE SHELTER NOW", "MOVE INDOORS", "SEEK SHELTER", "STAY INDOORS",
        "AVOID TRAVEL", "HAZARDOUS DRIVING", "LIFE-THREATENING",
        "DAMAGING WINDS", "LARGE HAIL", "FLASH FLOODING",
        "HEAVY RAINFALL", "STRONG WINDS", "GUSTY WINDS",
        "DAMAGING WIND GUSTS", "EXPECT DAMAGE", "PEOPLE OUTSIDE",
        "MOBILE HOMES", "TREES DOWN", "POWER OUTAGES",
        "HAIL SIZE", "GOLF BALL", "TENNIS BALL", "BASEBALL SIZE",
        "PENNY SIZE",

        # Time phrases (64-87)
        "UNTIL", "AT", "THROUGH", "FROM", "TONIGHT", "TOMORROW",
        "THIS AFTERNOON", "THIS EVENING", "OVERNIGHT", "EARLY MORNING",
        "LATE TONIGHT", "PM CDT", "AM CDT", "PM EDT", "AM EDT",
        "PM PDT", "AM PDT", "PM MDT", "AM MDT", "PM EST", "AM EST",
        "HOURS", "MINUTES", "EXPIRE AT",

        # Places / modifiers (88-111)
        "COUNTY", "COUNTIES", "PARISH", "ZONE", "ZONES",
        "NATIONAL WEATHER SERVICE", "NORTHWEST", "NORTHEAST",
        "SOUTHWEST", "SOUTHEAST", "NORTHERN", "SOUTHERN", "EASTERN",
        "WESTERN", "CENTRAL", "MILES", "INCHES", "FEET", "MPH",
        "MPH OR GREATER", "QUARTER SIZE", "HALF DOLLAR", "SIZE",
        "RADAR INDICATED",

        # Common verbs/conjunctions (112-127)
        "IS", "ARE", "WAS", "WERE", "WILL", "WILL BE", "HAS BEEN",
        "HAVE BEEN", "IS IN EFFECT", "ISSUED", "INCLUDES", "INCLUDING",
        "AFFECTING", "EXPECTED TO", "CAPABLE OF", "LIKELY",
    ]

    out = {
        "version": 1,
        "escape_byte": 0xFE,
        "phrase_count": len(phrases),
        "phrases": phrases,
    }
    path = out_dir / "weather_dict.json"
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    size = path.stat().st_size / 1024
    print(f"  weather_dict:  {len(phrases):>6} phrases, {size:.0f} KB")


def build_protocol_codes(out_dir: Path) -> None:
    """protocol.json is maintained by hand next to the wire code (see
    protocol/meshwx.py, core/space_weather.py, core/vtec_names.py) and is
    versioned with it. The builder only checks it parses and reports size,
    so a rebuild can never roll the file back to an older layout."""
    path = out_dir / "protocol.json"
    data = json.loads(path.read_text())
    size = path.stat().st_size / 1024
    print(f"  protocol.json: v{data.get('version')} kept ({size:.1f} KB)")


def _load_pfm_products() -> list[tuple[str, str]]:
    """Return a list of (filename, raw_text) tuples for PFM products.

    Prefers the bot's local EMWIN cache at data/emwin_cache/products.jsonl
    if it exists and has content. Falls back to downloading a fresh 3-hour
    EMWIN bundle from NOAA (dev-box only; production never runs this).
    """
    pfms: list[tuple[str, str]] = []

    # Try local cache first
    if EMWIN_CACHE.exists():
        try:
            with EMWIN_CACHE.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fname = rec.get("filename", "")
                    text = rec.get("raw_text", "")
                    if text and "PFM" in fname.upper():
                        pfms.append((fname, text))
            if pfms:
                print(f"  (loaded {len(pfms)} PFM products from local cache)")
                return pfms
        except Exception as exc:
            print(f"  (local cache read failed: {exc})")

    # Fallback: fetch from NOAA (needs internet)
    print(f"  (no local cache, fetching {EMWIN_BUNDLE_URL})")
    try:
        import httpx
    except ImportError:
        print("  (httpx not installed, skipping PFM fetch)")
        return []

    try:
        resp = httpx.get(EMWIN_BUNDLE_URL, timeout=60.0)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  (bundle fetch failed: {exc})")
        return []

    def _extract(data: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".zip"):
                        try:
                            _extract(zf.read(name))
                        except Exception:
                            pass
                    elif name.lower().endswith(".txt") and "PFM" in name.upper():
                        try:
                            text = zf.read(name).decode(
                                "utf-8", errors="replace"
                            ).strip()
                            if text:
                                pfms.append((name, text))
                        except Exception:
                            pass
        except zipfile.BadZipFile:
            pass

    _extract(resp.content)
    print(f"  (extracted {len(pfms)} PFM products from bundle)")
    return pfms


# PFM forecast point header patterns
_PFM_AFOS_RE = re.compile(r"^PFM([A-Z]{3})$")
_PFM_UGC_RE = re.compile(r"^([A-Z]{2}Z\d{3})(?:[->]\d{3})*-\d{6}-\s*$")
_PFM_COORD_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)N\s+(\d+(?:\.\d+)?)W\s+Elev")


def _scrape_pfm_points(text: str) -> list[dict]:
    """Scrape forecast-point headers from a single PFM product.

    Returns a list of dicts with keys: name, wfo, lat, lon, zone.
    Uses a lightweight line-by-line scan. This is scope-limited to what's
    needed for pfm_points.json (name + coords + zone) — not a full PFM
    column parser, which is Commit 2 scope.
    """
    lines = [line.strip() for line in text.splitlines()]

    # Find the AFOS line to get the WFO
    wfo = "???"
    for line in lines[:15]:
        m = _PFM_AFOS_RE.match(line)
        if m:
            wfo = m.group(1)
            break

    points: list[dict] = []
    i = 0
    while i < len(lines):
        m_ugc = _PFM_UGC_RE.match(lines[i])
        if not m_ugc:
            i += 1
            continue
        zone = m_ugc.group(1)

        # Next non-empty line = point name
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j >= len(lines):
            break
        name = lines[j]

        # Next non-empty line = coordinates
        k = j + 1
        while k < len(lines) and not lines[k]:
            k += 1
        if k >= len(lines):
            break
        m_coord = _PFM_COORD_RE.match(lines[k])
        if m_coord:
            try:
                lat = float(m_coord.group(1))
                lon = -float(m_coord.group(2))
            except ValueError:
                i = k
                continue
            points.append({
                "name": name,
                "wfo": wfo,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "zone": zone,
            })
        i = k + 1
    return points


def _fetch_shapefile(url: str, expected_md5: str | None = None) -> Path:
    """Download a shapefile ZIP to the .cache/nws_shapefiles/ directory.

    Caches forever — re-uses the local file on subsequent runs. Returns the
    path to the cached ZIP. Raises if download fails and no cached copy
    exists.
    """
    import hashlib

    SHAPEFILE_CACHE.mkdir(parents=True, exist_ok=True)
    dest = SHAPEFILE_CACHE / url.rsplit("/", 1)[-1]

    if dest.exists():
        if expected_md5:
            actual = hashlib.md5(dest.read_bytes()).hexdigest()
            if actual != expected_md5:
                print(f"  (cache MD5 mismatch for {dest.name}, re-downloading)")
                dest.unlink()
        if dest.exists():
            print(f"  (using cached {dest.name})")
            return dest

    print(f"  (fetching {url})")
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx required to download shapefile") from exc

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)

    if expected_md5:
        actual = hashlib.md5(dest.read_bytes()).hexdigest()
        if actual != expected_md5:
            dest.unlink()
            raise RuntimeError(
                f"Downloaded shapefile MD5 mismatch: got {actual}, expected {expected_md5}"
            )
    print(f"  (cached to {dest})")
    return dest


def build_zones_geojson(out_dir: Path) -> None:
    """Generate client_data/zones.geojson from the NWS public zones shapefile.

    Downloads the NWS shapefile on first run (into .cache/, git-ignored),
    loads with geopandas, joins against our zones.json to filter to known
    zones, simplifies geometries with shapely (~1 km tolerance), and writes
    a compact GeoJSON keyed by zone code. Client renders warning polygons
    from this file directly — no runtime geometry transmission.
    """
    try:
        import geopandas as gpd
    except ImportError:
        print("  zones.geojson: SKIPPED (geopandas not installed)")
        return

    try:
        shapefile_zip = _fetch_shapefile(
            NWS_ZONES_SHAPEFILE_URL, NWS_ZONES_SHAPEFILE_MD5
        )
    except Exception as exc:
        print(f"  zones.geojson: SKIPPED (shapefile fetch failed: {exc})")
        return

    # geopandas can read a zipped shapefile directly via zip:// URI
    try:
        gdf = gpd.read_file(f"zip://{shapefile_zip}")
    except Exception as exc:
        print(f"  zones.geojson: SKIPPED (shapefile read failed: {exc})")
        return

    # The shapefile's STATE_ZONE field is "TX192"; our canonical form is "TXZ192"
    # (matches zones.json keys). Build the canonical code and drop rows that
    # can't be matched to our metadata.
    known_zones: set[str] = set(json.loads((GEODATA / "zones.json").read_text()).keys())

    def to_canonical(state_zone: str) -> str | None:
        if not state_zone or len(state_zone) < 3:
            return None
        code = f"{state_zone[:2]}Z{state_zone[2:]}"
        return code if code in known_zones else None

    gdf["code"] = gdf["STATE_ZONE"].apply(to_canonical)
    filtered = gdf[gdf["code"].notna()].copy()
    dropped = len(gdf) - len(filtered)

    # Simplify: 0.01° ≈ ~1 km tolerance. NWS zones are huge; this is
    # visually indistinguishable at any map zoom a weather app uses, and
    # cuts the GeoJSON size dramatically.
    filtered["geometry"] = filtered["geometry"].simplify(
        tolerance=0.01, preserve_topology=True
    )

    # Keep only the fields we actually need in the bundle
    out_gdf = filtered[["code", "geometry"]].rename(columns={"code": "code"})

    # Write as minified GeoJSON
    path = out_dir / "zones.geojson"
    # geopandas writes pretty JSON by default; we want minified for bundle size
    geojson_text = out_gdf.to_json(drop_id=True)
    # to_json is already single-line/compact — write directly
    path.write_text(geojson_text)

    size_mb = path.stat().st_size / 1024 / 1024
    matched = len(filtered)
    missing_from_shapefile = len(known_zones) - len(
        set(filtered["code"])
    )
    print(
        f"  zones.geojson: {matched:>6} features, {size_mb:.1f} MB  "
        f"(dropped {dropped} unknown, missing {missing_from_shapefile} of ours)"
    )


def build_pfm_points(out_dir: Path) -> None:
    """Generate client_data/pfm_points.json from real PFM products.

    Output format (array form for compact JSON):
        {"version": 1, "points": [[name, wfo, lat, lon, zone], ...]}

    Array INDEX is the pfm_point_id used in LOC_PFM_POINT wire encoding.
    Ordering is deterministic (sorted by name) so indices are stable across
    rebuilds as long as the set of PFMs doesn't change.
    """
    pfms = _load_pfm_products()
    if not pfms:
        print("  pfm_points:   SKIPPED (no PFM products available)")
        return

    # Scrape all points from all PFMs
    all_points: list[dict] = []
    for _name, text in pfms:
        all_points.extend(_scrape_pfm_points(text))

    # Deduplicate by (name, wfo) — same point can appear in updates
    seen: dict[tuple[str, str], dict] = {}
    for p in all_points:
        key = (p["name"], p["wfo"])
        # Keep the first occurrence (PFMs within one bundle are homogeneous)
        if key not in seen:
            seen[key] = p

    # Deterministic ordering for stable indices across rebuilds
    ordered = sorted(seen.values(), key=lambda p: (p["name"], p["wfo"]))

    # Compact array form — see docstring
    out = {
        "version": 1,
        "points": [
            [p["name"], p["wfo"], p["lat"], p["lon"], p["zone"]]
            for p in ordered
        ],
    }
    path = out_dir / "pfm_points.json"
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    size = path.stat().st_size / 1024
    print(f"  pfm_points:    {len(ordered):>6} points, {size:.0f} KB")


def _read_gazetteer(path: Path) -> list[tuple[str, float, float]]:
    """(ZIP, lat, lon) per ZCTA, sorted by ZIP, from the tab-separated
    gazetteer (.txt, or the .zip it ships in). Points rounded to 4 decimals."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            text = zf.read(name).decode("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    gi, ai, oi = head.index("GEOID"), head.index("INTPTLAT"), head.index("INTPTLONG")
    rows: dict[str, tuple[float, float]] = {}
    for line in lines[1:]:
        f = [x.strip() for x in line.split("\t")]
        if len(f) <= max(gi, ai, oi) or not f[gi].isdigit():
            continue
        rows.setdefault(f[gi].zfill(5), (round(float(f[ai]), 4), round(float(f[oi]), 4)))
    return [(z, la, lo) for z, (la, lo) in sorted(rows.items())]


def _nearest_places(points: list[tuple[float, float]], places: list) -> list[int]:
    """Index of the nearest place to each (lat, lon) by great circle, ties to
    the lower index. Places sit in 1-degree cells: a point scans outward to a
    first candidate, then every cell that could hold one as near."""
    cells: dict[tuple[int, int], list[tuple[float, float, float, int]]] = {}
    for i, p in enumerate(places):
        la, lo = math.radians(p[2]), math.radians(p[3])
        cells.setdefault((math.floor(p[2]), math.floor(p[3]) % 360), []).append((la, lo, math.cos(la), i))

    def scan(keys, la, lo, cl, best):
        for key in keys:
            for pla, plo, pcl, i in cells.get(key, ()):
                # Haversine term: ranks like the distance, exact at short range.
                h = math.sin((pla - la) / 2) ** 2 + cl * pcl * math.sin((plo - lo) / 2) ** 2
                if h < best[0] or (h == best[0] and i < best[1]):
                    best = (h, i)
        return best

    out = []
    for lat, lon in points:
        la, lo, cl = math.radians(lat), math.radians(lon), math.cos(math.radians(lat))
        ci, cj = math.floor(lat), math.floor(lon)
        best, r = (math.inf, -1), 0
        while best[1] < 0 and r <= 180:
            ring = {(ci + di, (cj + dj) % 360) for di in range(-r, r + 1) for dj in range(-r, r + 1)
                    if max(abs(di), abs(dj)) == r}
            best = scan(ring, la, lo, cl, best)
            r += 1
        d = 2 * math.asin(min(1.0, math.sqrt(best[0])))       # radians
        dlat = math.degrees(d) + 1e-9
        dlon = (math.degrees(math.asin(math.sin(d) / cl)) + 1e-9
                if d < math.pi / 2 and math.sin(d) < cl else 180.0)
        box = {(i, j % 360) for i in range(math.floor(lat - dlat), math.floor(lat + dlat) + 1)
               for j in range(math.floor(lon - dlon), math.floor(lon + dlon) + 1)}
        out.append(scan(box, la, lo, cl, best)[1])
    return out


def build_zips(out_dir: Path, source: Path = ZCTA_GAZETTEER, places: list | None = None) -> None:
    """ZIP -> internal point and nearest bundled place (ZCTA_GAZETTEER_URL).

    Output: {"version": 1, "source": ..., "zips": [["78701", 30.2711, -97.7437, 12345], ...]}
    Sorted by ZIP, 5 characters with leading zeros. The last field indexes
    places.json `places`: the nearest place by great circle, ties to the lower
    index. Bot and apps resolve a ZIP by exact lookup here. ZCTAs approximate
    delivery ZIPs: PO-box-only and some business ZIPs have no row (unknown ZIP).
    """
    if not source.exists() and source.with_suffix(".zip").exists():
        source = source.with_suffix(".zip")
    path = out_dir / "zips.json"
    if not source.exists():
        kept = "existing zips.json kept" if path.exists() else "no zips.json"
        print(f"  zips.json:     SKIPPED ({source} missing, {kept}; get {ZCTA_GAZETTEER_URL})")
        return
    rows = _read_gazetteer(source)
    if places is None:
        places = json.loads((GEODATA / "places.json").read_text())
    nearest = _nearest_places([(la, lo) for _, la, lo in rows], places)
    out = {"version": 1, "source": ZCTA_SOURCE,
           "zips": [[z, la, lo, i] for (z, la, lo), i in zip(rows, nearest)]}
    path.write_text(json.dumps(out, separators=(",", ":")))
    size = path.stat().st_size / 1024 / 1024
    print(f"  zips.json:     {len(rows):>6} ZIPs, {size:.1f} MB")


def main():
    # Default output goes inside the package so it ships as package-data
    # and is available at runtime regardless of deployment location.
    default_out = ROOT / "meshcore_weather" / "client_data"
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building client preload bundle in {out_dir}/")
    build_zones(out_dir)
    build_places(out_dir)
    build_zips(out_dir)
    build_stations(out_dir)
    build_wfos(out_dir)
    build_state_index(out_dir)
    build_dictionary(out_dir)
    build_protocol_codes(out_dir)
    build_pfm_points(out_dir)
    build_zones_geojson(out_dir)

    total = sum(f.stat().st_size for f in out_dir.iterdir() if f.is_file())
    print(f"\nTotal: {total / 1024 / 1024:.2f} MB")
    print(f"Ship this directory with iOS and web clients.")


if __name__ == "__main__":
    main()
