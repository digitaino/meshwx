"""The status channel (status_channel.py): one line per change, none for a
flap, no more than the rate limits allow, and never on the request channel."""

import asyncio
from types import SimpleNamespace

import pytest

from meshcore_weather.config import settings
from meshcore_weather.main import WeatherBot
from meshcore_weather.status_channel import MAX_LEN, PER_HOUR, REPEAT_S, StatusReporter

T = 1_800_000_000.0


def _state(locked=True, disk=49, temp=51.0, throttled="0x0", stats=True) -> dict:
    """/api/state as the Pi's dashboard sends it, the parts the reporter reads."""
    s = {"mode": "receive", "t": T,
         "status": {"disk_used_pct": disk, "temp": temp, "services": {"goesrecv": "active"}}}
    if throttled is not None:
        s["status"]["throttled"] = throttled
    if stats:
        s["stats"] = {"locked": locked, "vit_avg": 300, "feed_alive": True}
    return s


def _feed(on=False, added=0, lag=None, quiet=3.0):
    return SimpleNamespace(fallback_active=on, fallback_added=added, feed_lag_min=lag,
                           dish_quiet_min=lambda now=None: quiet)


def _reporter() -> StatusReporter:
    async def never(text):
        raise AssertionError("check() does no I/O")
    return StatusReporter(send=never)


def _check(r, now, state=None, reachable=True, feed=None):
    return r.check(now, state if state is not None else _state(), reachable, feed or _feed())


# -- the conditions -----------------------------------------------------------------


def test_lock_lost_is_said_after_ten_minutes_and_locked_again_once():
    r = _reporter()
    assert _check(r, T, _state(locked=False)) == []
    assert _check(r, T + 599, _state(locked=False)) == []
    assert _check(r, T + 600, _state(locked=False)) == ["GOES lock lost 10m"]
    assert _check(r, T + 610, _state(locked=False)) == []                 # said once
    assert _check(r, T + 900, _state(locked=True)) == ["GOES locked again"]
    assert _check(r, T + 910, _state(locked=True)) == []


def test_a_flap_is_not_an_outage():
    r = _reporter()
    for i in range(12):                                                    # 2 min down, 3 min up, for an hour
        down = i % 2 == 0
        assert _check(r, T + i * 300, _state(locked=not down)) == []
        assert _check(r, T + i * 300 + 120, _state(locked=not down)) == []


def test_pointing_mode_is_not_a_lost_lock():
    r = _reporter()
    pointing = _state(locked=False)
    pointing["mode"] = "point"
    for i in range(10):                                                    # half an hour of pointing
        assert _check(r, T + i * 180, pointing) == []
    assert _check(r, T + 1800, _state(locked=False)) == []                 # back to receive: the clock starts now
    assert _check(r, T + 2399, _state(locked=False)) == []
    assert _check(r, T + 2400, _state(locked=False)) == ["GOES lock lost 10m"]
    assert _check(r, T + 2500, pointing) == []                             # pointing to fix it
    assert _check(r, T + 2600, _state(locked=True)) == ["GOES locked again"]


def test_no_reading_is_no_evidence_about_the_lock():
    r = _reporter()
    assert _check(r, T, _state(locked=False)) == []
    for i in range(1, 200):                                                # pointing mode, then unreachable
        assert _check(r, T + i * 10, _state(stats=False)) == []
        assert _check(r, T + i * 10 + 5, None, reachable=False) == []
    # Still unlocked when the receiver reports again: the clock never stopped.
    assert _check(r, T + 2000, _state(locked=False)) == ["GOES lock lost 33m"]


def test_the_internet_backup_on_and_off_are_transitions_not_a_state():
    r = _reporter()
    assert _check(r, T, feed=_feed(on=True)) == []                         # the first look is a baseline
    assert _check(r, T + 10, feed=_feed(on=True)) == []
    assert _check(r, T + 20, feed=_feed(on=False, added=12)) == \
        ["internet backup off, dish is back (12 products came by internet)"]
    assert _check(r, T + 30, feed=_feed(on=False)) == []
    assert _check(r, T + 40, feed=_feed(on=True, quiet=14.2)) == ["internet backup on (dish quiet 14m)"]
    r2 = _reporter()
    _check(r2, T, feed=_feed(on=False))
    assert _check(r2, T + 10, feed=_feed(on=True, quiet=float("inf"))) == \
        ["internet backup on (no file from the dish yet)"]
    assert _check(StatusReporter(send=None), T, feed=None) == []


def test_products_behind_the_uplink_cross_an_hour_up_and_half_an_hour_down():
    r = _reporter()
    assert _check(r, T, feed=_feed(lag=45)) == []
    assert _check(r, T + 10, feed=_feed(lag=61)) == ["products ~61m behind the uplink"]
    assert _check(r, T + 20, feed=_feed(lag=300)) == []                    # said; worse is not news
    assert _check(r, T + 30, feed=_feed(lag=None)) == []
    assert _check(r, T + 40, feed=_feed(lag=59)) == []                     # not yet clear
    assert _check(r, T + 50, feed=_feed(lag=20)) == ["products current again (~20m behind)"]
    assert _check(r, T + 60, feed=_feed(lag=20)) == []
    assert _check(StatusReporter(send=None), T, feed=_feed(lag=180)) == \
        ["products ~3h behind the uplink"]


