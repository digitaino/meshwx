"""`>hwo` answers with the outlook it holds.

Regression: the branch read `ol.raw_text`, a field `services.Outlook` has
never had — it carries the product, not its text — so the guard was always
falsy and every `>hwo` fell through to Not available, however many outlooks
the store held.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from meshcore_weather.core import services
from meshcore_weather.geodata import resolver
from meshcore_weather.parser.weather import EMWINProduct, WeatherStore
from meshcore_weather.protocol import v5

HWO_TEXT = """\
FLUS41 KLWX 171000
HWOLWX
Hazardous Weather Outlook
National Weather Service Baltimore MD/Washington DC
600 AM EDT Thu Sep 17 2026

DCZ001-172200-
District of Columbia-
600 AM EDT Thu Sep 17 2026

This hazardous weather outlook is for the District of Columbia.

.DAY ONE...Today and tonight
Scattered showers and thunderstorms are expected this afternoon.
A few storms may produce gusty winds and heavy rainfall.

.DAYS TWO THROUGH SEVEN...Friday through Wednesday
Drier weather returns Friday and lasts into the weekend.

.SPOTTER INFORMATION STATEMENT...
Spotter activation is not expected.

$$
"""

LOC = {"zones": ["DCZ001"], "office": "LWX", "lat": 38.9, "lon": -77.0}


def _product(source: str = "sdr") -> EMWINProduct:
    return EMWINProduct(
        filename="A_FLUS41KLWX171000_C_KWIN_20260917100046_567967-1-HWOLWXDC.TXT",
        emwin_id="HWOLWXDC", product_type="HWO", orig="LWXDC", office="LWX",
        state="DC", timestamp=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
        raw_text=HWO_TEXT, source=source)


def _app(text: str, store: WeatherStore) -> list[dict]:
    from meshcore_weather.protocol.broadcaster import AppResponder
    radio = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    radio.send_channel_data = AsyncMock(return_value=True)
    asyncio.run(AppResponder(store, radio).handle_request(text, "app"))
    return [v5.decode(c.args[0]) for c in radio.send_channel_data.await_args_list]


def test_hwo_answers_with_the_outlooks_text(monkeypatch):
    store = WeatherStore()
    prod = _product()
    store._products[prod.filename] = prod
    monkeypatch.setattr(resolver, "resolve", lambda arg: LOC)
    monkeypatch.setattr(services, "outlook_for",
                        lambda store, loc: services.Outlook(zone="DCZ001", product=prod))

    msgs = _app(">hwo washington dc", store)

    assert msgs, "the bot sent nothing"
    assert [m["name"] for m in msgs] == ["text"] * len(msgs)
    assert msgs[0]["subject"] == v5.SUBJECT_HWO
    body = "".join(m["text"] for m in msgs)
    assert "thunderstorms" in body.lower()


def test_hwo_states_the_products_own_source(monkeypatch):
    store = WeatherStore()
    prod = _product(source="sdr")
    store._products[prod.filename] = prod
    monkeypatch.setattr(resolver, "resolve", lambda arg: LOC)
    monkeypatch.setattr(services, "outlook_for",
                        lambda store, loc: services.Outlook(zone="DCZ001", product=prod))

    msgs = _app(">hwo washington dc", store)

    assert all(m["source"] == v5.SOURCE_GOES for m in msgs)


def test_hwo_with_no_outlook_is_not_available(monkeypatch):
    monkeypatch.setattr(resolver, "resolve", lambda arg: LOC)
    monkeypatch.setattr(services, "outlook_for", lambda store, loc: None)

    msgs = _app(">hwo washington dc", WeatherStore())

    assert [m["name"] for m in msgs] == ["not_available"]
    assert msgs[0]["reason"] == v5.REASON_NO_DATA
