"""The send path after the 1 October review of the bot's resends.

That day 60 datagrams of 161-165 bytes had no echo at all (the companion
node never reports a repeater's copy of them), 11 resends went out for
packets the mesh had already carried and 22 for packets nobody ever heard,
the hourly resend budget ran out on them, a multi-packet answer's row showed
one packet's result, resends went out between the packets of a batch with
no spacing, and the bot logged resends while its USB link was down.
"""

import asyncio
import time

import pytest
from meshcore import EventType

from meshcore_weather.config import settings
from meshcore_weather.main import channel_fit, reply_not_before
from meshcore_weather.meshcore import delivery, health
from meshcore_weather.meshcore import radio as radio_mod
from meshcore_weather.meshcore.delivery import (
    DeliveryTracker, Outbound, batch_summary, build_channel_data_payload, build_channel_payload,
    echo_visible, packet_hash,
)
from meshcore_weather.meshcore.radio import MeshcoreRadio

SECRET = bytes(range(16))


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(settings, "retransmit_max", 1)
    monkeypatch.setattr(settings, "retransmit_per_hour", 30)
    monkeypatch.setattr(settings, "mesh_quiet_s", 600)
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "scope_url", "")
    monkeypatch.setattr(delivery.random, "uniform", lambda a, b: 0.0)
    return DeliveryTracker()


def _ob(sends, h="ab" * 8, result=True, **kw):
    async def resend(attempt):
        sends.append(attempt)
        return result
    kw.setdefault("window_s", 0.05)
    return Outbound(kind="channel_data", hash=h, resend=resend, **kw)


def _echo(payload: bytes) -> bytes:
    return bytes([0x19, 0x41, 0xAB, 0xBA]) + payload        # FLOOD GRP_DATA, one hop at two bytes


# -- what the node can report ----------------------------------------------------------


def test_a_datagram_of_157_bytes_can_be_echoed_and_one_of_158_cannot():
    for n, seen in ((6, True), (157, True), (158, False), (165, False)):
        payload = build_channel_data_payload(SECRET, 0xFF10, bytes(n))
        assert echo_visible(len(payload)) is seen, n
        assert echo_visible(len(payload), 3) is seen, n             # three-byte hashes too
    assert len(build_channel_data_payload(SECRET, 0xFF10, bytes(157))) == 163
    assert len(build_channel_data_payload(SECRET, 0xFF10, bytes(158))) == 179


def test_a_channel_message_within_the_text_budget_can_be_echoed():
    """153 - len(name) bytes of text: 147 for WX-AUS. One byte more is a
    block more of ciphertext, and the node never reports the echo."""
    assert echo_visible(len(build_channel_payload(SECRET, "WX-AUS", "x" * 147, 1)))
    assert not echo_visible(len(build_channel_payload(SECRET, "WX-AUS", "x" * 148, 1)))


def test_no_echo_possible_and_no_corescope_means_no_blind_resend(fast):
    sends = []
    ob = _ob(sends, echo_visible=False)
    asyncio.run(_run(fast, ob))
    assert sends == [] and ob.skipped == "too long for the node to report an echo"


async def _run(tracker, ob):
    await tracker.track(ob)


# -- resends that never happened are not counted ---------------------------------------


def test_a_resend_the_radio_could_not_send_is_not_a_resend(fast):
    """12:27 to 12:32 on 1 October the radio fell off USB four times and the
    log showed resends that never went out."""
    sends = []
    ob = _ob(sends, result=False)
    asyncio.run(_run(fast, ob))
    d = fast.outcome(ob)
    assert sends == [1] and d["resent"] == 0 and d["attempts"] == 1 and ob.skipped == "radio unavailable"
    assert len(fast._resends) == 0                                   # the hourly budget is untouched


