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
