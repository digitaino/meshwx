"""Tests for the MeshWX v5 wire codec (docs/MeshWX_v5_Spec.md)."""

from __future__ import annotations

import json
import os

import pytest

from meshcore_weather.protocol import v5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTORS_PATH = os.path.join(ROOT, "docs", "meshwx_v5_vectors.json")
INDEX_PATH = os.path.join(
    ROOT, "meshcore_weather", "client_data", "index.json"
)
PROTOCOL_PATH = os.path.join(
    ROOT, "meshcore_weather", "client_data", "protocol.json"
)

BOT = 0x4C7A
NOW = 29823900  # 2026-09-15 01:00 UTC


# ---------------------------------------------------------------------------
# Re-encoder: rebuild the wire bytes from a decoded dict.  Every round-trip
# test and the vector test go through this, so it doubles as a worked example
# of the decode -> encode mapping an app would implement.
# ---------------------------------------------------------------------------


def reencode(d: dict) -> bytes:
    name = d["name"]
    seq, bot = d["seq"], d["bot"]
    if name == "warning":
        areas = (
            [(a["state"], a["county"], a["start"], a["run"]) for a in d["areas"]]
            if d.get("areas")
            else None
        )
        polygon = (
            [(lat, lon) for lat, lon in d["polygon"]] if d.get("polygon") else None
        )
        return v5.encode_warning(
            seq,
            bot,
            event=d["event"],
            office=d["office"],
            etn=d["etn"],
            expires_min=d["expires_min"],
            tornado=d["tornado"],
            flood_source=d["flood_source"],
            flood_damage=d["flood_damage"],
            hail_qin=d["hail_qin"],
            wind_mph=d["wind_mph"],
            polygon=polygon,
            areas=areas,
            update=d["update"],
        )
    if name == "cancel":
        return v5.encode_cancel(
            seq,
            bot,
            event=d["event"],
            office=d["office"],
            etn=d["etn"],
            reason=d["reason"],
        )
    if name == "digest":
        return v5.encode_digest(
            seq,
            bot,
            now_min=d["now_min"],
            feed_health=d["feed_health"],
            entries=[
                (e["event"], e["office"], e["etn"], e["expires_min"])
                for e in d["entries"]
            ],
        )
    if name == "observations":
        return v5.encode_obs(
            seq, bot, ts_min=d["ts_min"], stations=d["stations"]
        )
    if name == "forecast":
        return v5.encode_forecast(
            seq,
            bot,
            point=d["point"],
            issued_min=d["issued_min"],
            first_period=d["first_period"],
            periods=d["periods"],
        )
    if name == "text":
        return v5.encode_text(
            seq,
            bot,
            subject=d["subject"],
            group=d["group"],
            idx=d["idx"],
            total=d["total"],
            text=d["text"],
        )
    if name == "not_available":
        return v5.encode_not_available(
            seq, bot, request=d["request"], reason=d["reason"]
        )
    if name == "coverage":
        return v5.encode_coverage(
            seq,
            bot,
            lat=d["lat"],
            lon=d["lon"],
            radius_km=d["radius_km"],
            stations=d["stations"],
            offices=d["offices"],
            areas=[
                (a["state"], a["county"], a["start"], a["run"]) for a in d["areas"]
            ],
            zones_cut=d["zones_cut"],
            offices_cut=d["offices_cut"],
        )
    raise AssertionError(f"no re-encoder for {name!r}")


def roundtrip(data: bytes) -> dict:
    decoded = v5.decode(data)
    assert reencode(decoded) == data
    assert len(data) <= v5.MAX_DATA
    return decoded


# ---------------------------------------------------------------------------
# Constants and header
# ---------------------------------------------------------------------------


