"""Reply formatting: nothing cut mid-item, readable names; paging is tested in test_pages.py."""

from types import SimpleNamespace

from meshcore_weather.core import render_text as rt
from meshcore_weather.main import HELP_TEXT, channel_fit


def test_fit_list_keeps_every_item():
    items = [f"Item{i} Somewhere Long" for i in range(12)]
    out = rt.fit_list("16 storm reports NY: ", items, cap=100)
    assert out == "16 storm reports NY: " + "; ".join(items)      # paging happens later, in core/pages.py
    assert rt.fit_list("A: ", ["x", "y"], cap=100) == "A: x; y"


def test_storm_reports_are_readable():
    sr = SimpleNamespace(entries=[
        {"event": "Flash Flood", "location": "1 NNE Little Falls", "mag": "", "state": "NY"},
        {"event": "Flash Flood", "location": "Paterson", "mag": "", "state": "NJ"},
        {"event": "Flash Flood", "location": "1 SSE Paterson", "mag": "", "state": "NJ"},
        {"event": "Hail", "location": "3 W Albany", "mag": "1.00 in", "state": "NY"},
    ])
    out = rt.storm_reports("in NY", sr, state="NY")
    assert out.startswith("4 storm reports in NY: FlashFld Little Falls; FlashFld Paterson NJ x2; Hail 1.00 in Albany")
    assert len(out) <= rt.MAX_DM


def test_help_and_channel_fit():
    assert len(HELP_TEXT) <= 147 and "\n" not in HELP_TEXT
    long = "; ".join(f"Thing {i} at Place {i}" for i in range(30))
    out = channel_fit(long, 147)
    assert len(out) <= 147 and out.endswith("… DM me for all")
    assert not out.split("…")[0].rstrip().endswith("Plac")      # cut on a boundary
    assert channel_fit("short reply", 147) == "short reply"


def test_lsr_parser_on_real_lines():
    from meshcore_weather.core.services import parse_lsr_entries
    text = (
        "0730 AM     Flash Flood      1 NNE Little Falls      40.89N 74.21W\r\r\n"
        "09/13/2026                   Passaic            NJ   Fire Dept/Rescue \r\r\n\r\r\n"
        "0855 PM     Hail             3 NNW Mechanicville     42.94N 73.71W\r\r\n"
        "09/13/2026  M1.00 INCH       Saratoga           NY   Public           \r\r\n"
    )
    e = parse_lsr_entries(text)
    assert [(x["event"], x["location"], x["mag"], x["state"], x["county"]) for x in e] == [
        ("Flash Flood", "1 NNE Little Falls", "", "NJ", "Passaic"),
        ("Hail", "3 NNW Mechanicville", "M1.00 INCH", "NY", "Saratoga"),
    ]
    sr = SimpleNamespace(entries=e)
    assert rt.storm_reports("in NY", sr, state="NY") == "2 storm reports in NY: FlashFld Little Falls NJ; Hail M1.00 INCH Mechanicville"


def test_lsr_parser_handles_full_width_event_column():
    from meshcore_weather.core.services import parse_lsr_entries
    text = ("1235 PM     Non-Tstm Wnd Gst 2 NNE Laurel            45.70N 108.76W\r\r\n"
            "09/14/2026  M58 MPH          Yellowstone        MT   Mesonet          \r\r\n")
    e = parse_lsr_entries(text)
    assert [(x["event"], x["location"], x["mag"], x["county"], x["state"], x["date"]) for x in e] == [
        ("Non-Tstm Wnd Gst", "2 NNE Laurel", "M58 MPH", "Yellowstone", "MT", "09/14/2026")]
