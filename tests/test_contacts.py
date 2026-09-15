"""Contact table housekeeping: what gets removed from the node and what never does."""

import asyncio
import time

from meshcore import EventType

from meshcore_weather.config import settings
from meshcore_weather.meshcore.delivery import (
    PAYLOAD_GRP_DATA,
    build_channel_data_payload,
    delivery_tracker,
    packet_hash,
)
from meshcore_weather.meshcore.radio import MeshcoreRadio, plan_contact_removals

NOW = time.time()


def _c(name, ctype, heard_h_ago=1.0):
    return {"adv_name": name, "type": ctype, "lastmod": NOW - heard_h_ago * 3600}


def test_non_clients_go_and_protected_ones_stay():
    contacts = {
        "aa" * 32: _c("Digitaino", 1),
        "bb" * 32: _c("Barton Creek Solar", 2),
        "cc" * 32: _c("LCC Room", 3),
        "dd" * 32: _c("Some Sensor", 4),
        "ee" * 32: _c("WX-SAT", 1),
        "ff" * 32: _c("Admin Phone", 1),
        "11" * 32: _c("WX-DFW repeater?", 2),          # peer prefix protects even a non-client
        "99" * 32: _c("WX-AUS", 1),                     # ourselves
    }
    plan = plan_contact_removals(contacts, 100, 10, "ff" * 6, "WX-", own_key="99" * 32)
    removed = {name for _, name, _ in plan}
    assert removed == {"Barton Creek Solar", "LCC Room", "Some Sensor"}
    reasons = {name: r for _, name, r in plan}
    assert "repeater" in reasons["Barton Creek Solar"] and "room" in reasons["LCC Room"]


def test_people_heard_longest_ago_make_room_near_the_limit():
    contacts = {f"{i:02x}" * 32: _c(f"Person{i}", 1, heard_h_ago=i) for i in range(1, 21)}
    contacts["ad" * 32] = _c("Admin", 1, heard_h_ago=500)      # oldest of all, but protected
    contacts["ee" * 32] = _c("WX-SAT", 1, heard_h_ago=400)
    plan = plan_contact_removals(contacts, slots=20, keep_free=5, admin_key="ad" * 6, peer_prefix="WX-")
    names = [name for _, name, _ in plan]
    # 22 people, limit 15: the 7 heard longest ago among the unprotected go
    assert names == [f"Person{i}" for i in range(20, 13, -1)]
    assert all("heard longest ago" in r for _, _, r in plan)
    assert "Admin" not in names and "WX-SAT" not in names


def test_nothing_to_do_is_an_empty_plan():
    assert plan_contact_removals({"aa" * 32: _c("Digitaino", 1)}, 100, 10, "", "WX-") == []
    assert plan_contact_removals({}, 100, 10, "", "WX-") == []


class _Res:
    def __init__(self, ok=True):
        self.type = EventType.OK if ok else EventType.ERROR
        self.payload = None if ok else "nope"


class FakeCommands:
    """The node's own table, and the library's cache that only ever merges."""

    def __init__(self, mc, node):
        self.mc = mc
        self.node = node
        self.removed = []
        self.reloads = 0

    async def remove_contact(self, key):
        self.removed.append(key)
        self.node.pop(key, None)          # the node forgets it; the cache does not
        return _Res()

    async def get_contacts(self, lastmod=0):
        self.reloads += 1
        for k, c in self.node.items():
            self.mc._contacts.setdefault(k, c)
        return _Res()


class FakeMC:
    def __init__(self, contacts):
        self._contacts = dict(contacts)
        self._lastmod = 5
        self.self_info = {"public_key": "99" * 32, "name": "WX-AUS"}
        self.commands = FakeCommands(self, dict(contacts))

    @property
    def contacts(self):
        return self._contacts


def test_housekeeping_runs_the_plan_and_reports(monkeypatch):
    monkeypatch.setattr(settings, "contact_housekeeping", True)
    monkeypatch.setattr(settings, "admin_key", "")
    r = MeshcoreRadio()
    r._mc = FakeMC({"aa" * 32: _c("Digitaino", 1), "bb" * 32: _c("Repeater", 2), "cc" * 32: _c("Room", 3)})
    out = asyncio.run(r.housekeep_contacts())
    assert out["removed"] == 2 and out["kept"] == 1 and out["people"] == 1
    assert set(r._mc.commands.removed) == {"bb" * 32, "cc" * 32} and r._mc.commands.reloads == 1
    assert set(r._mc.contacts) == {"aa" * 32}                  # cache reloaded from the node, not merged
    assert "removed 2, 1 left (1 people) of 100 slots" == out["note"]


def test_housekeeping_off_touches_nothing(monkeypatch):
    monkeypatch.setattr(settings, "contact_housekeeping", False)
    r = MeshcoreRadio()
    r._mc = FakeMC({"bb" * 32: _c("Repeater", 2)})
    asyncio.run(r.housekeep_contacts())
    assert r._mc.commands.removed == [] and "bb" * 32 in r._mc.contacts