def test_transport_constants():
    assert v5.DATA_TYPE == 0xFF10
    assert v5.MAX_DATA == 165
    assert (
        v5.TYPE_WARNING,
        v5.TYPE_CANCEL,
        v5.TYPE_DIGEST,
        v5.TYPE_OBS,
        v5.TYPE_FORECAST,
        v5.TYPE_TEXT,
        v5.TYPE_NOT_AVAILABLE,
        v5.TYPE_COVERAGE,
    ) == (1, 2, 3, 4, 5, 6, 7, 8)
    assert v5.SUBJECT_WARNING == 0 and v5.SUBJECT_GENERAL == 8
    assert v5.REASON_NO_DATA == 0 and v5.REASON_RATE_LIMITED == 4
    assert v5.TAG_TORNADO_OBSERVED == 3
    assert v5.FLOOD_SOURCE_OBSERVED == 3
    assert v5.FLOOD_DAMAGE_CATASTROPHIC == 2


def test_header_layout():
    # seq 0x11, bot 0x4C7A little-endian, type 1 flags 0 -> 0x10.
    assert v5.encode_header(0x11, 0x4C7A, v5.TYPE_WARNING) == bytes.fromhex(
        "117a4c10"
    )
    assert v5.encode_header(0, 1, v5.TYPE_CANCEL, 2) == bytes.fromhex("00010022")


@pytest.mark.parametrize(
    "seq,bot",
    [(-1, 0), (256, 0), (0, -1), (0, 0x10000)],
)
def test_header_range_checks(seq, bot):
    with pytest.raises(ValueError):
        v5.encode_header(seq, bot, v5.TYPE_DIGEST)


def test_unknown_type_decodes_as_header_only():
    data = v5.encode_header(9, BOT, 11, 3) + b"\x01\x02"
    out = v5.decode(data)
    assert out == {
        "seq": 9,
        "bot": BOT,
        "type": 11,
        "name": "unknown",
        "flags": 3,
    }


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01\x02",
        bytes.fromhex("117a4c1003232a00"),          # warning cut short
        bytes.fromhex("127a4c20030d"),              # cancel cut short
        bytes.fromhex("147a4c30c913c70107"),        # digest cut short
        bytes.fromhex("157a4c40c913c7010301"),      # obs missing stations
        bytes.fromhex("167a4c5066000000000101"),    # forecast cut short
        bytes.fromhex("177a4c60000101"),            # text header cut short
        bytes.fromhex("187a4c7077"),                # not-available cut short
    ],
)
def test_truncated_input_raises(data):
    with pytest.raises(ValueError):
        v5.decode(data)


# ---------------------------------------------------------------------------
# Warning
# ---------------------------------------------------------------------------


def test_warning_roundtrip_polygon_and_areas():
    data = v5.encode_warning(
        17,
        BOT,
        event=3,
        office=35,
        etn=42,
        expires_min=NOW + 45,
        tornado=v5.TAG_TORNADO_RADAR_INDICATED,
        flood_source=v5.FLOOD_SOURCE_RADAR,
        flood_damage=v5.FLOOD_DAMAGE_CONSIDERABLE,
        hail_qin=4,
        wind_mph=60,
        polygon=[(30.52, -97.98), (30.61, -97.62), (30.38, -97.41)],
        areas=[(42, True, 453, 1), (42, True, 209, 3)],
        update=True,
    )
    out = roundtrip(data)
    assert out["name"] == "warning"
    assert out["flags"] == v5.FLAG_WARNING_UPDATE and out["update"] is True
    assert out["tornado"] == 2
    assert out["flood_source"] == 1
    assert out["flood_damage"] == 1
    assert out["hail_qin"] == 4 and out["wind_mph"] == 60
    assert out["polygon"] == [[30.52, -97.98], [30.61, -97.62], [30.38, -97.41]]
    assert out["areas"] == [
        {"state": 42, "county": True, "start": 453, "run": 1},
        {"state": 42, "county": True, "start": 209, "run": 3},
    ]


