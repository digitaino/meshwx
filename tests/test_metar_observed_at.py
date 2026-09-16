from datetime import datetime, timezone

from meshcore_weather.protocol.encoders import metar_observed_at

UTC = timezone.utc


def test_the_report_time_comes_from_its_own_group_not_the_product():
    near = datetime(2026, 9, 16, 9, 43, tzinfo=UTC)          # the collective's time
    assert metar_observed_at("KAUS 160853Z 17008KT 10SM CLR 24/17 A3001", near) == \
        datetime(2026, 9, 16, 8, 53, tzinfo=UTC)


def test_a_day_ahead_of_the_product_means_last_month():
    near = datetime(2026, 10, 1, 0, 20, tzinfo=UTC)
    assert metar_observed_at("KAUS 302355Z 00000KT 10SM CLR 20/15 A3005", near) == \
        datetime(2026, 9, 30, 23, 55, tzinfo=UTC)


def test_a_few_minutes_ahead_is_clock_skew_not_next_month():
    near = datetime(2026, 9, 16, 9, 43, tzinfo=UTC)
    assert metar_observed_at("KAUS 160945Z 17008KT 10SM CLR 24/17 A3001", near) == \
        datetime(2026, 9, 16, 9, 45, tzinfo=UTC)


def test_a_corrected_report_and_a_digit_station_still_parse():
    near = datetime(2026, 9, 16, 9, 43, tzinfo=UTC)
    assert metar_observed_at("K7R5 COR 160935Z AUTO 00000KT 10SM CLR 24/17 A3001", near) == \
        datetime(2026, 9, 16, 9, 35, tzinfo=UTC)


def test_no_time_group_means_no_time():
    near = datetime(2026, 9, 16, 9, 43, tzinfo=UTC)
    assert metar_observed_at("KAUS NIL", near) is None
    assert metar_observed_at("KAUS 169999Z", near) is None
