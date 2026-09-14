"""Space weather: SWPC 3-Day Forecast + Daily Indices + alerts -> one object, 27 bytes,
one line. Fixtures are real products received over GOES-19 on 2026-09-14."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from meshcore_weather.core import space_weather as sw
from meshcore_weather.parser.weather import WeatherStore
from tests.test_core import fresh

FIX = Path(__file__).parent / "fixtures" / "swpc"


def _text(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def _store() -> WeatherStore:
    store = WeatherStore()
    store.ingest([
        {"filename": fresh("A_FXXX10KWNP140031_C_KWIN_20260914003153_332550-2-DAYTDFUS.TXT"),
         "raw_text": _text("DAYTDFUS_20260914_0031Z.txt")},
        {"filename": fresh("A_AXXX81KWNP140017_C_KWIN_20260914001747_331851-2-DAYINDUS.TXT"),
         "raw_text": _text("DAYINDUS_20260914_0017Z.txt")},
    ])
    return store


def test_parse_3day_forecast():
    f = sw.parse_3day_forecast(_text("DAYTDFUS_20260914_0031Z.txt"))
    assert f["issued_at"] == datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)
    assert f["kp_max_24h"] == 3.0
    assert f["kp_forecast"] == [3.67, 3.67, 4.67]
    assert f["g_forecast"] == [0, 0, 1]          # 4.67 rounds to 5 -> G1
    assert f["s1_prob"] == [1, 1, 1]
    assert f["r12_prob"] == [10, 10, 10]
    assert f["r3_prob"] == [1, 1, 1]


def test_parse_daily_indices():
    i = sw.parse_daily_indices(_text("DAYINDUS_20260914_0017Z.txt"))
    assert i["ssn"] == 77 and i["sfi"] == 114 and i["sfi_90d"] == 126
    assert i["xray_bkgd"] == "B3.0"             # 2.99e-07 W/m^2


def test_service_bytes_and_text_agree():
    obj = sw.space_weather_for(_store())
    assert obj is not None
    wire = obj.to_bytes()
    assert len(wire) == sw.SpaceWeather.SIZE == 27
    back = sw.SpaceWeather.from_bytes(wire)
    assert back.issued_at == obj.issued_at
    assert abs(back.kp_max_24h - 3.0) < 0.2
    assert [round(k, 1) for k in back.kp_forecast] == [3.7, 3.7, 4.7]
    assert back.g_forecast == [0, 0, 1]
    assert back.r12_prob == [10, 10, 10] and back.s1_prob == [1, 1, 1]
    assert back.sfi == 114 and back.ssn == 77 and back.xray_bkgd == "B3.0"
    text = sw.render(obj)
    assert text.startswith("Kp now 3.0, next 3d 3.7/3.7/4.7 (G1 ")
    assert "SFI 114 SSN 77 xray B3.0" in text
    assert "R1-2 10%" in text
    assert len(text) <= 160


def test_missing_products():
    assert sw.space_weather_for(WeatherStore()) is None
    assert sw.render(None).startswith("No space weather")


def test_indices_only_still_answers():
    store = WeatherStore()
    store.ingest([{"filename": fresh("A_AXXX81KWNP140017_C_KWIN_20260914001747_331851-2-DAYINDUS.TXT"),
                   "raw_text": _text("DAYINDUS_20260914_0017Z.txt")}])
    obj = sw.space_weather_for(store)
    assert obj is not None and obj.kp_forecast == [] and obj.sfi == 114
    text = sw.render(obj)
    assert text.startswith("Kp forecast not received yet") and "SFI 114" in text
    assert len(obj.to_bytes()) == 27


# -- SWPC alert envelope -----------------------------------------------------------
#
# ALTEF3 is real (received 2026-09-14 11:06Z). The others follow SWPC's
# published message layout; no storm has happened on the dish yet.

_ISSUE = "2026 Sep 15 0312 UTC"


def _swpc(code: str, headline: str, *fields: str, issue: str = _ISSUE) -> str:
    body = "\r\r\n".join([
        f"WOXX30 KWNP 150312", code, "", f"Space Weather Message Code: {code}",
        "Serial Number: 999", f"Issue Time: {issue}", "", headline, *fields, "",
        "# Issued by the US Dept. of Commerce, NOAA, Space Weather Prediction Center", ""])
    return body


def _alert_store(*items) -> WeatherStore:
    store = WeatherStore()
    store.ingest([{"filename": fresh(f"A_WOXX30KWNP150312_C_KWIN_20260915031200_{n:06d}-1-{code}US.TXT"), "raw_text": txt}
                  for n, (code, txt) in enumerate(items, 1)])
    return store


NOW = datetime(2026, 9, 15, 3, 30, tzinfo=timezone.utc)


def test_parse_real_electron_alert():
    a = sw.parse_swpc_alert(_text("ALTEF3US_20260914_1106Z.txt"))
    assert a["code"] == "ALTEF3" and a["kind"] == "ALERT" and not a["cancel"]
    assert a["issued_at"] == datetime(2026, 9, 14, 11, 3, tzinfo=timezone.utc)
    assert a["begin_at"] == datetime(2026, 9, 10, 13, 4, tzinfo=timezone.utc)
    assert a["k"] is None and a["scale"] is None
    # An electron alert changes nothing a mesh user sees.
    st = sw.alert_state(_alert_store(("ALTEF3", _text("ALTEF3US_20260914_1106Z.txt"))), now=NOW)
    assert st == {"kp_now": 0, "kp_expected": 0, "xray_alert": 0, "proton_alert": 0, "ids": []}


def test_k_alert_is_live_for_its_synoptic_period():
    k6 = _swpc("ALTK06", "ALERT: Geomagnetic K-index of 6",
               "Threshold Reached: 2026 Sep 15 0310 UTC", "Synoptic Period: 0300-0600 UTC",
               "Active Warning: Yes", "NOAA Scale: G2 - Moderate")
    store = _alert_store(("ALTK06", k6))
    st = sw.alert_state(store, now=NOW)
    assert st["kp_now"] == 6 and st["kp_expected"] == 0
    assert sw.alert_state(store, now=NOW + timedelta(hours=3))["kp_now"] == 0
    obj = sw.space_weather_for(store, now=NOW)   # no daily products at all
    assert obj is not None and obj.kp_forecast == []
    assert sw.render(obj).startswith("!G2 storm now (K6). Kp forecast not received yet")


def test_warning_watch_and_cancel():
    w5 = _swpc("WARK05", "WARNING: Geomagnetic K-index of 5 expected",
               "Valid From: 2026 Sep 15 0300 UTC", "Valid To: 2026 Sep 15 1200 UTC",
               "Warning Condition: Onset", "NOAA Scale: G1 - Minor")
    watch = _swpc("WATA30", "WATCH: Geomagnetic Storm Category G2 Predicted",
                  "Highest Storm Level Predicted by Day:",
                  "Sep 15:  G2 (Moderate)   Sep 16:  G1 (Minor)   Sep 17:  None (Below G1)")
    st = sw.alert_state(_alert_store(("WARK05", w5), ("WATA30", watch)), now=NOW)
    assert st["kp_expected"] == 6            # watch G2 -> K6 beats warning K5
    st = sw.alert_state(_alert_store(("WARK05", w5)), now=NOW)
    assert st["kp_expected"] == 5
    assert sw.alert_state(_alert_store(("WARK05", w5)), now=NOW + timedelta(hours=10))["kp_expected"] == 0
    cancel = _swpc("WARK05", "CANCEL WARNING: Geomagnetic K-index of 5 expected",
                   "Cancel Serial Number: 999", "Original Issue Time: 2026 Sep 15 0312 UTC",
                   issue="2026 Sep 15 0320 UTC")
    st = sw.alert_state(_alert_store(("WARK05", w5), ("WARK05", cancel)), now=NOW)
    assert st["kp_expected"] == 0
    # Watch alone, after its last listed day: nothing expected
    assert sw.alert_state(_alert_store(("WATA30", watch)), now=NOW + timedelta(days=3))["kp_expected"] == 0


def test_xray_and_proton_events():
    x1 = _swpc("SUMX01", "SUMMARY: X-ray Event exceeded X1",
               "Begin Time: 2026 Sep 15 0250 UTC", "Maximum Time: 2026 Sep 15 0305 UTC",
               "End Time: 2026 Sep 15 0320 UTC", "X-ray Class: X1.3", "Location: N15W30",
               "NOAA Scale: R3 - Strong")
    m5 = _swpc("ALTXMF", "ALERT: X-Ray Flux exceeded M5",
               "Threshold Reached: 2026 Sep 15 0301 UTC", "NOAA Scale: R2 - Moderate")
    s1 = _swpc("ALTPX1", "ALERT: Proton Event 10MeV Integral Flux exceeded 10pfu",
               "Begin Time: 2026 Sep 15 0300 UTC", "NOAA Scale: S1 - Minor")
    store = _alert_store(("SUMX01", x1), ("ALTXMF", m5), ("ALTPX1", s1))
    st = sw.alert_state(store, now=NOW)
    assert st["xray_alert"] == 2 and st["proton_alert"] == 2
    assert sw.alert_state(store, now=NOW + timedelta(hours=4))["xray_alert"] == 0
    assert sw.alert_state(store, now=NOW + timedelta(hours=4))["proton_alert"] == 2
    ended = _swpc("SUMPX1", "SUMMARY: Proton Event 10MeV Integral Flux exceeded 10pfu",
                  "Begin Time: 2026 Sep 15 0300 UTC", "Maximum Time: 2026 Sep 15 0500 UTC",
                  "End Time: 2026 Sep 15 0700 UTC", "Maximum 10MeV Flux: 45 pfu",
                  issue="2026 Sep 15 0710 UTC")
    store = _alert_store(("SUMX01", x1), ("ALTPX1", s1), ("SUMPX1", ended))
    assert sw.alert_state(store, now=NOW + timedelta(hours=5))["proton_alert"] == 0


def test_alert_nibbles_round_trip_and_text():
    obj = sw.space_weather_for(_store())
    obj.kp_now, obj.kp_expected, obj.xray_alert, obj.proton_alert = 7, 5, 2, 3
    wire = obj.to_bytes()
    assert len(wire) == 27 and wire[25] == 0x75 and wire[26] == 0x23
    back = sw.SpaceWeather.from_bytes(wire)
    assert (back.kp_now, back.kp_expected, back.xray_alert, back.proton_alert) == (7, 5, 2, 3)
    text = sw.render(obj)
    assert text.startswith("!G3 storm now (K7) !R3 radio blackout !S2+ proton event. Kp now 3.0")
    assert len(text) <= 160