def test_the_dashboard_unreachable_five_minutes_and_back():
    r = _reporter()
    assert _check(r, T, None, reachable=False) == []
    assert _check(r, T + 299, None, reachable=False) == []
    assert _check(r, T + 300, None, reachable=False) == ["receiver dashboard unreachable 5m"]
    assert _check(r, T + 310, None, reachable=False) == []
    assert _check(r, T + 400) == ["receiver dashboard reachable again"]
    assert _check(r, T + 410) == []
    assert _check(r, T + 420, None, reachable=False) == []                 # a blip is nothing


def test_the_pi_disk_temperature_and_throttle_flags_make_one_line_and_one_all_clear():
    r = _reporter()
    assert _check(r, T, _state(disk=90)) == ["Pi: disk 90% full"]
    assert _check(r, T + 10, _state(disk=91, temp=72.4, throttled="0x50005")) == []   # one line until clear
    assert _check(r, T + 20, _state(disk=83, temp=68.0)) == []            # under the line, not yet normal
    assert _check(r, T + 30, _state(disk=70, temp=60.2)) == ["Pi back to normal (disk 70%, CPU 60C)"]
    assert _check(r, T + 40, _state(disk=70, temp=60.2)) == []
    r = _reporter()
    assert _check(r, T, _state(disk=90, temp=75.0, throttled="0x50005")) == \
        ["Pi: disk 90% full, CPU 75C, throttled 0x50005"]
    r = _reporter()
    assert _check(r, T, _state(throttled=None)) == []                      # an older dashboard: no flags
    assert _check(r, T + 10, _state(throttled="0x0")) == []
    assert _check(r, T + 15, _state(throttled="0x50000")) == []                # since-boot bits only: not now
    assert _check(r, T + 20, _state(throttled="0x80008")) == ["Pi: throttled 0x80008"]
    assert _check(r, T + 30, _state(throttled=None)) == ["Pi back to normal (disk 49%, CPU 51C)"]
    r = _reporter()
    assert _check(r, T, _state(temp=70.0)) == ["Pi: CPU 70C"]
    assert _check(r, T + 10, _state(temp=66.0)) == []
    assert _check(r, T + 20, _state(temp=64.9)) == ["Pi back to normal (disk 49%, CPU 65C)"]


# -- the rate limits ----------------------------------------------------------------


def test_the_same_condition_is_not_repeated_within_half_an_hour():
    r = _reporter()
    _check(r, T, None, reachable=False)
    assert _check(r, T + 300, None, reachable=False) == ["receiver dashboard unreachable 5m"]
    assert _check(r, T + 400) == ["receiver dashboard reachable again"]
    _check(r, T + 500, None, reachable=False)
    assert _check(r, T + 800, None, reachable=False) == []                 # 5 min down again, too soon
    assert _check(r, T + 300 + REPEAT_S - 10, None, reachable=False) == []
    assert _check(r, T + 300 + REPEAT_S, None, reachable=False) == ["receiver dashboard unreachable 26m"]
    # The all-clear has its own window: a second one inside it is dropped,
    # not held for later.
    assert _check(r, T + 300 + REPEAT_S + 10) == []
    assert r._down_said is False


def test_at_most_six_lines_an_hour_and_an_alert_waits_its_turn():
    r = _reporter()
    assert _check(r, T, feed=_feed(on=False)) == []
    got = []
    got += _check(r, T + 10, feed=_feed(on=True))
    got += _check(r, T + 20, feed=_feed(on=False, added=1))
    got += _check(r, T + 30, feed=_feed(lag=100))
    got += _check(r, T + 40, feed=_feed(lag=10))
    _check(r, T + 50, None, reachable=False)
    got += _check(r, T + 350, None, reachable=False)
    got += _check(r, T + 360)
    assert len(got) == PER_HOUR == 6
    # The seventh waits: the disk is still full at every poll, and the line
    # goes out the moment the oldest of the six is an hour old.
    assert _check(r, T + 370, _state(disk=95)) == []
    assert _check(r, T + 3609, _state(disk=95)) == []
    assert _check(r, T + 3610, _state(disk=95)) == ["Pi: disk 95% full"]
    assert _check(r, T + 3620, _state(disk=95)) == []


# -- posting --------------------------------------------------------------------------


