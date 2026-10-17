"""Coverage: the bot's broadcast area, built from operator-configured
cities, states, and NWS WFOs.

Used to filter warnings and radar grids before broadcast so we only use
airtime for data that affects the bot's mesh area.
"""

import logging
import math
from collections.abc import Iterable

from meshcore_weather.config import settings
from meshcore_weather.geodata import resolver
from meshcore_weather.protocol.meshwx import REGIONS


def _point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def _circle(lat: float, lon: float, radius_km: float):
    """Approximate geographic circle as a shapely polygon (lon/lat degrees)."""
    from shapely.geometry import Point
    from shapely.affinity import scale
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return scale(Point(lon, lat).buffer(1.0, 64), xfact=dlon, yfact=dlat, origin=(lon, lat))


def zones_within_radius(lat: float, lon: float, radius_km: float) -> set[str]:
    """Public forecast zones whose polygon intersects the circle. Falls back
    to zone centroids within the radius if shapes are unavailable."""
    resolver.load()
    if resolver._load_polygons():
        circle = _circle(lat, lon, radius_km)
        out = set()
        for i in resolver._poly_tree.query(circle):
            if resolver._poly_geoms[i].intersects(circle):
                out.add(resolver._poly_codes[i])
        return out
    from meshcore_weather.geodata import _haversine
    return {code for code, z in resolver._zones.items()
            if _haversine(lat, lon, z["la"], z["lo"]) <= radius_km}


def counties_within_radius(lat: float, lon: float, radius_km: float) -> set[str]:
    """Counties whose outline intersects the circle: the same rule as the
    zones, for the products that name counties (watches). Falls back to each
    county's inside point within the radius if outlines are unavailable."""
    if resolver._load_county_polygons():
        circle = _circle(lat, lon, radius_km)
        return {resolver._county_codes[i] for i in resolver._county_tree.query(circle)
                if resolver._county_geoms[i].intersects(circle)}
    from meshcore_weather.geodata import _haversine
    return {code for code, c in resolver.counties().items()
            if _haversine(lat, lon, c["lat"], c["lon"]) <= radius_km}


def counties_of_offices(wfos: Iterable[str]) -> set[str]:
    """Counties an NWS office serves: those whose inside point lies in one of
    its public zones. Offices' areas follow county lines, so a county is one
    office's, and its point settles which."""
    wfos = set(wfos)
    resolver.load()
    states = {z["s"] for z in resolver._zones.values() if z.get("w") in wfos}
    out = set()
    for code, c in resolver.counties().items():
        if c.get("state") not in states:
            continue
        zone = resolver.zone_containing(c["lat"], c["lon"])
        if zone is None:
            nearest = resolver._nearest_zones(c["lat"], c["lon"], n=1)
            zone = nearest[0] if nearest else None
        if zone and resolver._zones.get(zone, {}).get("w") in wfos:
            out.add(code)
    return out


logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # (north, south, west, east)


def _split_csv(s: str) -> list[str]:
    """Split comma-separated string, strip whitespace, drop empties."""
    return [p.strip() for p in s.split(",") if p.strip()]


