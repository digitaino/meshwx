"""Real products from the dish, replayed through the VTEC lifecycle tracker
and the parsers. Expected values were checked against api.weather.gov,
forecast.weather.gov and IEM on 2026-09-14; the tests pin them so the
parsers stay right without any network."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol.warnings import extract_active_warnings
from tests.test_core import fresh

PRODUCTS = Path(__file__).parent / "fixtures" / "products"
UTC = timezone.utc


def _store(*name_fragments: str) -> WeatherStore:
    recs = []
    for f in sorted(PRODUCTS.iterdir()):
        if any(frag in f.name for frag in name_fragments):
            recs.append({"filename": fresh(f.name), "raw_text": f.read_bytes().decode("utf-8", "replace")})
    assert recs, name_fragments
    store = WeatherStore()
    store.ingest(recs)
    return store


def _events(store, now, office=None):
    out = {}
    for w in extract_active_warnings(store, coverage=None, now=now):
        if w.get("vtec_etn") is None or (office and w["vtec_office"] != office):
            continue
        out[f"{w['vtec_phenomenon']}.{w['vtec_significance']}.{w['vtec_etn']}"] = w
    return out


def test_cancelled_event_stays_dead_after_partial_cancel_and_upgrade():
    # CYS 11:29Z: HW.A.37 CAN for WYZ106/109, UPG for WYZ110, NEW HW.W.38 for WYZ110.
    # CYS 19:38Z: HW.W.38 CAN for WYZ110.
    store = _store("NPWCYSWY")
    ev = _events(store, datetime(2026, 9, 14, 15, 0, tzinfo=UTC), "CYS")
    assert "HW.A.37" not in ev
    assert set(ev["HW.W.38"]["ugcs"]) == {"WYZ110"}
    ev = _events(store, datetime(2026, 9, 14, 20, 0, tzinfo=UTC), "CYS")
    assert "HW.W.38" not in ev and "HW.A.37" not in ev


def test_heat_advisory_replaced_by_its_successor():
    # PAH 04:06Z: CAN HT.Y.12, EXT HT.Y.13 (three segments); 16:22Z: CON HT.Y.13.
    store = _store("NPWPAHKY")
    ev = _events(store, datetime(2026, 9, 14, 17, 0, tzinfo=UTC), "PAH")
    assert "HT.Y.12" not in ev and "HT.Y.13" in ev
    assert ev["HT.Y.13"]["expires_at"] == datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
    assert len(ev["HT.Y.13"]["ugcs"]) >= 10


def test_marine_advisory_is_per_zone():
    # MTR 03:35Z: GL.W.29 cancelled/expired everywhere; SC.Y.110 EXB with
    # different end times per zone; SC.Y.112 NEW for two zones from 22Z.
    store = _store("MWWMTRCA")
    ev = _events(store, datetime(2026, 9, 14, 12, 0, tzinfo=UTC), "MTR")
    assert "GL.W.29" not in ev                                        # cancelled/expired everywhere
    assert set(ev["SC.Y.109"]["ugcs"]) == {"PZZ570", "PZZ575"}         # cancelled on 545, continued on 570/575
    assert set(ev["SC.Y.110"]["ugcs"]) == {"PZZ565", "PZZ535", "PZZ571"}   # 560/545 ended 10Z, 530/531 expired
    assert set(ev["SC.Y.112"]["ugcs"]) == {"PZZ560", "PZZ545"}
    assert ev["SC.Y.112"]["onset_at"] == datetime(2026, 9, 14, 22, 0, tzinfo=UTC)
    ev = _events(store, datetime(2026, 9, 15, 6, 0, tzinfo=UTC), "MTR")
    assert set(ev["SC.Y.110"]["ugcs"]) == {"PZZ565", "PZZ571"} and "SC.Y.112" not in ev


def test_flood_watch_extended_in_area_and_time():
    # DMX FA.A.13: NEW 27 zones; CON; then EXB adds 5 zones and EXT to 16/18Z.
    store = _store("FFADMXIA", "FFATOPKS")
    ev = _events(store, datetime(2026, 9, 14, 18, 0, tzinfo=UTC))      # TOP's watch was issued 17:47Z
    dmx = ev["FA.A.13"]
    assert dmx["vtec_office"] == "DMX"
    assert {"IAZ062", "IAZ074", "IAZ084", "IAZ094", "IAZ095", "IAZ025", "IAZ093"} <= set(dmx["ugcs"])
    assert len(dmx["ugcs"]) == 35          # 30 original + 5 added by the EXB
    assert dmx["expires_at"] == datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
    top = ev["FA.A.9"]
    assert top["vtec_office"] == "TOP" and top["onset_at"] == datetime(2026, 9, 15, 18, 0, tzinfo=UTC)


def test_pfm_houston_extended_days_from_glued_satellite_copy():
    from meshcore_weather.parser.pfm import parse_pfm, downsample_to_daily
    raw = next(PRODUCTS.glob("*PFMHGXTX*")).read_bytes().decode("utf-8", "replace")
    bush = [p for p in parse_pfm(raw) if p.name.startswith("Bush")][0]
    days = [(d.local_date.isoformat()[5:], d.high_f, d.low_f) for d in downsample_to_daily(bush)][:4]
    assert days == [("09-14", 94, 78), ("09-15", 95, 78), ("09-16", 94, 79), ("09-17", 91, 76)]


def test_pfm_guam_and_puerto_rico_parse_sanely():
    from meshcore_weather.parser.pfm import parse_pfm, downsample_to_daily
    for frag, tz_expect in (("PFMGUMGU", 10), ("PFMSJUPR", -4)):
        raw = next(PRODUCTS.glob(f"*{frag}*")).read_bytes().decode("utf-8", "replace")
        pts = parse_pfm(raw)
        assert pts, frag
        for pt in pts:
            assert pt.tz_offset_hours == tz_expect, (frag, pt.name, pt.tz_offset_hours)
            days = downsample_to_daily(pt)
            assert len(days) >= 5, (frag, pt.name)
            for d in days:
                assert d.high_f is not None and d.low_f is not None and d.high_f >= d.low_f
            assert days[0].local_date.isoformat() == "2026-09-14" or days[0].local_date.isoformat() == "2026-09-15"
    sju = [p for p in parse_pfm(next(PRODUCTS.glob("*PFMSJUPR*")).read_bytes().decode()) if p.name.startswith("Luis Munoz")][0]
    assert [(d.high_f, d.low_f) for d in downsample_to_daily(sju)][:2] == [(91, 80), (93, 80)]


def test_lsr_billings_wind_reports_all_parse():
    from meshcore_weather.core.services import parse_lsr_entries
    raw = sorted(PRODUCTS.glob("*LSRBYZMT*"))[-1].read_bytes().decode("utf-8", "replace")
    import re
    lines = [l for l in raw.replace("\r", "").split("\n") if re.match(r"^\d{4} [AP]M ", l)]
    entries = parse_lsr_entries(raw)
    assert len(entries) == len(lines) >= 1
    assert all(e["state"] == "MT" and e["date"] == "09/14/2026" for e in entries)
    assert entries[0]["event"] == "Non-Tstm Wnd Gst"