class EventMC(FakeMC):
    def __init__(self, contacts):
        super().__init__(contacts)
        self._pending_contacts = {}

    async def ensure_contacts(self, follow=False):
        pass


class _Ev:
    def __init__(self, payload):
        self.payload = payload


def test_new_contacts_and_readverts_reach_the_bot_only_for_companions():
    r = MeshcoreRadio()
    seen = []

    async def handler(name, prefix):
        seen.append((name, prefix))

    r.on_advert(handler)
    r._mc = EventMC({"aa" * 32: {**_c("Digitaino", 1), "public_key": "aa" * 32},
                     "bb" * 32: {**_c("Old Repeater", 2), "public_key": "bb" * 32}})
    # PUSH_CODE_NEW_ADVERT: a newcomer, a repeater the node did not store, a room
    asyncio.run(r._on_new_contact(_Ev({"public_key": "cc" * 32, "adv_name": "Newcomer", "type": 1})))
    asyncio.run(r._on_new_contact(_Ev({"public_key": "dd" * 32, "adv_name": "Some Repeater", "type": 2})))
    asyncio.run(r._on_new_contact(_Ev({"public_key": "ee" * 32, "adv_name": "A Room", "type": 3})))
    # PUSH_CODE_ADVERT: known contacts adverting again
    asyncio.run(r._on_advert(_Ev({"public_key": "aa" * 32})))
    asyncio.run(r._on_advert(_Ev({"public_key": "bb" * 32})))
    assert seen == [("Newcomer", "cc" * 6), ("Digitaino", "aa" * 6)]
    assert r._mc._pending_contacts == {}


# -- GRP_DATA send path -------------------------------------------------------
#
# meshcore-py has no helper for CMD_SEND_CHANNEL_DATA, so the frame is built by
# hand: [62][channel idx][path_len 0xFF = flood][data_type u16 LE][data].


class DataCommands:
    """Captures the raw frames the radio hands to the companion."""

    def __init__(self, secret):
        self.secret = secret
        self.frames = []

    async def get_channel(self, idx):
        ev = _Res()
        ev.type = EventType.CHANNEL_INFO
        ev.payload = {"channel_name": "#meshwx", "channel_secret": self.secret}
        return ev

    async def send(self, frame, expect):
        self.frames.append(bytes(frame))
        return _Res()


class DataMC:
    def __init__(self, secret):
        self.commands = DataCommands(secret)
        self.self_info = {"name": "WX-AUS", "public_key": "99" * 32}


SECRET = bytes(range(16))


def _data_radio(monkeypatch, secret=SECRET):
    """A radio whose text and data roles share slot 1, with the tracker stubbed
    out so the Outbound can be inspected without a live retransmit task."""
    r = MeshcoreRadio()
    r._mc = DataMC(secret)
    r._channel_idx = r._data_channel_idx = 1
    r._channel_secrets = {}
    tracked = []
    monkeypatch.setattr(delivery_tracker, "track", tracked.append)
    return r, tracked


def test_send_channel_data_builds_the_firmware_frame_and_tracks_the_hash(monkeypatch):
    monkeypatch.setattr(settings, "tx_enabled", True)
    r, tracked = _data_radio(monkeypatch)

    async def go():
        assert await r.send_channel_data(b"\x01\x02") is True
        # the retransmit must be the identical frame: every node that already
        # has it drops it, so nobody sees the datagram twice
        assert await tracked[0].resend(1) is True

    asyncio.run(go())
    frame = bytes([62, 1, 0xFF, 0x10, 0xFF]) + b"\x01\x02"
    assert r._mc.commands.frames == [frame, frame]
    ob = tracked[0]
    assert ob.kind == "channel_data" and ob.ptype == PAYLOAD_GRP_DATA
    assert ob.hash == packet_hash(PAYLOAD_GRP_DATA,
                                  build_channel_data_payload(SECRET, 0xFF10, b"\x01\x02"))


def test_send_channel_data_refuses_more_than_165_bytes(monkeypatch):
    monkeypatch.setattr(settings, "tx_enabled", True)
    r, tracked = _data_radio(monkeypatch)
    assert asyncio.run(r.send_channel_data(b"x" * 166)) is False
    assert r._mc.commands.frames == [] and tracked == []
    assert asyncio.run(r.send_channel_data(b"x" * 165)) is True


def test_send_channel_data_is_silent_with_tx_off_and_on_slot_0(monkeypatch):
    monkeypatch.setattr(settings, "tx_enabled", False)
    r, tracked = _data_radio(monkeypatch)
    assert asyncio.run(r.send_channel_data(b"\x01")) is False
    monkeypatch.setattr(settings, "tx_enabled", True)
    r._data_channel_idx = 0                       # the public channel, never ours
    assert asyncio.run(r.send_channel_data(b"\x01")) is False
    r._data_channel_idx = None                    # broadcasts off
    assert asyncio.run(r.send_channel_data(b"\x01")) is False
    assert r._mc.commands.frames == [] and tracked == []
