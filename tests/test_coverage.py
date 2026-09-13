"""Tests for the Coverage filter (region targeting)."""

from meshcore_weather.geodata import resolver
from meshcore_weather.protocol.coverage import Coverage


class TestCoverageFromSources:
    def test_empty_is_broadcast_all(self):
        cov = Coverage.empty()
        assert cov.is_empty()
        assert cov.covers_any(["TXZ192"])  # empty accepts everything
        assert cov.covers_zone("TXZ192") is False  # explicit check fails
        assert cov.region_ids == set()

    def test_city_resolution(self):
        cov = Coverage.from_sources(cities=["Austin TX"])
        assert not cov.is_empty()
        assert "TXZ192" in cov.zones
        assert cov.covers_zone("TXZ192")
        assert cov.sources["cities"] == ["Austin TX"]

    def test_state_resolution(self):
        cov = Coverage.from_sources(states=["TX"])
        assert not cov.is_empty()
        # All zones should be Texas
        resolver.load()
        for code in cov.zones:
            z = resolver._zones[code]
            assert z["s"] == "TX"
        # Should have many zones
        assert len(cov.zones) > 100

    def test_wfo_resolution(self):
        cov = Coverage.from_sources(wfos=["EWX"])
        assert not cov.is_empty()
        resolver.load()
        for code in cov.zones:
            z = resolver._zones[code]
            assert z["w"] == "EWX"

    def test_mix_and_match(self):
        cov = Coverage.from_sources(
            cities=["Miami FL"],
            states=["OK"],
            wfos=["EWX"],
        )
        assert not cov.is_empty()
        resolver.load()
        # Must contain at least one zone from each source
        has_miami = any(
            resolver._zones[c]["s"] == "FL" for c in cov.zones if c in resolver._zones
        )
        has_ok = any(
            resolver._zones[c]["s"] == "OK" for c in cov.zones if c in resolver._zones
        )
        has_ewx = any(
            resolver._zones[c]["w"] == "EWX" for c in cov.zones if c in resolver._zones
        )
        assert has_miami
        assert has_ok
        assert has_ewx

    def test_covers_any(self):
        cov = Coverage.from_sources(states=["TX"])
        assert cov.covers_any(["TXZ192", "OKZ050"])
        assert cov.covers_any(["OKZ050", "TXZ192"])
        assert not cov.covers_any(["OKZ050", "KSZ100"])

    def test_bbox_computed(self):
        cov = Coverage.from_sources(states=["TX"])
        assert cov.bbox is not None
        n, s, w, e = cov.bbox
        # Texas roughly 26-36 N, -106 to -93 W
        assert n > 34
        assert s < 29
        assert w < -99
        assert e > -97

    def test_texas_overlaps_southern_region(self):
        """Texas coverage should overlap MeshWX region 0x3 (Southern)."""
        cov = Coverage.from_sources(states=["TX"])
        assert 0x3 in cov.region_ids

    def test_florida_overlaps_southeast_region(self):
        """Florida coverage should overlap MeshWX region 0x1 (Southeast)."""
        cov = Coverage.from_sources(states=["FL"])
        assert 0x1 in cov.region_ids

    def test_hawaii_overlaps_hawaii_region(self):
        """Hawaii coverage should overlap MeshWX region 0x8 (Hawaii)."""
        cov = Coverage.from_sources(states=["HI"])
        assert 0x8 in cov.region_ids


class TestPolygonCoverage:
    def test_polygon_containing_zone_centroid(self):
        """A polygon around Austin should match a TX coverage."""
        cov = Coverage.from_sources(cities=["Austin TX"])
        # Rough polygon around central Texas
        polygon = [
            (31.0, -98.5),
            (31.0, -97.0),
            (30.0, -97.0),
            (30.0, -98.5),
        ]
        assert cov.covers_polygon(polygon)

    def test_polygon_far_away(self):
        """A polygon in Alaska should not match a TX coverage."""
        cov = Coverage.from_sources(cities=["Austin TX"])
        polygon = [
            (65.0, -150.0),
            (66.0, -150.0),
            (66.0, -149.0),
            (65.0, -149.0),
        ]
        assert not cov.covers_polygon(polygon)


class TestCoverageSummary:
    def test_summary_empty(self):
        s = Coverage.empty().summary()
        assert "all regions" in s.lower()

    def test_summary_with_sources(self):
        cov = Coverage.from_sources(
            cities=["Austin TX"],
            states=["OK"],
            wfos=["EWX"],
        )
        s = cov.summary()
        assert "zones" in s
        assert "cities" in s or "states" in s or "wfos" in s


class TestRadiusCoverage:
    def test_radius_zones_are_local_only(self):
        cov = Coverage.from_sources(cities=["Austin TX"], radius_km=120)
        assert not cov.is_empty()
        assert cov.center is not None and abs(cov.center[0] - 30.3) < 0.2
        assert cov.covers_zone("TXZ192")          # Travis
        assert cov.covers_zone("TXZ173")          # Williamson
        assert cov.covers_zone("TXZ191")          # Hays
        assert not cov.covers_zone("TXZ213")      # Houston (Harris) is ~230 km away
        assert not cov.covers_any(["LAZ040"])     # Lake Charles river floods stay out
        assert 15 < len(cov.zones) < 60

    def test_wfo_does_not_imply_whole_state(self):
        cov = Coverage.from_sources(wfos=["EWX"])
        assert cov.covers_zone("TXZ192")
        assert not cov.covers_any(["TXZ213"])     # Houston: HGX, not EWX
        assert not cov.explicit_states

    def test_small_polygon_inside_coverage_is_covered(self):
        cov = Coverage.from_sources(cities=["Austin TX"], radius_km=120)
        # A small SVR polygon over south Austin that contains no zone centroid.
        poly = [(30.20, -97.80), (30.22, -97.72), (30.16, -97.70), (30.15, -97.79)]
        assert cov.covers_polygon(poly)
        far = [(29.70, -95.40), (29.72, -95.30), (29.65, -95.30)]   # Houston
        assert not cov.covers_polygon(far)
