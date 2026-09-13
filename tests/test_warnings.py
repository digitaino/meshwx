"""Regression tests for pyIEM-backed warning extraction."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyiem")

from meshcore_weather.parser.weather import WeatherStore  # noqa: E402
from meshcore_weather.protocol.warnings import extract_active_warnings  # noqa: E402


def _npw_two_events(now: datetime) -> str:
    """An NPW modeled on KEWX 2026-09-13 17:19Z: ONE segment carrying TWO
    VTEC events (a CON for today and a NEW for tomorrow)."""
    d = now.strftime("%d%H%M")
    con_end = (now + timedelta(hours=6)).strftime("%y%m%dT%H00Z")
    new_start = (now + timedelta(hours=24)).strftime("%y%m%dT%H00Z")
    new_end = (now + timedelta(hours=31)).strftime("%y%m%dT%H00Z")
    exp = (now + timedelta(hours=8)).strftime("%d%H%M")
    return (
        f"WWUS74 KEWX {d}\r\r\nNPWEWX\r\r\n\r\r\n"
        "URGENT - WEATHER MESSAGE\r\r\n"
        "National Weather Service Austin/San Antonio TX\r\r\n"
        f"{now.strftime('%I%M %p')} CDT {now.strftime('%a %b %d %Y')}\r\r\n\r\r\n"
        f"TXZ173-191>194-205>209-220>225-{exp}-\r\r\n"
        f"/O.NEW.KEWX.HT.Y.0011.{new_start}-{new_end}/\r\r\n"
        f"/O.CON.KEWX.HT.Y.0010.000000T0000Z-{con_end}/\r\r\n"
        "Williamson-Hays-Travis-Bastrop-Lee-Bexar-Comal-Guadalupe-Caldwell-\r\r\n"
        "Fayette-Atascosa-Wilson-Karnes-Gonzales-De Witt-Lavaca-\r\r\n"
        f"{now.strftime('%I%M %p')} CDT {now.strftime('%a %b %d %Y')}\r\r\n\r\r\n"
        "...HEAT ADVISORY IN EFFECT FROM NOON TO 7 PM CDT MONDAY...\r\r\n"
        "...HEAT ADVISORY REMAINS IN EFFECT UNTIL 7 PM CDT THIS EVENING...\r\r\n\r\r\n"
        "* WHAT...Heat index values up to 111.\r\r\n\r\r\n"
        "$$\r\r\n"
    )


def test_segment_with_two_vtec_lines_yields_two_warnings():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = WeatherStore()
    fname = f"A_WWUS74KEWX{now:%d%H%M}_C_KWIN_{now:%Y%m%d%H%M%S}_310175-1-NPWEWXTX.TXT"
    store.ingest([{"filename": fname, "raw_text": _npw_two_events(now)}])

    warnings = extract_active_warnings(store, coverage=None)
    ewx = [w for w in warnings if w.get("vtec_office") == "EWX"]

    etns = sorted(w["vtec_etn"] for w in ewx)
    assert etns == [10, 11], f"expected both HT.Y events, got {etns}"
    by_etn = {w["vtec_etn"]: w for w in ewx}
    assert by_etn[10]["vtec_action"] == "CON"
    assert by_etn[11]["vtec_action"] == "NEW"
    assert len(by_etn[10]["zones"]) == 16
    # Each event gets its own headline, in VTEC order.
    assert "MONDAY" in by_etn[11]["headline"].upper()
    assert "MONDAY" not in by_etn[10]["headline"].upper()