def test_tick_keeps_lines_short_and_respects_transmit(monkeypatch):
    sent = []

    async def send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(settings, "tx_enabled", True)
    r = StatusReporter(send=send)
    assert asyncio.run(r.tick(T, _state(locked=False), True, _feed())) == []
    # Two changes in one poll are one line, when they fit.
    out = asyncio.run(r.tick(T + 600, _state(locked=False), True, _feed(on=True, quiet=11)))
    assert out == sent == ["status: GOES lock lost 10m; internet backup on (dish quiet 11m)"]
    out = asyncio.run(r.tick(T + 620, _state(locked=False), True, _feed(on=False, added=2)))
    assert out == ["status: internet backup off, dish is back (2 products came by internet)"]
    assert all(len(line) <= MAX_LEN for line in sent) and [line for _, line in r.recent] == sent

    # ... and when they do not, two lines, in a fixed order (the limit lowered
    # here: with the short "status:" prefix these three fit in one).
    import meshcore_weather.status_channel as status_channel
    monkeypatch.setattr(status_channel, "MAX_LEN", 80)
    two = StatusReporter(send=send)
    _check(two, T, _state(locked=False), feed=_feed(lag=None))
    out = asyncio.run(two.tick(T + 600, _state(locked=False, disk=90, temp=75.0, throttled="0x50005"), True,
                               _feed(lag=600)))
    assert out == ["status: GOES lock lost 10m; products ~10h behind the uplink",
                   "status: Pi: disk 90% full, CPU 75C, throttled 0x50005"]
    sent.clear()

    monkeypatch.setattr(settings, "tx_enabled", False)
    assert asyncio.run(r.tick(T + 700, _state(locked=True), True, _feed())) == []
    assert sent == []                                                      # nothing went to the sender
    monkeypatch.setattr(settings, "tx_enabled", True)
    assert asyncio.run(r.tick(T + 710, _state(locked=True), True, _feed())) == []   # the clear was spent

    async def refused(text):
        return False
    r = StatusReporter(send=refused)
    _check(r, T, feed=_feed(on=False))
    assert asyncio.run(r.tick(T + 10, _state(), True, _feed(on=True))) == [] and list(r.recent) == []

    # No name of its own in the line: the firmware already puts the node's
    # name before every channel message ("WX-AUS: status: ...").
    plain = StatusReporter(send=send)
    _check(plain, T, feed=_feed(on=False))
    (line,) = asyncio.run(plain.tick(T + 10, _state(), True, _feed(on=True, quiet=12)))
    assert line == "status: internet backup on (dish quiet 12m)"


def test_the_bot_never_posts_status_on_the_request_channel():
    sent = []

    class Radio:
        channel_idx, data_channel_idx = 1, 1
        status_channel_idx = None

        async def send_channel_message(self, channel, text, ev=None):
            sent.append((channel, text))
            return True

    bot = WeatherBot()
    bot.radio = Radio()
    assert asyncio.run(bot._post_status("WX status: x")) is False          # no slot of its own
    bot.radio.status_channel_idx = 1
    assert asyncio.run(bot._post_status("WX status: x")) is False          # the request channel
    bot.radio.status_channel_idx = 3
    assert asyncio.run(bot._post_status("WX status: x")) is True
    assert sent == [(3, "WX status: x")]


def test_the_radio_sends_on_its_status_slot_and_on_no_other(monkeypatch):
    from meshcore_weather.meshcore import radio as radio_mod
    monkeypatch.setattr(settings, "tx_enabled", True)
    r = radio_mod.MeshcoreRadio()
    calls = []

    class Commands:
        async def send_chan_msg(self, channel, text, timestamp=None):
            calls.append(channel)
            return SimpleNamespace(type=radio_mod.EventType.OK, payload={})

        async def get_channel(self, idx):
            return SimpleNamespace(type=radio_mod.EventType.CHANNEL_INFO,
                                   payload={"channel_name": "#x", "channel_secret": b""})

    r._mc = SimpleNamespace(commands=Commands(), self_info={"name": "WX-AUS"})
    r._channel_idx, r._data_channel_idx, r._status_channel_idx = 1, 1, 3
    r._channel_secrets = {}
    monkeypatch.setattr(radio_mod.delivery_tracker, "track", lambda outbound: None)
    assert r.status_channel_idx == 3 and r._role_for(3) == "status"
    assert asyncio.run(r.send_channel_message(3, "status: GOES locked again")) is True
    assert asyncio.run(r.send_channel_message(2, "nope")) is False
    assert asyncio.run(r.send_channel_message(0, "nope")) is False
    assert calls == [3]
    with pytest.raises(ValueError):
        asyncio.run(r.assign_role("status", settings.meshcore_channel))


def test_nothing_is_looked_at_until_the_node_has_the_status_slot():
    """Seen on WX-AUS at a restart (17 Oct): the first poll came before the
    radio had its status slot, the line failed, and the reporter counted it
    as said, so it would never be posted for real."""
    sent: list[str] = []

    async def send(line: str) -> bool:
        sent.append(line)
        return True

    ready = {"slot": False}
    r = StatusReporter(send=send, ready=lambda: ready["slot"])
    assert asyncio.run(r.tick(T, _state(), True, _feed(lag=180))) == []
    ready["slot"] = True
    assert asyncio.run(r.tick(T + 10, _state(), True, _feed(lag=180))) == ["status: products ~3h behind the uplink"]