def test_warning_zones_only_has_no_polygon():
    data = v5.encode_warning(
        1, BOT, event=24, office=35, etn=7, expires_min=NOW + 1080,
        areas=[(42, False, 191, 4)],
    )
    out = roundtrip(data)
    assert out["polygon"] is None
    assert out["areas"] == [
        {"state": 42, "county": False, "start": 191, "run": 4}
    ]
    assert len(data) == 15 + 1 + 4


def test_warning_bare_is_fifteen_bytes():
    data = v5.encode_warning(
        1, BOT, event=3, office=35, etn=1, expires_min=NOW
    )
    assert len(data) == 15
    out = roundtrip(data)
    assert out["polygon"] is None and out["areas"] is None


def test_warning_polygon_vertex_count_limits():
    ring = [(30.0 + i * 0.01, -97.0) for i in range(31)]
    with pytest.raises(ValueError, match="3..30 vertices"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW, polygon=ring
        )
    with pytest.raises(ValueError, match="3..30 vertices"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW,
            polygon=[(30.0, -97.0), (30.1, -97.1)],
        )


def test_warning_polygon_delta_overflow():
    # 0.001 deg in an i16 reaches +/-32.767 deg; 40 deg of latitude does not.
    with pytest.raises(ValueError, match="does not fit in i16"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW,
            polygon=[(30.0, -97.0), (70.0, -97.0), (30.0, -97.1)],
        )
    with pytest.raises(ValueError, match="does not fit in i16"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW,
            polygon=[(30.0, -97.0), (30.1, -140.0), (30.0, -97.1)],
        )
    # Just inside the limit still encodes.
    ok = v5.encode_warning(
        1, BOT, event=3, office=1, etn=1, expires_min=NOW,
        polygon=[(30.0, -97.0), (30.0, -129.767), (30.1, -97.0)],
    )
    assert v5.decode(ok)["polygon"][1] == [30.0, -129.767]


def test_warning_over_max_data_raises():
    ring = [(30.0 + i * 0.01, -97.0 - i * 0.01) for i in range(30)]
    areas = [(42, False, 100 + i, 1) for i in range(30)]
    with pytest.raises(ValueError, match="over the 165-byte limit"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW,
            polygon=ring, areas=areas,
        )


def test_warning_area_run_limits():
    with pytest.raises(ValueError, match="1..30 runs"):
        v5.encode_warning(
            1, BOT, event=3, office=1, etn=1, expires_min=NOW,
            areas=[(42, False, 100 + i, 1) for i in range(31)],
        )


def test_warning_tag_range_checks():
    for kwargs in ({"tornado": 4}, {"flood_source": 4}, {"flood_damage": 4}):
        with pytest.raises(ValueError):
            v5.encode_warning(
                1, BOT, event=3, office=1, etn=1, expires_min=NOW, **kwargs
            )


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_roundtrip():
    data = v5.encode_cancel(
        19, BOT, event=3, office=35, etn=42, reason=v5.CANCEL_UPGRADED
    )
    assert len(data) == 8
    out = roundtrip(data)
    assert out["name"] == "cancel"
    assert (out["event"], out["office"], out["etn"]) == (3, 35, 42)
    assert out["reason"] == v5.CANCEL_UPGRADED == out["flags"]


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def test_digest_roundtrip_and_relative_expiry():
    data = v5.encode_digest(
        20, BOT, now_min=NOW, feed_health=7,
        entries=[(3, 35, 42, NOW + 45), (24, 35, 7, NOW + 1080)],
    )
    assert len(data) == 10 + 6 * 2
    out = roundtrip(data)
    assert out["now_min"] == NOW and out["feed_health"] == 7
    assert [e["expires_rel"] for e in out["entries"]] == [45, 1080]
    assert [e["expires_min"] for e in out["entries"]] == [NOW + 45, NOW + 1080]


