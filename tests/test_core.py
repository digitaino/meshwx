"""Tests for the core service layer and the resolver fixes behind it.

These pin the behaviour that the text commands, the scheduler, and the
on-demand path now share: polygon zones, home-aware disambiguation,
"nearest station that reports", "nearest forecast point by distance", and
text/binary renderings that come from the same object.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meshcore_weather.geodata import resolver
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.core import render_text, services

AUSTIN = (30.27, -97.74)
FIXTURES = Path(__file__).parent / "fixtures"


def fresh(filename: str) -> str:
    """Re-stamp a fixture's KWIN receipt time to now: the store drops products
    older than 12 h, and the fixtures are real files from a fixed date."""
    import re
    return re.sub(r"_C_KWIN_\d{14}", f"_C_KWIN_{datetime.now(timezone.utc):%Y%m%d%H%M%S}", filename)


@pytest.fixture(autouse=True)
def _home():
    resolver.load()
    resolver.set_home(*AUSTIN)
    yield
    resolver._home = None


# -- Resolver -----------------------------------------------------------------


class TestResolver:
    def test_zone_by_polygon_not_centroid(self):
        # Cedar Park is in Williamson County (TXZ173); the nearest zone
        # centroid is Travis (TXZ192). Only polygon containment gets it right.
        r = resolver.resolve("Cedar Park TX")
        assert r["zones"][0] == "TXZ173"
        assert r["zone_method"] == "polygon"

    def test_bare_name_prefers_local_candidate(self):
        r = resolver.resolve("round rock")
        assert r["name"] == "Round Rock, TX"
        assert r["ambiguous"] == []

    def test_duplicate_name_in_state_prefers_local(self):
        # places.json has two "Lago Vista, TX": a colonia on the Rio Grande
        # and the Travis County city. Home is Austin, so the city wins.
        r = resolver.resolve("Lago Vista TX")
        assert abs(r["lat"] - 30.45) < 0.05

    def test_far_ambiguous_name_is_flagged(self):
        r = resolver.resolve("springfield")
        assert r["ambiguous"], "a far-away ambiguous bare name must be flagged"
        assert "add state" in render_text.place_label(r)

    def test_stations_ranked_with_distance(self):
        r = resolver.resolve("Round Rock, TX")
        # Georgetown (KGTU) and Austin Executive (KEDC) are both ~15-20 km away.
        assert r["station"] in ("KGTU", "KEDC")
        assert 12 < r["station_km"] < 22
        assert {s for s, _ in r["stations"][:2]} == {"KGTU", "KEDC"}
        assert r["stations"][0][1] <= r["stations"][1][1]

    def test_bare_word_is_not_a_substring_match(self):
        r = resolver.resolve("more")                        # was Skidmore, TX (substring)
        assert r is None or r["name"].upper().startswith("MORE")   # a prefix match (Moreno Valley) is fine
        assert resolver.resolve("san marc")["name"] == "San Marcos, TX"
        assert resolver.resolve("georget")["name"].startswith("Georgetown")

    def test_station_query_keeps_polygon_zone(self):
        r = resolver.resolve("KAUS")
        assert r["zones"][0] == "TXZ192"
        assert r["station_km"] == 0.0

    def test_station_id_with_digits(self):
        # 140 stations in the table carry digits (K1A6, KC09); a bare number is not one.
        assert resolver.resolve("K1A6")["station"] == "K1A6"
        assert resolver.resolve("kc09")["station"] == "KC09"
        assert resolver.resolve("1234") is None
        assert resolver.resolve("kyle")["station"] != "KYLE"


# -- Services -----------------------------------------------------------------


def _store_with(*records):
    store = WeatherStore()
    store.ingest(list(records))
    return store


def _metar_product(icao: str, line: str, age_min: int = 10, seq: int = 1) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    return {
        "filename": f"A_SAUS70KWBC{ts:%d%H%M}_C_KWIN_{ts:%Y%m%d%H%M%S}_{seq:06d}-2-SAHOURLY.TXT",
        "raw_text": f"SAUS70 KWBC {ts:%d%H%M}\r\r\nMETAR\r\r\n{line}=\r\r\n",
    }


class TestObservation:
    def test_skips_silent_nearest_station(self):
        # Only the second-nearest station has a METAR; it must be chosen.
        loc = resolver.resolve("Round Rock, TX")
        second = loc["stations"][1][0]
        store = _store_with(_metar_product(second, f"{second} 131855Z 18005G16KT 10SM FEW050 36/21 A2999"))
        ob = services.observation_for(store, loc)
        assert ob is not None and ob.station == second
        assert ob.temp_f == 97 and ob.wind_gust_mph == 18 and ob.obs_utc_min == 18 * 60 + 55

    def test_skips_stale_station(self):
        loc = resolver.resolve("Round Rock, TX")
        first, second = loc["stations"][0][0], loc["stations"][1][0]
        store = _store_with(
            _metar_product(first, f"{first} 131355Z 16010KT 10SM CLR 30/20 A3001", age_min=300, seq=1),
            _metar_product(second, f"{second} 131855Z 18005KT 10SM CLR 36/21 A2999", age_min=5, seq=2),
        )
        ob = services.observation_for(store, loc)
        assert ob.station == second

    def test_text_and_bytes_agree(self):
        loc = resolver.resolve("Round Rock, TX")
        st, km = loc["stations"][0]
        store = _store_with(_metar_product(st, f"{st} 131855Z 16010G15KT 10SM BKN055 35/21 A3001"))
        ob = services.observation_for(store, loc)
        text = render_text.observation(loc, ob)
        from meshcore_weather.protocol.meshwx import unpack_observation
        wire = unpack_observation(ob.to_bytes())
        assert "95F" in text and wire["temp_f"] == 95
        assert f"{st} {km:.0f}km" in text and wire["location"]["station"] == st
        assert wire["wind_gust_mph"] == 17 and "g17" in text
        assert len(text) <= render_text.MAX_DM


class TestForecast:
    @pytest.fixture
    def store(self):
        raw = (FIXTURES / "PFMEWX_20260913_1851Z.txt").read_bytes().decode("utf-8")
        return _store_with({
            "filename": fresh("A_FOUS54KEWX131851_C_KWIN_20260913185129_315409-3-PFMEWXTX.TXT"),
            "raw_text": raw,
        })

    def test_nearest_point_by_distance_not_zone(self, store):
        # Fixture holds the Austin (TXZ192) and Hondo (TXZ204) points. Kyle is
        # in Hays (TXZ191), which has no point of its own; it must still get
        # a forecast from the nearest point rather than nothing.
        fc = services.forecast_for(store, resolver.resolve("Kyle TX"))
        assert fc is not None
        assert fc.point_zone == "TXZ192"
        assert 25 < fc.distance_km < 45
        assert len(fc.periods) == 7

    def test_far_place_gets_no_forecast(self, store):
        assert services.forecast_for(store, resolver.resolve("Paris TX")) is None

    def test_text_and_bytes_agree(self, store):
        loc = resolver.resolve("Austin TX")
        fc = services.forecast_for(store, loc)
        text = render_text.forecast(loc, fc)
        from meshcore_weather.protocol.meshwx import unpack_forecast
        wire = unpack_forecast(fc.to_bytes())
        p0 = wire["periods"][0]
        assert f"{p0['high_f']}/{p0['low_f']}" in text
        assert len(wire["periods"]) == 7
        assert len(text) <= render_text.MAX_DM


class TestWarnings:
    def test_zone_match_and_text(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        from tests.test_warnings import _npw_two_events
        store = _store_with({
            "filename": f"A_WWUS74KEWX{now:%d%H%M}_C_KWIN_{now:%Y%m%d%H%M%S}_310175-1-NPWEWXTX.TXT",
            "raw_text": _npw_two_events(now),
        })
        loc = resolver.resolve("Round Rock, TX")      # TXZ173 is in the NPW
        ws = services.warnings_for(store, loc)
        assert [w["vtec_etn"] for w in ws] == [10, 11]  # in-effect first, then upcoming
        text = render_text.warnings(loc, ws)
        assert text.startswith("2 active, Round Rock, TX: Heat Adv til ")
        assert len(text) <= render_text.MAX_DM
        assert services.warnings_for(store, resolver.resolve("Paris TX")) == []


def test_observation_text_leaves_out_groups_the_metar_did_not_carry():
    resolver.load()
    loc = resolver.resolve("Round Rock, TX")
    st, _km = loc["stations"][0]
    store = _store_with(_metar_product(st, f"{st} 131855Z VRB04KT 31/ FEW250"))
    ob = services.observation_for(store, loc)
    conditions = render_text.observation(loc, ob).split("): ", 1)[1]
    assert conditions == "88F VRB5 few"
    assert ob.to_bytes()                                    # the v4 packer still fills its old defaults
