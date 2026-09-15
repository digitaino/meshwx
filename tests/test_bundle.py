"""The preload bundle the app ships: the wire indexes and the area tables agree."""

import json
from pathlib import Path

from meshcore_weather.protocol import v5

DATA = Path(__file__).resolve().parent.parent / "meshcore_weather" / "client_data"


def _load(name):
    return json.loads((DATA / name).read_text())


def test_counties_table_and_polygons_cover_the_same_codes():
    counties = _load("counties.json")
    geo = _load("counties.geojson")
    codes = {f["properties"]["code"] for f in geo["features"]}
    assert len(counties) >= 3200 and set(counties) == codes
    assert counties["TXC453"]["name"] == "Travis" and counties["TXC453"]["state"] == "TX"
    assert all(len(c) == 6 and c[2] == "C" and c[3:].isdigit() for c in counties)
    assert {f["geometry"]["type"] for f in geo["features"]} <= {"Polygon", "MultiPolygon"}


def test_every_area_state_on_the_wire_is_addressable():
    states = _load("index.json")["states"]
    county_states = {v["state"] for v in _load("counties.json").values()}
    zone_states = {v["state"] for v in _load("zones.json").values() if v.get("state")}
    assert county_states <= set(states) and zone_states <= set(states)
    assert len(states) <= 128                     # the state byte has 7 bits
    # A county run decodes back to a key in counties.json
    (state_idx, county, start, run) = v5.areas_from_ugcs(["TXC453", "TXC454"], states)[0]
    assert county and f"{states[state_idx]}C{start:03d}" == "TXC453" and run == 2


def test_protocol_json_exposes_v5_and_the_index_file():
    p = _load("protocol.json")
    assert p["version"] >= 8 and p["index_file"] == "index.json"
    assert p["v5"]["data_type"] == v5.DATA_TYPE and p["v5"]["types"]["warning"] == v5.TYPE_WARNING
