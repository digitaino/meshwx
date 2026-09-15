"""Echo/ACK tracking and the retransmit budget (meshcore/delivery.py)."""

import asyncio
import hashlib
import hmac

import pytest
from Crypto.Cipher import AES

from meshcore_weather.config import settings
from meshcore_weather.meshcore import delivery
from meshcore_weather.meshcore.delivery import (
    DeliveryTracker, Outbound, build_channel_payload, fmt_path, packet_hash, parse_packet,
)

# A real public-channel packet heard by an Austin observer (scope.digitaino.com),
# with the hash CoreScope computed for it. The public channel key is well known.
PUBLIC_SECRET = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")
RAW_HEX = "1502FA1E11788C08C69813A8D889455DFC95BBCA2D2B1021253B3F150289A50615A77F2FA8BA19AC043E85073F37E7C7417A6D946F332F"
SCOPE_HASH = "d87fe7c56048b751"


def test_we_can_rebuild_a_real_channel_packet_byte_for_byte():
    pkt = parse_packet(bytes.fromhex(RAW_HEX))
    assert pkt["route"] == 1 and pkt["ptype"] == 5 and pkt["path_len"] == 2 and pkt["path"] == "fa1e"
    payload = pkt["payload"]
    # Decrypt the real thing to learn what was said, then rebuild it ourselves.
    ct = payload[3:]
    assert hmac.new(PUBLIC_SECRET, ct, hashlib.sha256).digest()[:2] == payload[1:3]
    plain = AES.new(PUBLIC_SECRET, AES.MODE_ECB).decrypt(ct)
    ts, flags, text = int.from_bytes(plain[:4], "little"), plain[4], plain[5:].rstrip(b"\0").decode()
    name, said = text.split(": ", 1)
    assert build_channel_payload(PUBLIC_SECRET, name, said, ts, flags) == payload
    assert packet_hash(5, payload) == SCOPE_HASH          # the hash every node dedupes on
    assert fmt_path(pkt["path"]) == "FA,1E"


def test_parse_packet_rejects_garbage():
    assert parse_packet(b"") is None and parse_packet(b"\x15") is None
    assert parse_packet(bytes.fromhex("15 05 aa")) is None    # path longer than the packet


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(settings, "retransmit_max", 1)
    monkeypatch.setattr(settings, "retransmit_per_hour", 30)
    monkeypatch.setattr(settings, "mesh_quiet_s", 600)
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "scope_url", "")
    monkeypatch.setattr(delivery.random, "uniform", lambda a, b: 0.0)
    return DeliveryTracker()


def _outbound(sends, h="ab" * 8, **kw):
    async def resend(attempt):
        sends.append(attempt)
        return True
    return Outbound(kind="channel_text", hash=h, resend=resend, window_s=0.05, **kw)


def _track(tracker, ob):
    async def run():
        await tracker.track(ob)
    asyncio.run(run())


def _echo_raw(payload: bytes, path: bytes = b"\xd0\x3a") -> bytes:
    return bytes([0x15]) + bytes([len(path)]) + path + payload     # FLOOD, GRP_TXT, repeated once


def test_echo_within_the_window_means_no_retransmit(fast):
    sends, payload = [], b"\x11\x00\x00" + b"\x00" * 16
    ob = _outbound(sends, packet_hash(5, payload), ev={"delivery": None})

    async def run():
        task = fast.track(ob)
        await asyncio.sleep(0.01)
        fast.on_rx_log(_echo_raw(payload), snr=7.5)
        await task
    asyncio.run(run())
    d = fast.outcome(ob)
    assert sends == [] and d["result"] == "echoed" and d["via"] == "D0,3A" and d["snr"] == 7.5
    assert ob.ev["delivery"]["echo"] is True and fast.stats()["windows"]["1h"]["heard"] == 1


def test_no_echo_means_exactly_one_identical_retransmit(fast):
    sends = []
    ob = _outbound(sends)
    _track(fast, ob)
    d = fast.outcome(ob)
    assert sends == [1] and d["result"] == "no_echo" and d["resent"] == 1 and d["attempts"] == 2
    assert fast.stats()["windows"]["24h"]["resent"] == 1 and fast.stats()["windows"]["24h"]["heard"] == 0


def test_measure_only_mode_never_retransmits(fast, monkeypatch):
    monkeypatch.setattr(settings, "retransmit_max", 0)
    sends = []
    ob = _outbound(sends)
    _track(fast, ob)
    assert sends == [] and fast.outcome(ob)["result"] == "no_echo"


def test_budgets_and_a_quiet_mesh_skip_the_retransmit(fast, monkeypatch):
    monkeypatch.setattr(settings, "tx_enabled", False)
    sends = []
    ob = _outbound(sends)
    _track(fast, ob)
    assert sends == [] and ob.skipped == "tx off"

    monkeypatch.setattr(settings, "tx_enabled", True)
    fast.started_at -= 10000                     # running for a long time, never heard a repeat
    ob = _outbound(sends)
    _track(fast, ob)
    assert sends == [] and ob.skipped.startswith("mesh quiet")

    fast.on_rx_log(_echo_raw(b"\x99" * 20))      # somebody's packet, repeated: the mesh is alive
    monkeypatch.setattr(settings, "retransmit_per_hour", 1)
    _track(fast, _outbound(sends))
    assert sends == [1]
    ob = _outbound(sends)
    _track(fast, ob)
    assert sends == [1] and ob.skipped == "budget: hourly retransmits spent"


def test_dm_uses_the_ack_and_re_registers_the_new_code(fast):
    sends, gave_up = [], []

    async def resend(attempt):
        sends.append(attempt)
        return "beef0002"                         # the firmware expects a new ack code now

    async def give_up():
        gave_up.append(True)

    ob = Outbound(kind="dm", hash=None, ack="beef0001", resend=resend, window_s=0.05, give_up=give_up)

    async def run():
        task = fast.track(ob)
        await asyncio.sleep(0.08)                # first window passes, retransmit goes out
        fast.on_ack("beef0001")                  # stale code: ignored
        fast.on_ack("beef0002")                  # the retransmit's ack
        await task
    asyncio.run(run())
    d = fast.outcome(ob)
    assert sends == [1] and d["result"] == "acked" and d["resent"] == 1 and gave_up == []

    ob2 = Outbound(kind="dm", hash=None, ack="c0de", resend=resend, window_s=0.05, give_up=give_up)
    _track(fast, ob2)
    assert fast.outcome(ob2)["result"] == "no_ack" and gave_up == [True]   # the path gets reset
