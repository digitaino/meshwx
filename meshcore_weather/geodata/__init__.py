"""Offline geolocation: resolve city names, station IDs, and zone codes.

Bundled data files (~1.7 MB total):
    zones.json   - 4,029 NWS forecast zones with centroids, counties, WFO IDs
    places.json  - 32,333 US Census places with coordinates
    stations.json - 2,237 active US METAR stations with coordinates
"""

import json
import logging
import math
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent


def _load_json(name: str):
    path = _DATA_DIR / name
    with open(path) as f:
        return json.load(f)


def _normalize(text: str) -> str:
    """Strip accents and normalize to ASCII uppercase for matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


class LocationResolver:
    """Resolve natural language locations to NWS zone codes, offline.

    Handles:
        - Station IDs: "KAUS" → zone TXZ192, WFO EWX
        - Zone codes: "TXZ192" → directly
        - City + state: "Austin TX" → zone TXZ192
        - City only: "Austin" → best guess (largest/first match)
        - State only: "TX" → all zones in state
    """

    # A candidate place farther than this from the bot's home is treated as
    # "not local": bare names then prefer the local candidate, and replies
    # mention the ambiguity.
    LOCAL_KM = 300.0

    def __init__(self):
        self._zones: dict = {}      # zone_code -> {n, w, s, la, lo, c}
        self._places: list = []     # [[NAME, STATE, lat, lon], ...]
        self._stations: dict = {}   # ICAO -> {n, s, la, lo}
        self._loaded = False
        self._home: tuple[float, float] | None = None   # bot coverage centre
        self._poly_tree = None      # shapely STRtree over public zone polygons
        self._poly_geoms: list = []
        self._poly_codes: list[str] = []
        self._poly_tried = False

    def load(self) -> None:
        if self._loaded:
            return
        self._zones = _load_json("zones.json")
        self._places = _load_json("places.json")
        self._stations = _load_json("stations.json")
        self._build_three_letter_index()
        self._loaded = True
        logger.info(
            "Location data loaded: %d zones, %d places, %d stations",
            len(self._zones), len(self._places), len(self._stations),
        )
        self._set_home_from_settings()

    def _set_home_from_settings(self) -> None:
        """Default the coverage centre to the first MCW_HOME_CITIES entry so
        every consumer (bot, CLI simulator, portal, tests) disambiguates the
        same way without having to remember to call set_home()."""
        if self._home is not None:
            return
        try:
            from meshcore_weather.config import settings
            first = settings.home_cities.split(",")[0].strip() if settings.home_cities else ""
        except Exception:
            return
        if not first:
            return
        loc = self.resolve(first)
        if loc:
            self._home = (loc["lat"], loc["lon"])
            logger.info("Resolver home: %s (%.2f, %.2f)", loc["name"], loc["lat"], loc["lon"])

    # Common IATA codes that differ from the ICAO suffix.
    # Only needed where dropping the ICAO prefix doesn't yield the IATA code.
    _IATA_TO_ICAO = {
        "SJU": "TJSJ",  # San Juan PR
        "BQN": "TJBQ",  # Aguadilla PR
        "SIG": "TJIG",  # Isla Grande PR
        "NRR": "TJNR",  # Roosevelt Roads PR
    }

    def _build_three_letter_index(self) -> None:
        """Build a 3-letter → ICAO lookup from station data.

        For collisions, prefer K (CONUS) > PH (Hawaii) > TJ (Caribbean)
        > PA (Alaska) > others.
        """
        priority = {"K": 0, "PH": 1, "TJ": 1, "PA": 2}
        index: dict[str, str] = {}
        for icao in self._stations:
            if len(icao) != 4:
                continue
            three = icao[1:].upper()
            prefix = icao[:2] if icao[0] in ("P", "T") else icao[0]
            new_pri = priority.get(prefix, 3)
            if three in index:
                old_icao = index[three]
                old_prefix = old_icao[:2] if old_icao[0] in ("P", "T") else old_icao[0]
                old_pri = priority.get(old_prefix, 3)
                if new_pri >= old_pri:
                    continue
            index[three] = icao
        # Add IATA overrides for stations where IATA != ICAO suffix
        for iata, icao in self._IATA_TO_ICAO.items():
            if icao in self._stations:
                index[iata] = icao
        self._three_to_icao = index

    def resolve(self, query: str) -> dict | None:
        """Resolve a location query to zone info.

        Returns dict with keys:
            zones: list[str]  - NWS zone codes (e.g. ["TXZ192"])
            wfos: list[str]   - WFO IDs (e.g. ["EWX"])
            station: str|None - nearest METAR station (e.g. "KAUS")
            name: str         - human-readable resolved name
            lat: float
            lon: float
        Or None if unresolvable.
        """
        self.load()
        query = query.strip()
        if not query:
            return None

        upper = query.upper()

        # 1. Direct zone code (e.g. TXZ192)
        if len(upper) >= 5 and upper[2] == "Z" and upper[3:].isdigit():
            return self._resolve_zone(upper)

        # 2. Station ID (e.g. KAUS, TJSJ - any 4-letter ICAO code in our database)
        if len(upper) == 4 and upper.isalpha() and upper in self._stations:
            return self._resolve_station(upper)

        # 2b. 3-letter code (e.g. AUS → KAUS, SJU → TJSJ, HNL → PHNL)
        if len(upper) == 3 and upper.isalpha() and upper in self._three_to_icao:
            return self._resolve_station(self._three_to_icao[upper])

        # 3. City + State (e.g. "Austin TX", "Round Rock, TX")
        parts = upper.replace(",", " ").split()
        if len(parts) >= 2:
            state = parts[-1]
            if len(state) == 2 and state.isalpha():
                city = " ".join(parts[:-1])
                result = self._resolve_city_state(city, state)
                if result:
                    return result

        # 4. Try as city name without state
        return self._resolve_city(upper)

    # -- Home / coverage centre -------------------------------------------------

    def set_home(self, lat: float, lon: float) -> None:
        """Set the bot's coverage centre. Used to disambiguate bare city names
        and duplicate (name, state) pairs in favour of the local candidate."""
        self._home = (lat, lon)

    def home(self) -> tuple[float, float] | None:
        return self._home

    # -- Zone lookup by polygon --------------------------------------------------

    def _load_polygons(self) -> bool:
        """Lazily build an STRtree over the bundled public-zone polygons.

        Returns False (and never retries) if shapely or zones.geojson is
        unavailable, in which case zone lookup falls back to nearest centroid.
        """
        if self._poly_tree is not None:
            return True
        if self._poly_tried:
            return False
        self._poly_tried = True
        try:
            from shapely.geometry import shape
            from shapely.strtree import STRtree
            # The polygons ship in the client bundle (client_data/), not in
            # geodata/, so clients and the bot render from the same shapes.
            gj_path = Path(__file__).resolve().parent.parent / "client_data" / "zones.geojson"
            gj = json.loads(gj_path.read_text())
        except Exception as exc:  # shapely missing, file missing, bad JSON
            logger.warning("Zone polygons unavailable (%s); using centroid fallback", exc)
            return False
        geoms, codes = [], []
        for feat in gj.get("features", []):
            code = (feat.get("properties") or {}).get("code")
            if not code or code not in self._zones:   # public forecast zones only
                continue
            try:
                geoms.append(shape(feat["geometry"]))
                codes.append(code)
            except Exception:
                continue
        if not geoms:
            return False
        self._poly_tree = STRtree(geoms)
        self._poly_geoms = geoms
        self._poly_codes = codes
        logger.info("Zone polygons loaded: %d public zones", len(codes))
        return True

    def zone_containing(self, lat: float, lon: float) -> str | None:
        """Public forecast zone whose polygon contains (lat, lon), or None."""
        if not self._load_polygons():
            return None
        from shapely.geometry import Point
        pt = Point(lon, lat)
        for i in self._poly_tree.query(pt):
            if self._poly_geoms[i].covers(pt):
                return self._poly_codes[i]
        return None

    def _zones_for_point(self, lat: float, lon: float, n: int = 2) -> tuple[list[str], str]:
        """Zones for a point: the containing polygon first, then nearest
        centroids as secondaries. Returns (zones, method) where method is
        "polygon" or "centroid" so callers can tell how sure we are."""
        inside = self.zone_containing(lat, lon)
        nearest = self._nearest_zones(lat, lon, n=n + 1)
        if inside:
            rest = [z for z in nearest if z != inside][: max(0, n - 1)]
            return [inside] + rest, "polygon"
        # Outside every polygon (coastline simplification, islands, bad
        # coordinates): take the polygon whose EDGE is closest, which is far
        # more reliable than the closest centroid for coastal cities.
        edge = self.zone_nearest_edge(lat, lon)
        if edge:
            rest = [z for z in nearest if z != edge][: max(0, n - 1)]
            return [edge] + rest, "nearest-edge"
        return nearest[:n], "centroid"

    def zone_nearest_edge(self, lat: float, lon: float, max_deg: float = 0.5) -> str | None:
        """Public zone whose polygon boundary is closest to the point (within
        ~max_deg degrees), for points that fall outside every polygon."""
        if not self._load_polygons():
            return None
        from shapely.geometry import Point
        pt = Point(lon, lat)
        box = pt.buffer(max_deg)
        best, best_d = None, float("inf")
        for i in self._poly_tree.query(box):
            d = self._poly_geoms[i].distance(pt)
            if d < best_d:
                best, best_d = self._poly_codes[i], d
        return best

    # -- Station ranking --------------------------------------------------------

    def rank_stations(self, lat: float, lon: float, n: int = 5) -> list[tuple[str, float]]:
        """Nearest n METAR stations as [(icao, km), ...], nearest first."""
        dists = [(_haversine(lat, lon, s["la"], s["lo"]), icao) for icao, s in self._stations.items()]
        dists.sort()
        return [(icao, round(d, 1)) for d, icao in dists[:n]]

    # -- Candidate ranking for names ---------------------------------------------

    def _rank_candidates(self, matches: list) -> tuple[list, list[str]]:
        """Order place candidates: nearest to home first when a home is set
        (so "round rock" near Austin means Round Rock TX, and duplicate
        names within a state pick the local one); otherwise keep file order.

        Returns (ordered_matches, other_states) where other_states lists the
        states of non-chosen candidates that are NOT local, for the reply to
        mention. Empty when the choice is unambiguous."""
        if len(matches) <= 1:
            return matches, []
        if self._home is None:
            ordered = list(matches)
        else:
            hlat, hlon = self._home
            ordered = sorted(matches, key=lambda p: _haversine(hlat, hlon, p[2], p[3]))
        chosen = ordered[0]
        others = []
        for p in ordered[1:]:
            if p[1] == chosen[1] and abs(p[2] - chosen[2]) < 0.01 and abs(p[3] - chosen[3]) < 0.01:
                continue   # same place listed twice
            if p[1] not in others and p[1] != chosen[1]:
                others.append(p[1])
        # If the chosen candidate is itself not local, the user needs to know
        # the name was ambiguous; if it is local, the choice is safe.
        if self._home is not None:
            hlat, hlon = self._home
            if _haversine(hlat, hlon, chosen[2], chosen[3]) <= self.LOCAL_KM:
                return ordered, []
        return ordered, others

    _PLACE_SUFFIXES = (" ZONA URBANA", " COMUNIDAD", " MUNICIPIO", " CDP", " CITY AND", " URBAN")

    @classmethod
    def _clean_place_name(cls, name: str) -> str:
        """'SAN JUAN ZONA URBANA' -> 'San Juan'. Census place names carry
        legal-form suffixes nobody types or wants back on a LoRa reply."""
        n = name.upper()
        for suf in cls._PLACE_SUFFIXES:
            if n.endswith(suf):
                n = n[: -len(suf)].rstrip()
        return n.title()

    def _result_for_place(self, place, ambiguous: list[str] | None = None) -> dict:
        lat, lon = place[2], place[3]
        zones, method = self._zones_for_point(lat, lon, n=2)
        wfos = list({self._zones[z]["w"] for z in zones if z in self._zones})
        stations = self.rank_stations(lat, lon)
        return {
            "zones": zones,
            "zone_method": method,
            "wfos": wfos,
            "station": stations[0][0] if stations else None,
            "station_km": stations[0][1] if stations else None,
            "stations": stations,
            "name": f"{self._clean_place_name(place[0])}, {place[1]}",
            "lat": lat,
            "lon": lon,
            "ambiguous": ambiguous or [],
        }

    def _resolve_zone(self, zone_code: str) -> dict | None:
        zone = self._zones.get(zone_code)
        if not zone:
            return None
        stations = self.rank_stations(zone["la"], zone["lo"])
        station = stations[0][0] if stations else None
        return {
            "zones": [zone_code],
            "zone_method": "explicit",
            "wfos": [zone["w"]],
            "station": station,
            "station_km": stations[0][1] if stations else None,
            "stations": stations,
            "name": f"{zone['n']}, {zone['s']}",
            "lat": zone["la"],
            "lon": zone["lo"],
            "ambiguous": [],
        }

    def _resolve_station(self, icao: str) -> dict | None:
        st = self._stations.get(icao)
        if not st:
            return None
        zones, method = self._zones_for_point(st["la"], st["lo"], n=2)
        wfos = list({self._zones[z]["w"] for z in zones if z in self._zones})
        # Shorten verbose airport names for LoRa display
        name = self._short_station_name(st["n"], st["s"])
        return {
            "zones": zones,
            "zone_method": method,
            "wfos": wfos,
            "station": icao,
            "station_km": 0.0,
            "stations": [(icao, 0.0)] + [s for s in self.rank_stations(st["la"], st["lo"]) if s[0] != icao][:4],
            "name": name,
            "lat": st["la"],
            "lon": st["lo"],
            "ambiguous": [],
        }

    @staticmethod
    def _short_station_name(name: str, state: str) -> str:
        """Shorten station names for compact display."""
        n = name.title()
        # Strip common airport suffixes
        for suffix in [" International Airport", " Intl Airport", " Intl Ap",
                       " Regional Airport", " Municipal Airport", " Airport",
                       " Arpt", " Ap", " Field"]:
            if n.lower().endswith(suffix.lower()):
                n = n[:-len(suffix)].rstrip(" -/")
                break
        return f"{n}, {state}"

    def _resolve_city_state(self, city: str, state: str) -> dict | None:
        city_n = _normalize(city)
        matches = [p for p in self._places if p[1] == state and _normalize(p[0]) == city_n]
        if not matches:
            matches = [p for p in self._places if p[1] == state and city_n in _normalize(p[0])]
        if not matches:
            return None
        ordered, others = self._rank_candidates(matches)
        return self._result_for_place(ordered[0], others)

    def _resolve_city(self, city: str) -> dict | None:
        city_n = _normalize(city)
        matches = [p for p in self._places if _normalize(p[0]) == city_n]
        if not matches:
            matches = [p for p in self._places if city_n in _normalize(p[0])]
        if not matches:
            return None
        ordered, others = self._rank_candidates(matches)
        return self._result_for_place(ordered[0], others)

    def resolve_by_place_index(self, idx: int) -> dict | None:
        """Resolve a place index to zone info (for LOC_PLACE requests)."""
        self.load()
        if idx < 0 or idx >= len(self._places):
            return None
        p = self._places[idx]
        return self.resolve_by_coords(p[2], p[3])

    def resolve_by_coords(self, lat: float, lon: float) -> dict | None:
        """Resolve GPS coordinates to zone info (for location-aware DM)."""
        self.load()
        zones, method = self._zones_for_point(lat, lon, n=2)
        if not zones:
            return None
        wfos = list({self._zones[z]["w"] for z in zones if z in self._zones})
        stations = self.rank_stations(lat, lon)
        station = stations[0][0] if stations else None
        # Find nearest place name
        best_place = None
        best_d = float("inf")
        for p in self._places:
            d = _haversine(lat, lon, p[2], p[3])
            if d < best_d:
                best_d = d
                best_place = p
        name = f"{best_place[0].title()}, {best_place[1]}" if best_place else f"{lat:.2f}, {lon:.2f}"
        return {
            "zones": zones,
            "zone_method": method,
            "wfos": wfos,
            "station": station,
            "station_km": stations[0][1] if stations else None,
            "stations": stations,
            "name": name,
            "lat": lat,
            "lon": lon,
            "ambiguous": [],
        }

    def _nearest_zones(self, lat: float, lon: float, n: int = 2) -> list[str]:
        """Find the n nearest NWS zones by Haversine distance to centroids."""
        dists = []
        for code, z in self._zones.items():
            d = _haversine(lat, lon, z["la"], z["lo"])
            dists.append((d, code))
        dists.sort()
        return [code for _, code in dists[:n]]

    def find_place_index(self, lat: float, lon: float) -> int | None:
        """Find the index of the nearest place in places.json by coordinates.

        Returns the array index (uint24 place_id for LOC_PLACE) or None if
        no places are loaded.
        """
        self.load()
        best_idx = None
        best_d = float("inf")
        for i, p in enumerate(self._places):
            d = _haversine(lat, lon, p[2], p[3])
            if d < best_d:
                best_d = d
                best_idx = i
        return best_idx

    def _nearest_station(self, lat: float, lon: float) -> str | None:
        """Find the nearest METAR station."""
        best_d = float("inf")
        best = None
        for icao, s in self._stations.items():
            d = _haversine(lat, lon, s["la"], s["lo"])
            if d < best_d:
                best_d = d
                best = icao
        return best


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


# Module-level singleton
resolver = LocationResolver()