def test_digest_clamps_expiry():
    data = v5.encode_digest(
        1, BOT, now_min=NOW, feed_health=255,
        entries=[(3, 1, 1, NOW - 500), (3, 1, 2, NOW + 999999)],
    )
    entries = v5.decode(data)["entries"]
    assert entries[0]["expires_rel"] == 0        # already past -> 0
    assert entries[1]["expires_rel"] == 0xFFFF   # clamped to u16


def test_digest_empty_and_limit():
    out = roundtrip(v5.encode_digest(1, BOT, now_min=NOW, feed_health=0, entries=[]))
    assert out["entries"] == []
    big = [(3, 1, i, NOW + 10) for i in range(26)]
    with pytest.raises(ValueError, match="at most 25 entries"):
        v5.encode_digest(1, BOT, now_min=NOW, feed_health=0, entries=big)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def _station(**over):
    base = dict(
        station=202, temp_f=88, dewpoint_f=72, wind_dir_deg=160, sky=3,
        wind_mph=12, gust_mph=21, visibility_mi=10, pressure_inhg=29.92,
        humidity_pct=59, feels_delta_f=7,
    )
    base.update(over)
    return base


def test_obs_roundtrip():
    data = v5.encode_obs(21, BOT, ts_min=NOW - 7, stations=[_station()])
    assert len(data) == 9 + 11
    out = roundtrip(data)
    assert out["name"] == "observations"
    assert out["ts_min"] == NOW - 7
    s = out["stations"][0]
    assert s["station"] == 202
    assert s["temp_f"] == 88 and s["dewpoint_f"] == 72
    assert s["sky"] == 3
    assert s["wind_dir_deg"] == 157.5 and s["wind_dir"] == "SSE"
    assert s["pressure_inhg"] == 29.92
    assert s["feels_delta_f"] == 7


def test_obs_sentinels():
    data = v5.encode_obs(
        1, BOT, ts_min=NOW,
        stations=[
            _station(
                temp_f=None, dewpoint_f=None, wind_dir_deg=None, sky=None,
                wind_mph=None, gust_mph=None, visibility_mi=None,
                pressure_inhg=None, humidity_pct=None, feels_delta_f=None,
            )
        ],
    )
    # temp/dewpoint -128, dir 0 + sky 15, wind 255, gust 0, vis 255,
    # pressure 255, humidity 255, feels 0.
    assert data[9 + 2:9 + 11] == bytes([0x80, 0x80, 0x0F, 255, 0, 255, 255, 255, 0])
    s = roundtrip(data)["stations"][0]
    assert s["temp_f"] is None and s["dewpoint_f"] is None
    assert s["wind_mph"] is None and s["visibility_mi"] is None
    assert s["pressure_inhg"] is None and s["humidity_pct"] is None
    assert s["sky"] == 15
    assert s["gust_mph"] == 0 and s["feels_delta_f"] == 0


def test_obs_calm_is_direction_zero():
    data = v5.encode_obs(
        1, BOT, ts_min=NOW,
        stations=[_station(wind_dir_deg=None, wind_mph=0, gust_mph=0)],
    )
    s = v5.decode(data)["stations"][0]
    assert s["wind_dir_deg"] == 0.0 and s["wind_mph"] == 0


def test_obs_count_limits():
    with pytest.raises(ValueError, match="1..14 stations"):
        v5.encode_obs(1, BOT, ts_min=NOW, stations=[])
    with pytest.raises(ValueError, match="1..14 stations"):
        v5.encode_obs(1, BOT, ts_min=NOW, stations=[_station()] * 15)
    full = v5.encode_obs(1, BOT, ts_min=NOW, stations=[_station()] * 14)
    assert len(full) == 9 + 11 * 14 == 163 <= v5.MAX_DATA


def test_obs_pressure_out_of_range():
    with pytest.raises(ValueError, match="out of encodable range"):
        v5.encode_obs(1, BOT, ts_min=NOW, stations=[_station(pressure_inhg=28.5)])


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------