def test_a_packet_heard_while_its_resend_waited_its_turn_stays_home(fast):
    sends, payload = [], b"\x51\x00\x00" + bytes(16)
    h = packet_hash(6, payload)

    async def resend(attempt):                     # the echo arrives while it waits at the radio
        fast.on_rx_log(_echo(payload))
        assert fast.heard(h)
        sends.append(attempt)
        return None

    ob = Outbound(kind="channel_data", hash=h, resend=resend, window_s=0.05)
    asyncio.run(_run(fast, ob))
    d = fast.outcome(ob)
    assert d["result"] == "echoed" and d["resent"] == 0 and len(fast._resends) == 0


def test_the_echo_is_timed_from_when_the_resend_went_on_air(fast):
    """The resend can wait seconds for its turn; timing from when it was
    asked for made the echo look quicker, or negative."""
    sends, payload = [], b"\x52\x00\x00" + bytes(16)
    h = packet_hash(6, payload)

    async def resend(attempt):
        await asyncio.sleep(0.1)                   # its turn on the air
        fast.on_tx(h)
        sends.append(time.time())
        return True

    ob = Outbound(kind="channel_data", hash=h, resend=resend, window_s=0.05)

    async def go():
        task = fast.track(ob)
        while not sends:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.03)
        fast.on_rx_log(_echo(payload))
        await task

    asyncio.run(go())
    d = fast.outcome(ob)
    assert d["echo"] and d["resent"] == 1 and 0 <= d["echo_ms"] < 80


# -- late echoes -------------------------------------------------------------------------


def test_an_echo_after_the_wait_still_counts_and_says_it_was_late(fast, monkeypatch):
    monkeypatch.setattr(settings, "retransmit_max", 0)
    payload = b"\x53\x00\x00" + bytes(16)
    ev = {"delivery": None}
    ob = _ob([], packet_hash(6, payload), ev=ev)

    async def go():
        await fast.track(ob)
        assert ev["delivery"]["result"] == "no_echo"
        fast.on_rx_log(_echo(payload))                # 9 s later, say

    asyncio.run(go())
    assert ev["delivery"]["result"] == "echoed" and ev["delivery"]["late"] is True
    row = fast.recent_outcomes()[-1]
    assert row[2] is True and row[6] == {"late": True}
    assert health.unheard_streak([row]) == 0


# -- CoreScope ---------------------------------------------------------------------------


def test_one_observer_of_a_repeat_is_enough_and_counts_as_heard(fast, monkeypatch):
    monkeypatch.setattr(settings, "scope_url", "https://scope.example")
    monkeypatch.setattr(settings, "scope_mode", "decide")
    monkeypatch.setattr(settings, "scope_min_observers", 1)
    asked = []

    async def lookup(url, h, ptype, since=None):
        asked.append(since)
        return {"observers": 1, "repeated_by": 1, "direct_by": 0, "paths": ["ABBA"]}

    monkeypatch.setattr(delivery, "scope_lookup", lookup)
    sends = []
    ob = _ob(sends)
    asyncio.run(_run(fast, ob))
    assert sends == [] and ob.skipped == "CoreScope: 1 observer heard a repeat" and ob.carried == "scope"
    assert asked and asked[0] == ob.sent_at                         # observations before the send are not ours
    rows = fast.recent_outcomes() * 3
    assert health.unheard_streak(rows) == 0 and fast.stats()["windows"]["1h"]["carried"] == 1


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_the_lookup_is_by_hash_and_drops_observations_of_an_earlier_packet(monkeypatch):
    import httpx
    calls = []
    sent = 1790882821.0                                              # 2026-10-01 19:27:01Z
    data = {"packets": [{"hash": "df8d", "observations": [
        {"observer_id": "A", "path_json": '["ABBA"]', "timestamp": "2026-10-01T19:27:02.000Z"},
        {"observer_id": "B", "path_json": "[]", "timestamp": "2026-10-01T19:27:03Z"},
        {"observer_id": "C", "path_json": '["ABBA"]', "timestamp": "2026-10-01T15:02:00Z"},   # hours before
    ]}]}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append((url, params))
            return _FakeResponse(data)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    got = asyncio.run(delivery.scope_lookup("https://scope.example/", "df8d", 6, since=sent))
    assert got == {"observers": 2, "repeated_by": 1, "direct_by": 1, "paths": ["ABBA"]}
    assert calls == [("https://scope.example/api/packets", {"hash": "df8d", "expand": "observations", "limit": "5"})]
    all_of_them = asyncio.run(delivery.scope_lookup("https://scope.example", "df8d", 6))
    assert all_of_them["observers"] == 3
    data["packets"][0]["observations"] = data["packets"][0]["observations"][2:]
    assert asyncio.run(delivery.scope_lookup("https://scope.example", "df8d", 6, since=sent)) is None


