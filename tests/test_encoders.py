"""Tests for MeshWX v2 encoders — text weather data → binary messages."""

from meshcore_weather.protocol.encoders import (
    classify_sky,
    encode_metar,
    encode_rwr_city,
    encode_forecast_from_zfp,
)
from meshcore_weather.protocol.meshwx import (
    MSG_OBSERVATION,
    MSG_FORECAST,
    SKY_CLEAR,
    SKY_SCATTERED,
    SKY_OVERCAST,
    SKY_RAIN,
    SKY_THUNDERSTORM,
    unpack_observation,
    unpack_forecast,
)


class TestClassifySky:
    def test_clear(self):
        assert classify_sky("Clear") == SKY_CLEAR
        assert classify_sky("Sunny and warm") == SKY_CLEAR

    def test_scattered(self):
        assert classify_sky("Scattered clouds") == SKY_SCATTERED

    def test_rain(self):
        assert classify_sky("Light rain") == SKY_RAIN
        assert classify_sky("Rain showers") == SKY_RAIN

    def test_thunderstorm(self):
        assert classify_sky("Thunderstorms possible") == SKY_THUNDERSTORM

    def test_overcast(self):
        assert classify_sky("Overcast skies") == SKY_OVERCAST
        assert classify_sky("Mostly cloudy") == SKY_OVERCAST


class TestEncodeMetar:
    def test_basic_metar(self):
        metar = "KAUS 082151Z 17010KT 10SM SCT040 BKN070 28/18 A3010"
        msg = encode_metar("KAUS", metar, 720)
        assert msg is not None
        assert msg[0] == MSG_OBSERVATION
        decoded = unpack_observation(msg)
        assert decoded["location"]["station"] == "KAUS"
        # 28°C = 82°F, 18°C = 64°F
        assert decoded["temp_f"] == 82
        assert decoded["dewpoint_f"] == 64
        # 10 knots ≈ 12 mph
        assert 10 <= decoded["wind_speed_mph"] <= 13
        assert decoded["wind_dir"] == "S"  # 170° is S/SSW

    def test_metar_with_gust(self):
        metar = "KDFW 082151Z 18015G25KT 10SM FEW050 SCT080 32/22 A2995"
        msg = encode_metar("KDFW", metar, 900)
        decoded = unpack_observation(msg)
        assert decoded["wind_gust_mph"] > 0
        # 25 knots ≈ 29 mph
        assert decoded["wind_gust_mph"] >= 28

    def test_metar_with_rain(self):
        metar = "KHOU 082151Z 09015KT 2SM +RA BKN008 OVC015 25/24 A2985"
        msg = encode_metar("KHOU", metar, 900)
        decoded = unpack_observation(msg)
        assert decoded["sky_code"] == SKY_RAIN
        assert decoded["visibility_mi"] == 2

    def test_metar_freezing(self):
        metar = "KORD 082151Z 36008KT 10SM CLR M05/M12 A3025"
        msg = encode_metar("KORD", metar, 900)
        decoded = unpack_observation(msg)
        # -5°C = 23°F, -12°C = 10°F
        assert decoded["temp_f"] == 23
        assert decoded["dewpoint_f"] == 10

    def test_invalid_metar(self):
        assert encode_metar("KAUS", "not a metar", 0) is None
        assert encode_metar("KAUS", "", 0) is None


class TestEncodeRwr:
    def test_basic_rwr(self):
        # Typical RWR line: AUSTIN SUNNY 85 55 40 S10 30.05
        msg = encode_rwr_city("TXZ192", "SUNNY 85 55 40 S10 30.05", 720)
        assert msg is not None
        decoded = unpack_observation(msg)
        assert decoded["location"]["zone"] == "TXZ192"
        assert decoded["temp_f"] == 85
        assert decoded["dewpoint_f"] == 55
        assert decoded["sky_code"] == SKY_CLEAR
        assert decoded["wind_speed_mph"] == 10
        assert decoded["wind_dir"] == "S"

    def test_rwr_with_rain(self):
        msg = encode_rwr_city("TXZ192", "LGT RAIN 72 68 85 SE8 29.95", 720)
        decoded = unpack_observation(msg)
        assert decoded["sky_code"] == SKY_RAIN
        assert decoded["temp_f"] == 72


class TestEncodeForecast:
    def test_basic_zfp(self):
        zfp = """
.TONIGHT...Mostly clear. Lows around 60. Southeast winds around 5 mph.

.FRIDAY...Sunny. Highs in the upper 80s. Southeast winds 5 to 10 mph.

.FRIDAY NIGHT...Partly cloudy. Lows in the mid 60s. Southeast winds 5 mph.

.SATURDAY...Mostly sunny. Highs in the lower 90s. Southeast winds 5 to 10 mph.
"""
        msg = encode_forecast_from_zfp("TXZ192", zfp, issued_hours_ago=2)
        assert msg is not None
        assert msg[0] == MSG_FORECAST
        decoded = unpack_forecast(msg)
        assert decoded["location"]["zone"] == "TXZ192"
        assert len(decoded["periods"]) == 4
        # Tonight: low 60, no high
        assert decoded["periods"][0]["low_f"] == 60
        assert decoded["periods"][0]["high_f"] is None
        # Friday: high 80 (upper 80s), no low
        assert decoded["periods"][1]["high_f"] == 80
        assert decoded["periods"][1]["sky_code"] == SKY_CLEAR

    def test_zfp_with_thunder(self):
        zfp = """
.TONIGHT...Thunderstorms likely. 60 percent chance of rain. Lows around 65.

.TOMORROW...Scattered thunderstorms. Highs in the upper 80s.
"""
        msg = encode_forecast_from_zfp("TXZ192", zfp, issued_hours_ago=1)
        decoded = unpack_forecast(msg)
        assert decoded["periods"][0]["sky_code"] == SKY_THUNDERSTORM
        assert decoded["periods"][0]["precip_pct"] == 60
        assert decoded["periods"][0]["condition_flags"] & 0x01  # thunder flag