def _period(**over):
    base = dict(
        high_f=93, low_f=None, pop_pct=40, sky=3, thunder=True, wintry=False,
        windy=False, fog=False, wind_dir_deg=180, wind_mph=10,
    )
    base.update(over)
    return base


def test_forecast_roundtrip():
    periods = [
        _period(high_f=None, low_f=73, thunder=False, fog=True),
        _period(),
        _period(high_f=None, low_f=72, wintry=True, windy=True),
    ]
    data = v5.encode_forecast(
        22, BOT, point=102, issued_min=NOW - 120, first_period=1, periods=periods
    )
    assert len(data) == 12 + 5 * 3
    out = roundtrip(data)
    assert out["name"] == "forecast"
    assert out["point"] == 102 and out["first_period"] == 1
    assert out["issued_min"] == NOW - 120
    assert out["periods"][0]["high_f"] is None
    assert out["periods"][0]["low_f"] == 73
    assert out["periods"][0]["fog"] is True
    assert out["periods"][1]["low_f"] is None
    assert out["periods"][1]["thunder"] is True
    assert out["periods"][2]["wintry"] is True and out["periods"][2]["windy"] is True
    assert out["periods"][1]["wind_dir_deg"] == 180.0
    assert out["periods"][1]["wind_dir"] == "S"
    assert out["periods"][1]["wind_mph"] == 10


def test_forecast_sentinels_and_speed_quantisation():
    data = v5.encode_forecast(
        1, BOT, point=0xFFFF, issued_min=NOW, first_period=0,
        periods=[
            _period(high_f=None, low_f=None, pop_pct=None, sky=None,
                    wind_dir_deg=None, wind_mph=None),
            _period(wind_mph=200),
        ],
    )
    assert data[12] == 127 and data[13] == 127  # high/low not given
    assert data[14] == 255                      # pop not given
    out = v5.decode(data)
    assert out["point"] == 0xFFFF
    p0, p1 = out["periods"]
    assert p0["high_f"] is None and p0["low_f"] is None and p0["pop_pct"] is None
    assert p0["sky"] == 15
    assert p0["wind_dir_deg"] == 0.0 and p0["wind_mph"] == 0
    assert p1["wind_mph"] == 75  # nibble saturates at 15 -> 75 mph


def test_forecast_count_limits():
    with pytest.raises(ValueError, match="1..14 periods"):
        v5.encode_forecast(
            1, BOT, point=1, issued_min=NOW, first_period=0, periods=[]
        )
    with pytest.raises(ValueError, match="1..14 periods"):
        v5.encode_forecast(
            1, BOT, point=1, issued_min=NOW, first_period=0,
            periods=[_period()] * 15,
        )


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_text_roundtrip():
    data = v5.encode_text(
        23, BOT, subject=v5.SUBJECT_AFD, group=23, idx=0, total=2, text="hello"
    )
    assert len(data) == 8 + 5
    out = roundtrip(data)
    assert out["name"] == "text"
    assert out["subject"] == v5.SUBJECT_AFD
    assert (out["group"], out["idx"], out["total"]) == (23, 0, 2)
    assert out["text"] == "hello"


def test_text_chunk_limit():
    with pytest.raises(ValueError, match="over the 157-byte limit"):
        v5.encode_text(
            1, BOT, subject=0, group=1, idx=0, total=1, text="x" * 158
        )
    full = v5.encode_text(
        1, BOT, subject=0, group=1, idx=0, total=1, text="x" * 157
    )
    assert len(full) == v5.MAX_DATA


def test_text_chunks_split_and_number():
    body = "A" * 400
    chunks = v5.text_chunks(23, BOT, subject=v5.SUBJECT_WARNING, text=body)
    assert len(chunks) == 3
    decoded = [roundtrip(c) for c in chunks]
    assert [d["seq"] for d in decoded] == [23, 24, 25]
    assert {d["group"] for d in decoded} == {23}
    assert [d["idx"] for d in decoded] == [0, 1, 2]
    assert {d["total"] for d in decoded} == {3}
    assert "".join(d["text"] for d in decoded) == body
    assert [len(c) for c in chunks] == [165, 165, 8 + 400 - 314]


