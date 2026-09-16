"""Store data -> v5 messages (protocol/v5_builders.py) and the warnings job
state machine (schedule/executor.py): new, update, repeat, cancel."""

import time
from datetime import datetime, timedelta, timezone

from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b
from meshcore_weather.schedule import executor as ex
from meshcore_weather.schedule.models import BroadcastJob


def _warning(etn=42, hours=2, ugcs=("TXC453", "TXC209"), vertices=True, hail=4, wind=60, tornado=0,
             phen="SV", sig="W"):
    now = datetime.now(timezone.utc)
    return {
        "event_code": b.tables.event_code(f"{phen}.{sig}"), "vtec_office": "EWX", "vtec_etn": etn,
        "vtec_phenomenon": phen, "vtec_significance": sig,
        "expires_at": now + timedelta(hours=hours), "onset_unix_min": int(now.timestamp() // 60),
        "ugcs": list(ugcs), "zones": [u for u in ugcs if u[2] == "Z"],
        "vertices": [(30.30, -97.90), (30.45, -97.85), (30.40, -97.60), (30.25, -97.65)] if vertices else [],
        "hail_qin": hail, "wind_mph": wind, "tornado_tag": tornado, "flood_source": 0, "flood_damage": 0,
        "headline": "Severe Thunderstorm Warning until 4:45 PM", "description": "60 mph wind and quarter size hail",
    }


def test_warning_message_round_trips_identity_tags_polygon_and_counties():
    w = _warning()
    msg = b.warning_message(7, 0x041D, w)
    d = v5.decode(msg)
    assert d["name"] == "warning" and d["seq"] == 7 and d["bot"] == 0x041D
    assert (d["event"], d["office"], d["etn"]) == b.warning_identity(w)
    assert d["office"] == b.tables.office("EWX") and b.tables.office_code(d["office"]) == "EWX"
    assert d["hail_qin"] == 4 and d["wind_mph"] == 60 and d["expires_min"] == b.expires_min(w)
    assert len(d["polygon"]) == 4 and abs(d["polygon"][0][0] - 30.30) < 0.0002
    counties = {(a["start"], a["county"]) for a in d["areas"]}
    assert counties == {(453, True), (209, True)} and all(a["state"] == b.tables.states.index("TX") for a in d["areas"])
    assert b.identity_str(b.warning_identity(w)) == "SV.W.EWX.42"
    assert b.parse_identity("SV.W.EWX.42") == b.warning_identity(w) and b.parse_identity("XX.Q.EWX.1") is None
    assert len(msg) <= v5.MAX_DATA


def test_oversized_polygon_is_thinned_rather_than_dropped():
    w = _warning()
    w["vertices"] = [(30.0 + i * 0.01, -97.0 - i * 0.01) for i in range(60)]
    w["ugcs"] = [f"TXC{n}" for n in range(1, 120, 2)]           # 60 non-consecutive counties
    msg = b.warning_message(1, 1, w)
    assert msg is not None and len(msg) <= v5.MAX_DATA
    d = v5.decode(msg)
    assert 3 <= len(d["polygon"]) <= 16


def test_digest_lists_identities_soonest_first_and_skips_sps():
    ws = [_warning(etn=1, hours=5), _warning(etn=2, hours=1), {**_warning(etn=3), "vtec_etn": None}]
    d = v5.decode(b.digest_message(3, 9, ws, feed_health=7))
    assert d["name"] == "digest" and d["feed_health"] == 7
    assert [e["etn"] for e in d["entries"]] == [2, 1]
    assert 55 <= d["entries"][0]["expires_rel"] <= 60


class _Ctx:
    """Minimal ExecutorContext stand-in with a controllable warning list."""

    def __init__(self, warnings):
        self.warnings = warnings


def _ctx(monkeypatch, warnings):
    from meshcore_weather.parser.weather import WeatherStore
    from meshcore_weather.protocol.coverage import Coverage
    monkeypatch.setattr(ex, "extract_active_warnings", lambda store, coverage=None: list(warnings))
    return ex.ExecutorContext(store=WeatherStore(), coverage=Coverage.empty(), seq=b.SeqCounter(), bot=1)


def test_warnings_job_sends_new_then_repeats_then_ignores_wording_then_cancels(monkeypatch):
    w = _warning()
    active = [w]
    ctx = _ctx(monkeypatch, active)
    job = BroadcastJob(id="warnings", name="w", product="warnings", location_type="coverage", interval_minutes=2)

    first = ex._build_warnings(job, ctx)
    assert len(first) == 1 and v5.decode(first[0])["update"] is False
    key = "SV.W.EWX.42"
    assert key in ctx.warning_state and ctx.warning_state[key]["repeat_at"] is not None

    assert ex._build_warnings(job, ctx) == []                     # nothing changed, not yet time to repeat
    ctx.warning_state[key]["repeat_at"] = time.time() - 1
    rep = ex._build_warnings(job, ctx)
    assert len(rep) == 1 and ctx.warning_state[key]["repeat_at"] is None     # the one life-safety repeat

    w["headline"] = "reworded"                                     # wording only: silence
    assert ex._build_warnings(job, ctx) == []
    w["expires_at"] += timedelta(hours=1)                          # material: update flag set
    upd = ex._build_warnings(job, ctx)
    assert len(upd) == 1 and v5.decode(upd[0])["update"] is True

    active.clear()                                                 # gone with time left: cancel
    out = ex._build_warnings(job, ctx)
    assert len(out) == 1 and v5.decode(out[0])["name"] == "cancel" and ctx.cancel_sent
    assert key not in ctx.warning_state


def test_expired_warning_disappears_without_a_cancel(monkeypatch):
    w = _warning(hours=0.05)
    active = [w]
    ctx = _ctx(monkeypatch, active)
    job = BroadcastJob(id="warnings", name="w", product="warnings", location_type="coverage", interval_minutes=2)
    ex._build_warnings(job, ctx)
    active.clear()
    assert ex._build_warnings(job, ctx) == [] and not ctx.cancel_sent


def test_text_messages_chunk_and_share_a_group():
    seq = b.SeqCounter(250)
    msgs = b.text_messages(seq, 5, v5.SUBJECT_AFD, "word " * 80)
    ds = [v5.decode(m) for m in msgs]
    assert len(ds) == 3 and [d["idx"] for d in ds] == [0, 1, 2] and all(d["total"] == 3 for d in ds)
    assert {d["group"] for d in ds} == {250} and [d["seq"] for d in ds] == [250, 251, 252]
    assert "".join(d["text"] for d in ds).split() == ["word"] * 80


def test_humidity_and_feels_like():
    assert b._humidity(95, 75) in range(50, 56)
    assert b._feels_delta(95, 52, 5) >= 8            # heat index above 100
    assert b._feels_delta(30, None, 20) <= -10       # wind chill
    assert b._feels_delta(70, 50, 5) == 0


# -- Revision 3 ---------------------------------------------------------------------


def _half_hour_ahead(hours=2):
    """A future :00 or :30, so +25 minutes stays inside one 30-minute bucket."""
    return datetime.fromtimestamp((int(time.time() // 1800) + hours * 2) * 1800, timezone.utc)


def test_expiry_extension_inside_one_half_hour_is_sent(monkeypatch):
    w = _warning()
    w["expires_at"] = _half_hour_ahead()
    before = b.warning_fingerprint(w)
    w["expires_at"] += timedelta(minutes=25)                        # 21:00 -> 21:25
    assert b.warning_fingerprint(w) != before
    ctx = _ctx(monkeypatch, [w])
    job = BroadcastJob(id="warnings", name="w", product="warnings", location_type="coverage", interval_minutes=2)
    ex._build_warnings(job, ctx)
    w["expires_at"] += timedelta(minutes=25)
    upd = ex._build_warnings(job, ctx)
    assert len(upd) == 1 and v5.decode(upd[0])["update"] is True


def test_invented_expiry_only_counts_in_half_hours(monkeypatch):
    w = _warning()
    del w["expires_at"]                                             # fallback: now + expiry_minutes
    monkeypatch.setattr(b, "now_min", lambda: 29823900)
    fp = b.warning_fingerprint(w)
    monkeypatch.setattr(b, "now_min", lambda: 29823901)
    assert b.warning_fingerprint(w) == fp
    ufn = {**_warning(), "expires_estimated": True, "expires_at": datetime.fromtimestamp(29823900 * 60, timezone.utc)}
    fp = b.warning_fingerprint(ufn)                                 # until further notice: now + 12 h
    ufn["expires_at"] += timedelta(minutes=1)
    assert b.warning_fingerprint(ufn) == fp


def test_national_centres_are_appended_and_unknown_offices_are_not_sent(caplog):
    assert (b.tables.office("NHC"), b.tables.office("WNS"), b.tables.office("EWX")) == (125, 126, 35)
    watch = {**_warning(phen="TO", sig="A", vertices=False, ugcs=("TXC453",)), "vtec_office": "WNS"}
    assert b.identity_str(b.warning_identity(watch)) == "TO.A.WNS.42"
    assert v5.decode(b.warning_message(1, 1, watch))["office"] == 126
    stray = {**_warning(etn=5), "vtec_office": "XYZ"}
    assert b.warning_identity(stray) is None and b.warning_message(1, 1, stray) is None
    assert [e["etn"] for e in v5.decode(b.digest_message(1, 1, [stray, _warning(etn=7)], 0))["entries"]] == [7]
    assert "XYZ" in caplog.text


def _metar_store(*lines):
    from meshcore_weather.parser.weather import WeatherStore
    ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    store = WeatherStore()
    store.ingest([{"filename": f"A_SAUS70KWBC{ts:%d%H%M}_C_KWIN_{ts:%Y%m%d%H%M%S}_{i:06d}-2-SAHOURLY.TXT",
                   "raw_text": "SAUS70 KWBC\nMETAR\n" + line} for i, line in enumerate(lines, 1)])
    return store


def test_absent_metar_groups_go_out_as_unknown():
    store = _metar_store("KAUS 151153Z VRB04KT 31/ FEW250 RMK AO2 SLP123",
                         "KGTU 151156Z AUTO 12/08 A3001",
                         "KHYI 151155Z 00000KT 1 1/2SM BR OVC003 20/19 A2990")
    aus, gtu, hyi = v5.decode(b.obs_message(1, 1, store, ["KAUS", "KGTU", "KHYI"]))["stations"]
    assert aus["temp_f"] == 88 and aus["dewpoint_f"] is None and aus["humidity_pct"] is None
    assert aus["wind_mph"] == 5 and aus["wind_dir_deg"] == 0      # VRB: the nibble has no unknown, 0 is sent
    assert aus["visibility_mi"] is None and aus["pressure_inhg"] is None and aus["sky"] == 1
    assert gtu["wind_mph"] is None and gtu["sky"] == 15 and gtu["pressure_inhg"] == 30.01
    assert hyi["wind_mph"] == 0 and hyi["visibility_mi"] == 1        # 1 1/2 statute miles, rounded down


def test_parse_metar_fractional_and_less_than_visibility():
    from meshcore_weather.protocol.encoders import parse_metar
    vis = lambda group: parse_metar(f"KAUS 151153Z 18005KT {group} OVC002 20/19 A2990")["visibility_mi"]
    assert (vis("1/2SM"), vis("1 1/2SM"), vis("M1/4SM"), vis("3/4SM"), vis("10SM")) == (0.5, 1.5, 0.25, 0.75, 10)
    assert parse_metar("KAUS 151153Z 18005KT OVC002 20/19")["visibility_mi"] is None


def test_out_of_range_pressure_is_unknown_not_a_lost_batch():
    store = _metar_store("PADK 151153Z 18005KT 10SM CLR 08/05 A2870",
                         "KGTU 151156Z 18005KT 10SM CLR 20/10 A3001",
                         "PAFA 151153Z 00000KT 10SM CLR M40/M44 A3160")
    stations = [s["pressure_inhg"] for s in v5.decode(b.obs_message(1, 1, store, ["PADK", "KGTU", "PAFA"]))["stations"]]
    assert stations == [None, 30.01, None]


# -- Revision 5: per-station observation ages and the warning issue time ------------


def _aged_metar_store(pairs):
    """One hourly product per station, so each METAR carries its own time.
    `pairs` is (minutes before now, METAR line)."""
    from meshcore_weather.parser.weather import WeatherStore
    now = datetime.now(timezone.utc)
    store = WeatherStore()
    store.ingest([
        {"filename": f"A_SAUS70KWBC{ts:%d%H%M}_C_KWIN_{ts:%Y%m%d%H%M%S}_{i:06d}-2-SAHOURLY.TXT",
         "raw_text": "SAUS70 KWBC\nMETAR\n" + line}
        for i, (ts, line) in enumerate(
            ((now - timedelta(minutes=m), line) for m, line in pairs), 1)
    ])
    return store


def _clear(icao):
    return f"{icao} 151153Z 18005KT 10SM CLR 20/10 A3001"


def test_each_station_says_how_far_behind_the_batch_time_its_own_report_is():
    """The batch timestamp is the newest METAR in it, so one station reads 0
    and the others say how much older they are: "as of 8:24 PM" then belongs
    to a station, not to the packet (spec 6, revision 5)."""
    store = _aged_metar_store([(4, _clear("KAUS")), (25, _clear("KGTU")), (118, _clear("KHYI"))])
    d = v5.decode(b.obs_message(1, 1, store, ["KAUS", "KGTU", "KHYI"]))
    assert d["flags"] == v5.FLAG_OBS_AGES
    assert [s["age_min"] for s in d["stations"]] == [0, 20, 110]
    # ts is the newest report's minute, not "now".
    newest = store._find_metar_raw("KAUS")[1]
    assert d["ts_min"] == int(newest.timestamp() // 60)


def test_a_full_batch_drops_its_farthest_station_to_carry_the_ages():
    """Fourteen stations are 163 bytes on their own, so the ages cost the
    fourteenth — the farthest, since the list arrives nearest first — rather
    than being sent for some stations and not others."""
    icaos = ["KAUS", "KATT", "KGTU", "KHYI", "KEDC", "KBAZ", "KRYW",
             "KDZB", "KSSF", "KSAT", "KTPL", "KILE", "KGRK", "KLZZ"]
    store = _aged_metar_store([(5 + i, _clear(c)) for i, c in enumerate(icaos)])
    msg = b.obs_message(1, 1, store, icaos)
    assert len(msg) == 159 <= v5.MAX_DATA
    d = v5.decode(msg)
    assert d["flags"] == v5.FLAG_OBS_AGES
    assert len(d["stations"]) == v5.MAX_STATIONS_WITH_AGES == 13
    dropped = b.tables.station("KLZZ")                 # the farthest, last in the list
    assert dropped not in {s["station"] for s in d["stations"]}
    assert all(s["age_min"] is not None for s in d["stations"])


def test_thirteen_stations_keep_every_age():
    icaos = ["KAUS", "KATT", "KGTU", "KHYI", "KEDC", "KBAZ", "KRYW",
             "KDZB", "KSSF", "KSAT", "KTPL", "KILE", "KGRK"]
    store = _aged_metar_store([(5, _clear(c)) for c in icaos])
    d = v5.decode(b.obs_message(1, 1, store, icaos))
    assert len(d["stations"]) == 13 and all(s["age_min"] == 0 for s in d["stations"])


def test_a_warning_carries_the_issue_time_and_keeps_it_when_the_polygon_goes():
    issued = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=37)
    w = {**_warning(), "issued_at": issued}
    d = v5.decode(b.warning_message(7, 0x041D, w))
    assert d["flags"] & v5.FLAG_WARNING_ISSUED
    assert d["issued_min"] == int(issued.timestamp() // 60) == b.issued_min(w)

    big = {**w, "vertices": [(30.0 + i * 0.01, -97.0 - i * 0.01) for i in range(60)],
           "ugcs": [f"TXC{n}" for n in range(1, 120, 2)]}
    msg = b.warning_message(1, 1, big)
    assert msg is not None and len(msg) <= v5.MAX_DATA
    d = v5.decode(msg)
    assert 3 <= len(d["polygon"]) <= 16                      # the polygon is what gives way
    assert d["issued_min"] == int(issued.timestamp() // 60)   # the issue time never does


def test_a_warning_with_no_known_issue_time_sends_the_old_form():
    w = _warning()
    assert w.get("issued_at") is None and b.issued_min(w) is None
    d = v5.decode(b.warning_message(1, 1, w))
    assert not d["flags"] & v5.FLAG_WARNING_ISSUED and d["issued_min"] is None


def _austin_point(**changes):
    from meshcore_weather.parser.pfm import parse_pfm
    from tests.test_pfm import SAMPLE_PFM
    pt = parse_pfm(SAMPLE_PFM)[0]
    for k, v in changes.items():
        setattr(pt, k, v)
    return pt


def _serve(monkeypatch, pt):
    from types import SimpleNamespace
    from meshcore_weather.core import services
    monkeypatch.setattr(services, "nearest_pfm_point",
                        lambda store, lat, lon: (pt, SimpleNamespace(timestamp=pt.issue_time), 0.0))


def test_forecast_first_counts_from_the_issue_date_at_the_point(monkeypatch):
    from datetime import date
    from meshcore_weather.parser.pfm import downsample_to_daily
    from meshcore_weather.parser.weather import WeatherStore
    # 03:30Z Saturday is 10:30 PM CDT Friday: an evening issuance, first full day Saturday.
    pt = _austin_point(issue_time=datetime(2026, 4, 11, 3, 30, tzinfo=timezone.utc))
    assert downsample_to_daily(pt)[0].local_date == date(2026, 4, 11)
    _serve(monkeypatch, pt)
    d = v5.decode(b.forecast_message(1, 1, WeatherStore(), pt.lat, pt.lon))
    assert d["first_period"] == 2 and len(d["periods"]) >= 2
    pt.issue_time = datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc)   # 6 AM CDT Saturday
    assert v5.decode(b.forecast_message(1, 1, WeatherStore(), pt.lat, pt.lon))["first_period"] == 0


def test_a_cold_dry_day_is_not_wintry_but_freezing_rain_is(monkeypatch):
    from meshcore_weather.parser.weather import WeatherStore
    pt = _austin_point()
    for s in pt.slots:
        s.temp_f, s.obvis = 30, None          # frost territory, nothing falling
    _serve(monkeypatch, pt)
    days = v5.decode(b.forecast_message(1, 1, WeatherStore(), pt.lat, pt.lon))["periods"]
    assert days and not any(d["wintry"] for d in days)
    for s in pt.slots:
        s.obvis = "ZR"
    days = v5.decode(b.forecast_message(1, 1, WeatherStore(), pt.lat, pt.lon))["periods"]
    assert all(d["wintry"] for d in days)


def test_forecast_at_shared_coordinates_answers_under_the_index_asked_for(monkeypatch):
    from meshcore_weather.parser.weather import WeatherStore
    b.tables.load()
    lat, lon = b.tables.points[1840][2], b.tables.points[1840][3]
    assert [lat, lon] == b.tables.points[1617][2:4]
    pt = _austin_point(lat=lat, lon=lon, name="Stafford Springs-Tolland CT")
    _serve(monkeypatch, pt)
    fc = lambda *a, **k: v5.decode(b.forecast_message(1, 1, WeatherStore(), *a, **k))["point"]
    assert fc(lat, lon, point=1840) == 1840 and fc(lat, lon, point=1617) == 1617
    assert fc(lat, lon) == 1617                                     # no index asked: the point's own name decides
    pt.name = "Windsor Locks-Hartford CT"
    assert fc(lat, lon) == 1840
    assert fc(30.19, -97.67, point=102) == 1840                     # a substitute keeps its own index


def test_f_request_by_index_carries_that_index(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from meshcore_weather.parser.weather import WeatherStore
    from meshcore_weather.protocol.broadcaster import AppResponder
    b.tables.load()
    p = b.tables.points[454]
    _serve(monkeypatch, _austin_point(lat=p[2], lon=p[3], name="Conway Lake-Tyler WV"))   # 402's name, 454's place
    radio = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    radio.send_channel_data = AsyncMock(return_value=True)
    asyncio.run(AppResponder(WeatherStore(), radio).handle_request(">f 454", "a"))
    d = v5.decode(radio.send_channel_data.await_args.args[0])
    assert d["name"] == "forecast" and d["point"] == 454