class Coverage:
    """Canonical set of NWS zones, and of counties, that the bot cares about.

    Sources (all optional, all additive):
      - Cities: "Austin TX" → each city's resolved zone and its county
      - Radius around the first city → every zone and county whose outline
        intersects the circle
      - States: "TX" → all zones where zone.s == "TX", and the state's counties
      - WFOs: "EWX" → all zones where zone.w == "EWX", and the counties it serves

    The counties are there for the products that name counties and carry no
    polygon: tornado and severe thunderstorm watches (WCN). Matching only
    zones dropped every one of them.

    Empty coverage (no sources) means "broadcast everything" — legacy behavior.
    """

    def __init__(
        self,
        zones: set[str] | None = None,
        sources: dict | None = None,
        counties: set[str] | None = None,
    ):
        self.zones: set[str] = zones or set()
        self.counties: set[str] = counties or set()
        self.sources: dict = sources or {"cities": [], "states": [], "wfos": []}
        self.center: tuple[float, float] | None = self.sources.get("center")
        self.radius_km: float = float(self.sources.get("radius_km") or 0)
        self.bbox: BBox | None = None
        self.region_ids: set[int] = set()
        # States the operator *explicitly* covers (via home_states or via WFOs
        # whose served state we can determine). Used to match county-FIPS UGCs
        # (e.g. TXC029) which are not in the zones set.
        self.explicit_states: set[str] = set()
        for s in self.sources.get("states", []) or []:
            if s:
                self.explicit_states.add(s.upper())
        self._recompute_bbox_and_regions()
        # NOTE: listing a WFO no longer implies its whole state. That rule
        # turned MCW_HOME_WFOS=EWX into "all of Texas" and flooded Austin
        # with Lake Charles river warnings. Only MCW_HOME_STATES widens.

    @classmethod
    def empty(cls) -> "Coverage":
        """Empty coverage = broadcast everything (legacy behavior)."""
        return cls()

    @classmethod
    def from_config(cls) -> "Coverage":
        """Build coverage from settings: a radius around the first home city
        (the normal case), widened by any extra cities/states/WFOs."""
        return cls.from_sources(
            cities=_split_csv(settings.home_cities),
            states=_split_csv(settings.home_states),
            wfos=_split_csv(settings.home_wfos),
            radius_km=settings.home_radius_km,
        )

    @classmethod
    def from_sources(
        cls,
        cities: list[str] | None = None,
        states: list[str] | None = None,
        wfos: list[str] | None = None,
        radius_km: float = 0,
    ) -> "Coverage":
        """Build coverage from explicit lists of cities/states/WFOs, plus a
        radius (km) around the first city: every public zone whose polygon
        intersects that circle is covered."""
        cities = cities or []
        states = [s.upper() for s in (states or [])]
        wfos = [w.upper() for w in (wfos or [])]

        resolver.load()
        zones: set[str] = set()
        counties: set[str] = set()
        center: tuple[float, float] | None = None

        # Cities → the zone and the county each city is in (polygon), plus
        # the radius circle around the first one.
        for city in cities:
            loc = resolver.resolve(city)
            if loc and loc.get("zones"):
                zones.add(loc["zones"][0])
                county = resolver.county_for_point(loc["lat"], loc["lon"])
                if county:
                    counties.add(county)
                if center is None:
                    center = (loc["lat"], loc["lon"])
        if center is not None and radius_km > 0:
            zones.update(zones_within_radius(center[0], center[1], radius_km))
            counties.update(counties_within_radius(center[0], center[1], radius_km))

        # States → all zones and counties in that state
        if states:
            for code, z in resolver._zones.items():
                if z.get("s") in states:
                    zones.add(code)
            counties.update(code for code, c in resolver.counties().items() if c.get("state") in states)

        # WFOs → all zones and counties served by that office
        if wfos:
            for code, z in resolver._zones.items():
                if z.get("w") in wfos:
                    zones.add(code)
            counties.update(counties_of_offices(wfos))

        return cls(
            zones=zones,
            counties=counties,
            sources={"cities": cities, "states": states, "wfos": wfos,
                     "center": center, "radius_km": radius_km if center else 0},
        )

    # -- Derived properties --

    def is_empty(self) -> bool:
        """No coverage set → broadcast everything (legacy behavior)."""
        return not self.zones

    def _recompute_bbox_and_regions(self) -> None:
        """Compute bbox of all zone centroids + overlapping MeshWX regions."""
        if not self.zones:
            self.bbox = None
            self.region_ids = set()
            return

        lats: list[float] = []
        lons: list[float] = []
        for code in self.zones:
            z = resolver._zones.get(code)
            if z:
                lats.append(z["la"])
                lons.append(z["lo"])

        if not lats:
            self.bbox = None
            self.region_ids = set()
            return

        # Pad the bbox by ~1 degree so region overlap is tolerant
        pad = 1.0
        self.bbox = (
            max(lats) + pad,
            min(lats) - pad,
            min(lons) - pad,
            max(lons) + pad,
        )

        # Find MeshWX regions that overlap the bbox
        self.region_ids = set()
        n, s, w, e = self.bbox
        for rid, region in REGIONS.items():
            # Standard AABB overlap test
            if not (region["s"] > n or region["n"] < s or region["w"] > e or region["e"] < w):
                self.region_ids.add(rid)

    # -- Filtering API --

    def covers_zone(self, zone_code: str) -> bool:
        """Is this exact zone code in our coverage?"""
        return zone_code in self.zones

    def covers_any(self, zone_codes: Iterable[str], by_county: bool = True) -> bool:
        """Does any of these UGC codes intersect our coverage?

        Accepts both NWS zone codes (TXZ192) and county codes (TXC453). Match
        rules, in order:
          1. Exact zone match against our zones set (narrow, precise)
          2. Exact county match against our counties set. A watch (WCN)
             names counties and has no polygon, so this is the only way one
             is in coverage. `by_county=False` skips it, for a storm-based
             warning: its UGC line lists the counties its polygon touches,
             not the area warned, and the caller judges it by the polygon.
          3. If the operator explicitly covers a state (home_states),
             accept any UGC whose 2-letter prefix matches.
        """
        if not self.zones:
            return True  # empty coverage = accept everything
        codes = list(zone_codes)
        for code in codes:
            if code in self.zones:
                return True
        if by_county:
            for code in codes:
                if code in self.counties:
                    return True
        if self.explicit_states:
            for code in codes:
                if len(code) >= 2 and code[:2].upper() in self.explicit_states:
                    return True
        return False

    def covers_polygon(self, vertices: list[tuple[float, float]]) -> bool:
        """Does a storm-based polygon touch our area?

        With a centre/radius: the polygon intersects the coverage circle
        (exact, and catches small SVR/TOR polygons that contain no zone
        centroid). Otherwise: it intersects any covered zone's polygon, or,
        if shapes are unavailable, contains a covered zone centroid.
        """
        if not self.zones or not vertices or len(vertices) < 3:
            return self.is_empty()  # empty coverage = accept
        try:
            from shapely.geometry import Polygon
            poly = Polygon([(lon, lat) for lat, lon in vertices])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if self.center and self.radius_km > 0:
                return poly.intersects(_circle(self.center[0], self.center[1], self.radius_km))
            if resolver._load_polygons():
                for i, code in enumerate(resolver._poly_codes):
                    if code in self.zones and resolver._poly_geoms[i].intersects(poly):
                        return True
                return False
        except Exception:
            pass
        for code in self.zones:
            z = resolver._zones.get(code)
            if z and _point_in_polygon(z["la"], z["lo"], vertices):
                return True
        return False

    def summary(self) -> str:
        """Human-readable summary for logging/portal display."""
        if self.is_empty():
            return "all regions (no filter)"
        parts = []
        if self.center and self.radius_km:
            parts.append(f"{self.radius_km:.0f} km around {self.center[0]:.2f},{self.center[1]:.2f}")
        if self.sources.get("cities"):
            parts.append(f"{len(self.sources['cities'])} cities")
        if self.sources.get("states"):
            parts.append(f"states={','.join(self.sources['states'])}")
        if self.sources.get("wfos"):
            parts.append(f"wfos={','.join(self.sources['wfos'])}")
        return f"{len(self.zones)} zones, {len(self.counties)} counties ({', '.join(parts)})"