def test_text_chunks_never_split_a_code_point():
    # Three-byte code points: 157 is not a multiple of 3, so a naive split
    # would cut one in half.
    body = "☃" * 200
    chunks = v5.text_chunks(0, BOT, subject=v5.SUBJECT_GENERAL, text=body)
    rebuilt = "".join(v5.decode(c)["text"] for c in chunks)
    assert rebuilt == body
    for c in chunks:
        assert (len(c) - 8) % 3 == 0


def test_text_chunks_seq_wraps():
    chunks = v5.text_chunks(254, BOT, subject=0, text="B" * 400)
    assert [v5.decode(c)["seq"] for c in chunks] == [254, 255, 0]
    assert {v5.decode(c)["group"] for c in chunks} == {254}


def test_text_chunks_too_long():
    with pytest.raises(ValueError, match="over the 8-chunk limit"):
        v5.text_chunks(0, BOT, subject=0, text="C" * (157 * 8 + 1))


def test_text_chunks_empty_text():
    chunks = v5.text_chunks(5, BOT, subject=0, text="")
    assert len(chunks) == 1
    assert v5.decode(chunks[0])["text"] == ""


# ---------------------------------------------------------------------------
# Not available
# ---------------------------------------------------------------------------


def test_not_available_roundtrip():
    data = v5.encode_not_available(
        25, BOT, request="f round rock zz", reason=v5.REASON_UNKNOWN_LOCATION
    )
    assert len(data) == 6
    out = roundtrip(data)
    assert out["name"] == "not_available"
    assert out["request"] == "f" and out["request_code"] == ord("f")
    assert out["reason"] == v5.REASON_UNKNOWN_LOCATION


def test_not_available_strips_the_request_prefix():
    data = v5.encode_not_available(1, BOT, request=">metar KAUS", reason=0)
    assert v5.decode(data)["request"] == "m"
    with pytest.raises(ValueError):
        v5.encode_not_available(1, BOT, request="", reason=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "deg,nib,compass",
    [
        (0, 0, "N"), (90, 4, "E"), (180, 8, "S"), (270, 12, "W"),
        (350, 0, "N"), (360, 0, "N"), (292.5, 13, "WNW"), (None, 0, "N"),
    ],
)
def test_wind_dir_nibble(deg, nib, compass):
    assert v5.wind_dir_nibble(deg) == nib
    assert v5.nibble_to_compass(nib) == compass


def test_areas_from_ugcs_merges_runs():
    states = ["TX", "OK"]
    assert v5.areas_from_ugcs(
        ["TXZ191", "TXZ192", "TXZ193", "TXZ194", "TXZ200"], states
    ) == [(0, False, 191, 4), (0, False, 200, 1)]


def test_areas_from_ugcs_separates_kind_and_state():
    states = ["TX", "OK"]
    out = v5.areas_from_ugcs(
        ["TXC191", "TXZ191", "TXZ192", "OKZ001", "OKZ002"], states
    )
    # Sorted by (state index, county flag, number): zones before counties.
    assert out == [
        (0, False, 191, 2),
        (0, True, 191, 1),
        (1, False, 1, 2),
    ]


def test_areas_from_ugcs_skips_unknown_states_and_junk():
    states = ["TX"]
    assert v5.areas_from_ugcs(
        ["TXC453", "OKZ001", "ZZZ", "TX453", "txc454"], states
    ) == [(0, True, 453, 2)]


def test_areas_from_ugcs_dedupes():
    assert v5.areas_from_ugcs(["TXZ191", "TXZ191", "TXZ192"], ["TX"]) == [
        (0, False, 191, 2)
    ]


