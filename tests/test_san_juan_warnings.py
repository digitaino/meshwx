"""San Juan's warnings reach the app (field report 2026-09-20).

A typed `warn pr` listed a heat advisory for all of Puerto Rico while the
app's `>w PRZ001` came back Not available. A VTEC id names the issuing
station without its leading letter — KLWX is office LWX — but San Juan
issues as TJSJ, so its code reads JSJ while index.json calls the office
SJU. Every binary Warning needs an office index, so the advisory was
dropped before it was built: Puerto Rico and the US Virgin Islands could
never receive a warning in the app.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b

# The heat advisory of 2026-09-20 as the live bot extracted it: HT.Y, ETN 61,
# issued by TJSJ for PRZ001>013 and VIZ001-002.
ADVISORY = {
    "vtec_office": "JSJ", "vtec_phenomenon": "HT", "vtec_significance": "Y", "vtec_etn": 61,
    "event_code": 14, "expiry_minutes": 351,
    "ugcs": [f"PRZ{n:03d}" for n in range(1, 14)] + ["VIZ001", "VIZ002"],
    "headline": "HEAT ADVISORY FROM 10 AM TO 3 PM AST",
    "vertices": [],
}


def _app(text: str, monkeypatch, warnings=(ADVISORY,)) -> list[dict]:
    from meshcore_weather.protocol import broadcaster
    monkeypatch.setattr(broadcaster, "extract_active_warnings",
                        lambda store, coverage=None: [dict(w) for w in warnings])
    radio = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    radio.send_channel_data = AsyncMock(return_value=True)
    asyncio.run(broadcaster.AppResponder(WeatherStore(), radio).handle_request(text, "app"))
    return [v5.decode(c.args[0]) for c in radio.send_channel_data.await_args_list]


def test_the_vtec_office_of_san_juan_is_the_office_index_json_lists():
    assert b.tables.office("SJU") is not None
    assert b.tables.office("JSJ") == b.tables.office("SJU")
    # A four-letter station is not an office code, aliased or not.
    assert b.tables.office("TJSJ") is None


def test_a_san_juan_advisory_has_an_identity():
    ident = b.warning_identity(ADVISORY)
    assert ident is not None
    assert b.identity_str(ident) == "HT.Y.SJU.61"


@pytest.mark.parametrize("ugc", ["PRZ001", "PRZ013", "VIZ002"])
def test_the_zone_request_the_app_sends_is_answered(monkeypatch, ugc):
    msgs = _app(f">w {ugc}", monkeypatch)
    assert [m["name"] for m in msgs] == ["warning"], msgs
    assert msgs[0]["office"] == b.tables.office("SJU")


def test_a_code_the_advisory_does_not_name_is_still_not_available(monkeypatch):
    # It lists zones, not counties: San Juan's own county is not in it.
    msgs = _app(">w PRC127", monkeypatch)
    assert [m["name"] for m in msgs] == ["not_available"]
    assert msgs[0]["reason"] == v5.REASON_NO_DATA
