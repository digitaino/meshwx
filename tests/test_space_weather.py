"""Space weather: SWPC 3-Day Forecast + Daily Indices -> one object, 25 bytes,
one line. Fixtures are real products received over GOES-19 on 2026-09-14."""

from datetime import datetime, timezone
from pathlib import Path

from meshcore_weather.core import space_weather as sw
from meshcore_weather.parser.weather import WeatherStore

FIX = Path(__file__).parent / "fixtures" / "swpc"


def _text(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def _store() -> WeatherStore:
    store = WeatherStore()
    store.ingest([
        {"filename": "A_FXXX10KWNP140031_C_KWIN_20260914003153_332550-2-DAYTDFUS.TXT",
         "raw_text": _text("DAYTDFUS_20260914_0031Z.txt")},
        {"filename": "A_AXXX81KWNP140017_C_KWIN_20260914001747_331851-2-DAYINDUS.TXT",
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
    assert len(wire) == sw.SpaceWeather.SIZE == 25
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
    store.ingest([{"filename": "A_AXXX81KWNP140017_C_KWIN_20260914001747_331851-2-DAYINDUS.TXT",
                   "raw_text": _text("DAYINDUS_20260914_0017Z.txt")}])
    obj = sw.space_weather_for(store)
    assert obj is not None and obj.kp_forecast == [] and obj.sfi == 114
    text = sw.render(obj)
    assert text.startswith("Kp forecast not received yet") and "SFI 114" in text
    assert len(obj.to_bytes()) == 25