def test_areas_from_ugcs_splits_runs_over_255():
    ugcs = [f"TXZ{n:03d}" for n in range(1, 260)]
    runs = v5.areas_from_ugcs(ugcs, ["TX"])
    assert runs == [(0, False, 1, 255), (0, False, 256, 4)]


# ---------------------------------------------------------------------------
# Bundle files
# ---------------------------------------------------------------------------


def test_index_json_matches_the_source_tables():
    base = os.path.dirname(INDEX_PATH)
    with open(INDEX_PATH, encoding="utf-8") as fh:
        index = json.load(fh)
    with open(os.path.join(base, "wfos.json"), encoding="utf-8") as fh:
        wfos = json.load(fh)
    with open(os.path.join(base, "stations.json"), encoding="utf-8") as fh:
        stations = json.load(fh)
    with open(os.path.join(base, "state_index.json"), encoding="utf-8") as fh:
        state_index = json.load(fh)
    assert index["version"] == 2
    # Sorted WFOs, then the national centres appended: no office index moves.
    assert index["offices"] == sorted(set(wfos) - {"NHC", "WNS"}) + ["NHC", "WNS"]
    assert list(wfos) == index["offices"]
    assert (index["offices"].index("NHC"), index["offices"].index("WNS")) == (125, 126)
    assert index["stations"] == sorted(stations)
    assert index["states"] == state_index["states"]  # same list, same order
    assert index["offices"].index("EWX") == 35
    assert index["states"].index("TX") == 42
    assert len(index["states"]) <= 128  # the state byte only has 7 bits


def test_protocol_json_v5_block():
    with open(PROTOCOL_PATH, encoding="utf-8") as fh:
        proto = json.load(fh)
    assert proto["version"] == 9
    assert proto["index_file"] == "index.json"
    # Legacy keys other code still reads are untouched.
    for key in ("messages", "events", "event_names", "sky_codes", "data_types"):
        assert key in proto
    block = proto["v5"]
    assert block["data_type"] == v5.DATA_TYPE == 65296
    assert block["max_data"] == v5.MAX_DATA
    assert block["types"] == {
        "warning": 1, "cancel": 2, "digest": 3, "observations": 4,
        "forecast": 5, "text": 6, "not_available": 7, "coverage": 8,
    }
    assert block["flags"]["coverage"] == {
        "zones_truncated": v5.FLAG_COVERAGE_ZONES_CUT,
        "offices_truncated": v5.FLAG_COVERAGE_OFFICES_CUT,
    }
    assert block["limits"]["coverage_offices"] == [0, v5.MAX_COVERAGE_OFFICES]
    assert block["limits"]["coverage_runs"] == [0, v5.MAX_COVERAGE_RUNS]
    assert block["record_sizes"]["coverage_fixed"] == 14
    assert block["header"]["bot"]["offset"] == 1
    assert block["not_available_reasons"]["rate_limited"] == v5.REASON_RATE_LIMITED
    assert block["text_subjects"]["general"] == v5.SUBJECT_GENERAL
    assert block["tornado_tags"]["observed"] == v5.TAG_TORNADO_OBSERVED
    assert block["flood_source"]["radar_and_gauge"] == v5.FLOOD_SOURCE_RADAR_AND_GAUGE
    assert block["flood_damage"]["catastrophic"] == v5.FLOOD_DAMAGE_CATASTROPHIC
    assert block["flags"]["warning"]["update"] == v5.FLAG_WARNING_UPDATE
    assert block["notes"]


# ---------------------------------------------------------------------------
# Test vectors
# ---------------------------------------------------------------------------


def _vectors():
    with open(VECTORS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_vectors_cover_every_message_type():
    names = {v5.decode(bytes.fromhex(v["hex"]))["name"] for v in _vectors()}
    assert names == {
        "warning", "cancel", "digest", "observations", "forecast", "text",
        "not_available", "coverage",
    }


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["name"])
def test_vector_decodes_and_reencodes(vector):
    data = bytes.fromhex(vector["hex"])
    assert len(data) <= v5.MAX_DATA
    decoded = v5.decode(data)
    assert decoded == vector["decoded"]
    assert reencode(decoded).hex() == vector["hex"]