# -- a multi-packet answer's row ---------------------------------------------------------


def test_each_packet_of_an_answer_keeps_its_own_result(fast, monkeypatch):
    monkeypatch.setattr(settings, "retransmit_max", 0)
    ev = {"delivery": None}
    payloads = [bytes([0x60 + i, 0, 0]) + bytes(16) for i in range(3)]
    obs = [_ob([], packet_hash(6, p), ev=ev) for p in payloads]

    async def go():
        tasks = [fast.track(o) for o in obs]
        await asyncio.sleep(0.01)
        fast.on_rx_log(_echo(payloads[0]))
        fast.on_rx_log(_echo(payloads[2]))
        await asyncio.gather(*tasks)

    asyncio.run(go())
    d = ev["delivery"]
    assert d["result"] == "batch" and d["packets"] == 3 and d["heard"] == 2 and d["pending"] == 0
    assert [p["result"] for p in d["parts"]] == ["echoed", "no_echo", "echoed"]


def test_one_packet_events_keep_the_plain_outcome(fast):
    ev = {"delivery": None}
    payload = b"\x70\x00\x00" + bytes(16)
    ob = _ob([], packet_hash(6, payload), ev=ev)

    async def go():
        task = fast.track(ob)
        await asyncio.sleep(0.01)
        fast.on_rx_log(_echo(payload))
        await task

    asyncio.run(go())
    assert ev["delivery"]["result"] == "echoed" and "parts" not in ev["delivery"]


def test_batch_summary_counts_a_corescope_repeat_as_heard():
    s = batch_summary([{"result": "no_echo", "echo": False, "resent": 1, "observed_repeats": 3},
                       {"result": "echoed", "echo": True, "resent": 0}, None])
    assert s["heard"] == 2 and s["pending"] == 1 and s["resent"] == 1 and s["parts"][2] is None


# -- the radio ---------------------------------------------------------------------------


class _Res:
    def __init__(self, ok=True):
        self.type = EventType.OK if ok else EventType.ERROR
        self.payload = None if ok else "nope"


class Commands:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def get_channel(self, idx):
        ev = _Res()
        ev.type = EventType.CHANNEL_INFO
        ev.payload = {"channel_name": "#meshwx", "channel_secret": SECRET}
        return ev

    async def send(self, frame, expect):
        self.calls.append(("data", time.monotonic(), bytes(frame)))
        return _Res(self.ok)

    async def send_chan_msg(self, channel, text, timestamp=None):
        self.calls.append(("text", time.monotonic(), text, int.from_bytes(timestamp, "little")))
        return _Res(self.ok)


class MC:
    def __init__(self, ok=True):
        self.commands = Commands(ok)
        self.self_info = {"name": "WX-AUS", "public_key": "99" * 32}


def _radio(monkeypatch, ok=True):
    monkeypatch.setattr(settings, "tx_enabled", True)
    r = MeshcoreRadio()
    r._mc = MC(ok)
    r._channel_idx = r._data_channel_idx = 1
    r._channel_secrets = {}
    tracked = []
    monkeypatch.setattr(radio_mod.delivery_tracker, "track", tracked.append)
    return r, tracked


def test_every_send_waits_its_turn_two_seconds_after_the_last(monkeypatch):
    """A resend used to go out half a second after the next packet of the
    same answer, and text replies never waited at all."""
    monkeypatch.setattr(radio_mod, "TX_GAP_S", 0.2)
    r, tracked = _radio(monkeypatch)

    async def go():
        await r.send_channel_data(b"\x01")
        await asyncio.gather(tracked[0].resend(1), r.send_channel_message(1, "wx austin"), r.send_channel_data(b"\x02"))

    asyncio.run(go())
    times = [c[1] for c in r._mc.commands.calls]
    assert len(times) == 4
    assert all(b - a >= 0.19 for a, b in zip(times, times[1:]))


