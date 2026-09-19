"""`>o ICAO` answers with the nearest station that reports (spec 8.2, rev 8).

Field report 2026-09-18: Dayton's nearest station in the app's bundled list
is Wright-Patterson AFB (KFFO), which sends nothing on the feed, so every
`>o KFFO` came back Not available while Dayton International (KDAY, 16.6 km
from it) reported hourly. Chat never had the problem: it walks the stations
nearest first and skips the silent ones.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b
from tests.test_v5_builders import _metar_store


def _index(icao: str) -> int:
    return b.tables.station(icao)


def _app(text: str, store: WeatherStore) -> list[dict]:
    from meshcore_weather.protocol.broadcaster import AppResponder
    radio = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    radio.send_channel_data = AsyncMock(return_value=True)
    asyncio.run(AppResponder(store, radio).handle_request(text, "app"))
    return [v5.decode(c.args[0]) for c in radio.send_channel_data.await_args_list]


def test_a_station_that_reports_answers_for_itself():
    store = _metar_store("KFFO 181256Z 03009KT 7SM OVC006 22/21 A3023",
                         "KDAY 181256Z 03009KT 7SM OVC006 23/21 A3023")
    stations = v5.decode(b.station_obs_message(1, 1, store, "KFFO"))["stations"]
    assert [s["station"] for s in stations] == [_index("KFFO")]


def test_a_silent_station_is_answered_by_the_nearest_that_reports():
    # KDAY is 16.6 km from KFFO, KMGY 30.7 km: the nearer one answers, alone,
    # under its own index.
    store = _metar_store("KDAY 181256Z 03009KT 7SM OVC006 22/21 A3023",
                         "KMGY 181253Z AUTO 9SM OVC007 23/22 A3021")
    stations = v5.decode(b.station_obs_message(1, 1, store, "KFFO"))["stations"]
    assert [s["station"] for s in stations] == [_index("KDAY")]
    assert stations[0]["temp_f"] == 72


def test_nothing_within_40_km_is_nothing():
    # Columbus (KCMH) is about 100 km from Wright-Patterson: too far to stand in.
    store = _metar_store("KCMH 181251Z 03009KT 10SM OVC010 22/20 A3022")
    assert b.station_obs_message(1, 1, store, "KFFO") is None


def test_the_request_answers_with_the_stand_in_and_says_where_it_came_from():
    store = _metar_store("KDAY 181256Z 03009KT 7SM OVC006 22/21 A3023")
    msgs = _app(">o KFFO", store)
    assert [m["name"] for m in msgs] == ["observations"]
    assert [s["station"] for s in msgs[0]["stations"]] == [_index("KDAY")]


def test_a_silent_station_with_nothing_near_is_not_available():
    msgs = _app(">o KFFO", WeatherStore())
    assert [m["name"] for m in msgs] == ["not_available"]
    assert msgs[0]["reason"] == v5.REASON_NO_DATA


def test_an_unknown_station_is_still_an_unknown_location():
    msgs = _app(">o KZZZ", WeatherStore())
    assert [m["name"] for m in msgs] == ["not_available"]
    assert msgs[0]["reason"] == v5.REASON_UNKNOWN_LOCATION