def test_warning_vector_byte_by_byte():
    """Hand-check of `severe_thunderstorm_warning_polygon`, offset by offset.

    hex: 11 7a4c 10 | 03 23 2a00 c913c701 83 04 3c |
         06 30a804 a80cf1 | 5a00 6801 | 1aff d200 | 1aff a6ff |
         c4ff a2fe | be00 42ff | 02 | aad10001 | aac50101

      0      11        seq = 17
      1..2   7a 4c     bot = 0x4C7A = 19578 (little-endian)
      3      10        type byte: high nibble 1 = warning, flags 0
      4      03        event = 3 = SV.W
      5      23        office = 35 = EWX in index.json "offices"
      6..7   2a 00     etn = 42
      8..11  c9 13 c7 01  expires = 0x01C713C9 = 29823945 min = 01:45 UTC
      12     83        tags 1000 0011: tornado 2 (radar indicated),
                       flood source 0, flood damage 0, bit1 polygon, bit0 areas
      13     04        hail = 4 quarter inches = 1.00 in
      14     3c        wind = 60 mph
      15     06        polygon vertex count = 6
      16..18 30 a8 04  lat0 i24 = 305200 -> 30.5200
      19..21 a8 0c f1  lon0 i24 = 0xF10CA8 - 2^24 = -979800 -> -97.9800
      22..25 5a00 6801 dlat +90, dlon +360 (0.001 deg) -> 30.610, -97.620
      26..29 1aff d200 dlat -230, dlon +210            -> 30.380, -97.410
      30..33 1aff a6ff dlat -230, dlon  -90            -> 30.150, -97.500
      34..37 c4ff a2fe dlat  -60, dlon -350            -> 30.090, -97.850
      38..41 be00 42ff dlat +190, dlon -190            -> 30.280, -98.040
      42     02        area run count = 2
      43..46 aa d100 01  state 0xAA = county flag + index 42 (TX),
                         start = 209 (Hays), run = 1
      47..50 aa c501 01  state TX county, start = 453 (Travis), run = 1

    Total 15 + 27 + 9 = 51 bytes, matching spec section 3.
    """
    vector = next(
        v for v in _vectors() if v["name"] == "severe_thunderstorm_warning_polygon"
    )
    data = bytes.fromhex(vector["hex"])
    assert len(data) == 51
    assert data[:4] == bytes.fromhex("117a4c10")
    assert data[4] == 3 and data[5] == 35
    assert int.from_bytes(data[6:8], "little") == 42
    assert int.from_bytes(data[8:12], "little") == NOW + 45
    assert data[12] == 0x83
    assert data[13] == 4 and data[14] == 60
    assert data[15] == 6
    assert int.from_bytes(data[16:19], "little") == 305200
    assert int.from_bytes(data[19:22], "little") - (1 << 24) == -979800
    assert data[42] == 2
    assert data[43] == 0x80 | 42
    assert int.from_bytes(data[44:46], "little") == 209
    assert data[46] == 1
    assert int.from_bytes(data[48:50], "little") == 453


def test_vector_sizes_are_within_budget():
    sizes = {v["name"]: len(v["hex"]) // 2 for v in _vectors()}
    assert sizes["severe_thunderstorm_warning_polygon"] == 51
    assert sizes["winter_storm_warning_zones"] == 24
    assert sizes["cancel_expired_early"] == 8
    assert sizes["not_available_unknown_location"] == 6
    # WX-AUS's real coverage: 14 fixed + 4 offices + 1 + 5 runs of 4.
    assert sizes["coverage_wx_aus"] == 39
    assert all(n <= v5.MAX_DATA for n in sizes.values())