def test_a_refused_text_send_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setattr(radio_mod, "TX_GAP_S", 0)
    r, tracked = _radio(monkeypatch, ok=False)
    assert asyncio.run(r.send_channel_message(1, "wx austin")) is False and tracked == []
    assert asyncio.run(r.send_channel_data(b"\x01")) is False and tracked == []


def test_no_resend_once_the_link_is_lost(monkeypatch):
    monkeypatch.setattr(radio_mod, "TX_GAP_S", 0)
    r, tracked = _radio(monkeypatch)

    async def go():
        assert await r.send_channel_message(1, "wx austin") is True
        assert await r.send_channel_data(b"\x01") is True
        r._lost = True
        return [await ob.resend(1) for ob in tracked]

    assert asyncio.run(go()) == [False, False]
    assert len(r._mc.commands.calls) == 2


def test_two_replies_with_one_text_in_one_second_are_different_packets(monkeypatch):
    """Same text, same second: the same packet, and every node drops the
    second as a copy of the first."""
    monkeypatch.setattr(radio_mod, "TX_GAP_S", 0)
    r, tracked = _radio(monkeypatch)

    async def go():
        await r.send_channel_message(1, "Austin, TX: 88F")
        await r.send_channel_message(1, "Austin, TX: 88F")

    asyncio.run(go())
    stamps = [c[3] for c in r._mc.commands.calls]
    assert stamps[1] == stamps[0] + 1 and tracked[0].hash != tracked[1].hash


def test_the_text_budget_is_bytes(monkeypatch):
    """A reply with a degree sign or accents used to fit by characters and
    not by bytes: the firmware clipped it, or the node never heard its echo."""
    monkeypatch.setattr(radio_mod, "TX_GAP_S", 0)
    r, tracked = _radio(monkeypatch)
    assert r.channel_text_budget() == 147
    asyncio.run(r.send_channel_message(1, "é" * 100))
    sent = r._mc.commands.calls[0][2]
    assert len(sent.encode()) <= 147 and tracked[0].echo_visible
    fit = channel_fit("; ".join(f"Bogotá {i}°" for i in range(40)), 147)
    assert len(fit.encode()) <= 147 and fit.endswith("… DM me for all")


# -- answering a relayed request ---------------------------------------------------------


def test_an_answer_to_a_relayed_request_waits_and_a_direct_one_does_not(monkeypatch):
    monkeypatch.setattr(settings, "relayed_reply_delay_s", 1.0)
    assert reply_not_before(0, 100.0) == 100.0
    assert reply_not_before(None, 100.0) == 100.0
    for hops in (1, 3):
        t = reply_not_before(hops, 100.0)
        assert 101.0 <= t <= 101.5
    monkeypatch.setattr(settings, "relayed_reply_delay_s", 0.0)
    assert reply_not_before(2, 100.0) == 100.0


@pytest.mark.asyncio
async def test_the_scheduler_holds_the_first_packet_until_not_before(monkeypatch):
    from unittest.mock import MagicMock

    import meshcore_weather.schedule.scheduler as sched_mod
    from meshcore_weather.parser.weather import WeatherStore
    from meshcore_weather.protocol import v5
    monkeypatch.setattr(sched_mod, "TX_SPACING", 0)
    radio = MagicMock()
    radio._mc = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    at = []

    async def send(data, data_type=0xFF10, ev=None):
        at.append(time.monotonic())
        return True

    radio.send_channel_data = send
    s = sched_mod.Scheduler(WeatherStore(), radio)
    t0 = time.monotonic()
    await s.transmit([v5.encode_cancel(7, 1, event=3, office=35, etn=1)], "t", not_before=t0 + 0.15)
    assert at[0] - t0 >= 0.14
