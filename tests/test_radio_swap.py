"""Radio hardware swaps: the node profile (meshcore/profile.py), adoption
through the radio (meshcore/radio.py), port discovery, the health verdict
(meshcore/health.py), delivery outcome persistence and the portal routes."""

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from meshcore import EventType

from meshcore_weather.config import settings
from meshcore_weather.meshcore import health, profile, radio as radio_mod
from meshcore_weather.meshcore.delivery import DeliveryTracker

KEY_A = "1d" * 32
KEY_B = "2e" * 32
PRIV_A = "aa" * 64


class _Res:
    def __init__(self, type_, payload=None):
        self.type, self.payload = type_, payload or {}


class FakeCommands:
    """The companion command set, remembering what was written."""

    def __init__(self, node):
        self.node = node
        self.calls = []

    async def export_private_key(self):
        if self.node.export_disabled:
            return _Res(EventType.DISABLED)
        return _Res(EventType.PRIVATE_KEY, {"private_key": bytes.fromhex(self.node.private_key)})

    async def import_private_key(self, key):
        self.calls.append(("import_key", key.hex()))
        if self.node.refuse_import:
            return _Res(EventType.ERROR, {"reason": "disabled"})
        self.node.pending_private = key.hex()
        return _Res(EventType.OK)

    async def set_name(self, name):
        self.calls.append(("name", name)); self.node.self_info["name"] = name; return _Res(EventType.OK)

    async def set_coords(self, lat, lon):
        self.calls.append(("coords", lat, lon)); return _Res(EventType.OK)

    async def set_radio(self, f, bw, sf, cr, repeat=None):
        self.calls.append(("radio", f, bw, sf, cr)); return _Res(EventType.OK)

    async def set_tx_power(self, dbm):
        self.calls.append(("tx", dbm)); return _Res(EventType.OK)

    async def add_contact(self, c):
        self.calls.append(("contact", c["public_key"], c["adv_name"])); return _Res(EventType.OK)

    async def reboot(self):
        self.calls.append(("reboot",))
        # The reboot applies the imported key, as the firmware does.
        if self.node.pending_private:
            self.node.private_key = self.node.pending_private
            self.node.self_info["public_key"] = self.node.pub_for(self.node.private_key)

    async def send_device_query(self):
        return _Res(EventType.DEVICE_INFO, dict(self.node.device))


class FakeNode:
    """A radio: identity in 'flash', reachable through FakeMC objects."""

    PUB = {PRIV_A: KEY_A, "bb" * 64: KEY_B}

    def __init__(self, private_key, name, model="Heltec V3", max_tx=22, export_disabled=False, refuse_import=False):
        self.private_key = private_key
        self.pending_private = None
        self.export_disabled = export_disabled
        self.refuse_import = refuse_import
        self.self_info = {"name": name, "public_key": self.pub_for(private_key), "radio_freq": 910.525,
                          "radio_bw": 62.5, "radio_sf": 7, "radio_cr": 5, "tx_power": 22, "max_tx_power": max_tx,
                          "adv_lat": 30.27, "adv_lon": -97.74}
        self.device = {"model": model, "ver": "v1.17.1-d929643", "max_contacts": 350}
        self.contacts = {}

    @classmethod
    def pub_for(cls, priv):
        return cls.PUB.get(priv, "ff" * 32)


class FakeMC:
    def __init__(self, node):
        self.node = node
        self.self_info = node.self_info
        self.contacts = node.contacts
        self.commands = FakeCommands(node)
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def prof_path(tmp_path, monkeypatch):
    path = tmp_path / "node_profile.json"
    monkeypatch.setattr(profile, "PROFILE_PATH", path)
    monkeypatch.setattr(radio_mod, "_PENDING_VERIFY", None)
    monkeypatch.setattr(radio_mod, "_ADOPT_ATTEMPTS", {})
    monkeypatch.setattr(radio_mod, "REBOOT_WAIT_S", 0.0)
    monkeypatch.setattr(settings, "radio_adopt", "auto")
    return path


def _people(n):
    return {f"{i:02x}" * 32: {"public_key": f"{i:02x}" * 32, "type": 1, "flags": 0, "out_path_len": -1, "out_path": "",
                              "adv_name": f"Person{i}", "last_advert": 1, "adv_lat": 30.0, "adv_lon": -97.0,
                              "lastmod": 1000 + i} for i in range(1, n + 1)}


# -- the profile file --

def test_snapshot_holds_identity_settings_and_people_only(prof_path):
    node = FakeNode(PRIV_A, "WX-AUS")
    node.contacts.update(_people(3))
    node.contacts["ee" * 32] = {"public_key": "ee" * 32, "type": 2, "adv_name": "A repeater", "flags": 0}
    prof = asyncio.run(profile.snapshot(FakeMC(node), node.device))
    assert prof["public_key"] == KEY_A and prof["private_key"] == PRIV_A and prof["key_export"] == "ok"
    assert prof["name"] == "WX-AUS" and prof["radio"]["freq_mhz"] == 910.525 and prof["tx_power"] == 22
    assert [c["adv_name"] for c in prof["contacts"]] == ["Person3", "Person2", "Person1"]     # newest first, no repeater
    profile.save(prof)
    assert oct(prof_path.stat().st_mode & 0o777) == "0o600"
    assert profile.load() == prof
    summary = profile.public_summary(prof)
    assert summary["has_key"] and summary["contacts"] == 3 and "private_key" not in summary


def test_snapshot_keeps_the_old_key_when_the_firmware_refuses_the_export(prof_path):
    node = FakeNode(PRIV_A, "WX-AUS", export_disabled=True)
    fresh = asyncio.run(profile.snapshot(FakeMC(node), node.device))
    assert fresh["private_key"] is None and "disabled" in fresh["key_export"]
    assert profile.can_adopt(fresh)
    again = asyncio.run(profile.snapshot(FakeMC(node), node.device, previous={"public_key": KEY_A, "private_key": PRIV_A}))
    assert again["private_key"] == PRIV_A                       # same node, so the key cannot have changed


def test_differs_and_can_adopt():
    prof = {"public_key": KEY_A, "private_key": PRIV_A}
    assert not profile.differs(None, {"public_key": KEY_B})
    assert not profile.differs(prof, {"public_key": KEY_A})
    assert profile.differs(prof, {"public_key": KEY_B})
    assert not profile.differs(prof, {})                        # no key known yet: no verdict
    assert profile.can_adopt(None) and profile.can_adopt({"public_key": KEY_A}) and profile.can_adopt(prof) is None


def test_adopt_writes_everything_and_caps_tx_power_to_the_board(prof_path):
    old = FakeNode(PRIV_A, "WX-AUS")
    old.contacts.update(_people(2))
    prof = asyncio.run(profile.snapshot(FakeMC(old), old.device))
    new = FakeNode("bb" * 64, "Heltec T114", model="Heltec T114", max_tx=20)
    mc = FakeMC(new)
    steps = asyncio.run(profile.adopt(mc, prof))
    kinds = [c[0] for c in mc.commands.calls]
    assert kinds == ["import_key", "name", "coords", "radio", "tx", "contact", "contact"]
    assert ("tx", 20) in mc.commands.calls and any("tops out at 20" in s for s in steps)
    assert "2 contacts restored" in steps


# -- adoption through the radio object --

def _connect(radio, node, monkeypatch):
    """Run radio.start() against a fake node; the serial layer is replaced."""
    opened = []

    async def open_any(self):
        opened.append(node.self_info["public_key"])
        return FakeMC(node)

    async def configure(self):
        pass

    monkeypatch.setattr(radio_mod.MeshcoreRadio, "_open_any", open_any)
    monkeypatch.setattr(radio_mod.MeshcoreRadio, "_configure_node", configure)
    asyncio.run(radio.start())
    return opened


def test_a_replacement_radio_is_adopted_on_connect(prof_path, monkeypatch):
    old = FakeNode(PRIV_A, "WX-AUS")
    old.contacts.update(_people(1))
    profile.save(asyncio.run(profile.snapshot(FakeMC(old), old.device)))

    new = FakeNode("bb" * 64, "Heltec T114", model="Heltec T114")
    r = radio_mod.MeshcoreRadio()
    opened = _connect(r, new, monkeypatch)
    assert opened == [KEY_B, KEY_A]                             # opened, adopted+rebooted, opened again
    assert new.self_info["public_key"] == KEY_A and new.self_info["name"] == "WX-AUS"
    assert r.adoption["ok"] and r.pending_adoption is None and r.connected
    saved = profile.load()
    assert saved["public_key"] == KEY_A and saved["model"] == "Heltec T114"
    assert saved["history"][-1]["ok"] and saved["history"][-1]["radio_key"] == KEY_B


def test_manual_mode_runs_with_the_new_identity_and_offers_adoption(prof_path, monkeypatch):
    old = FakeNode(PRIV_A, "WX-AUS")
    profile.save(asyncio.run(profile.snapshot(FakeMC(old), old.device)))
    monkeypatch.setattr(settings, "radio_adopt", "manual")
    new = FakeNode("bb" * 64, "fresh")
    r = radio_mod.MeshcoreRadio()
    opened = _connect(r, new, monkeypatch)
    assert opened == [KEY_B] and new.self_info["public_key"] == KEY_B
    assert r.pending_adoption["radio"]["public_key"] == KEY_B and r.pending_adoption["profile"]["name"] == "WX-AUS"
    assert profile.load()["public_key"] == KEY_A               # the profile was NOT overwritten by the stranger
    assert r.profile_status()["matches"] is False and "not refreshed" in r.profile_status()["note"]
    # The portal button: write, reboot, and the next connect verifies.
    steps = asyncio.run(r.adopt_profile())
    assert steps[0] == "identity key imported" and r._mc is None
    r2 = radio_mod.MeshcoreRadio()
    _connect(r2, new, monkeypatch)
    assert r2.adoption["ok"] and new.self_info["public_key"] == KEY_A and r2.pending_adoption is None


def test_a_node_that_refuses_the_key_is_not_retried_forever(prof_path, monkeypatch):
    old = FakeNode(PRIV_A, "WX-AUS")
    profile.save(asyncio.run(profile.snapshot(FakeMC(old), old.device)))
    new = FakeNode("bb" * 64, "locked", refuse_import=True)
    r = radio_mod.MeshcoreRadio()
    with pytest.raises(RuntimeError):
        _connect(r, new, monkeypatch)
    assert r.adoption["ok"] is False and "refused" in r.adoption["note"]
    assert profile.load()["history"][-1]["ok"] is False
    r2 = radio_mod.MeshcoreRadio()
    with pytest.raises(RuntimeError):
        _connect(r2, new, monkeypatch)
    r3 = radio_mod.MeshcoreRadio()                             # third connect: attempts spent, runs as-is
    _connect(r3, new, monkeypatch)
    assert r3.connected and r3.pending_adoption["attempts"] == 2


def test_force_saving_a_new_profile_forgets_the_old_node(prof_path, monkeypatch):
    old = FakeNode(PRIV_A, "WX-AUS")
    profile.save(asyncio.run(profile.snapshot(FakeMC(old), old.device)))
    monkeypatch.setattr(settings, "radio_adopt", "off")
    new = FakeNode("bb" * 64, "WX-NEW")
    r = radio_mod.MeshcoreRadio()
    _connect(r, new, monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(r.save_profile_now())
    summary = asyncio.run(r.save_profile_now(force=True))
    assert summary["public_key"] == KEY_B and profile.load()["name"] == "WX-NEW" and r.pending_adoption is None


# -- port discovery --

def test_candidate_ports_prefers_configured_then_alias_and_dedupes_symlinks(tmp_path, monkeypatch):
    dev = tmp_path
    (dev / "ttyUSB0").write_text(""); (dev / "ttyACM0").write_text("")
    byid = dev / "by-id"; byid.mkdir()
    os.symlink(dev / "ttyUSB0", byid / "usb-Silicon_Labs_CP2102-if00-port0")
    alias = dev / "meshcore"; os.symlink(dev / "ttyACM0", alias)
    patterns = {"/dev/serial/by-id/*": [str(byid / "usb-Silicon_Labs_CP2102-if00-port0")],
                "/dev/ttyACM*": [str(dev / "ttyACM0")], "/dev/ttyUSB*": [str(dev / "ttyUSB0")], "/dev/cu.usb*": []}
    real_exists, real_realpath = os.path.exists, os.path.realpath
    monkeypatch.setattr(radio_mod.glob, "glob", lambda p: patterns.get(p, []))
    monkeypatch.setattr(radio_mod.os.path, "exists", lambda p: p == "/dev/meshcore" or real_exists(p))
    monkeypatch.setattr(radio_mod.os.path, "realpath", lambda p: real_realpath(str(alias)) if p == "/dev/meshcore" else real_realpath(p))
    # configured port gone (a different board): the alias comes first, each device once
    assert radio_mod.candidate_ports("/dev/ttyUSB9") == ["/dev/meshcore", str(byid / "usb-Silicon_Labs_CP2102-if00-port0")]
    # configured port present: it goes first, its by-id twin is skipped
    assert radio_mod.candidate_ports(str(dev / "ttyUSB0")) == [str(dev / "ttyUSB0"), "/dev/meshcore"]
    assert radio_mod.candidate_ports("tcp://host:4403") == ["/dev/meshcore", str(byid / "usb-Silicon_Labs_CP2102-if00-port0")]


def test_udev_rule_covers_the_common_bridges():
    text = Path("deploy/99-meshcore-radio.rules").read_text()
    for vid in ("10c4", "1a86", "303a", "239a", "2886"):
        assert f'ATTRS{{idVendor}}=="{vid}"' in text
    assert text.count('SYMLINK+="meshcore"') >= 5
    assert all(line.startswith("SUBSYSTEM==") for line in text.splitlines() if line and not line.startswith("#"))


# -- health --

def _row(t, heard, kind="channel_data"):
    return (t, kind, heard, False, 0 if heard else 1, 900 if heard else None)


def test_health_tx_suspect_needs_a_streak_and_a_live_mesh():
    now = 10_000.0
    rows = [_row(now - 300 + i * 30, False) for i in range(3)]
    d = health.assess(outcomes=rows, last_rx_at=now - 5, last_repeat_heard_at=now - 60, rx_frames=50,
                      started_at=now - 3600, tx_enabled=True, rx_silent_s=1800, now=now)
    assert d["verdict"] == "tx_suspect" and d["unheard_streak"] == 3 and d["mesh_alive"]
    quiet = health.assess(outcomes=rows, last_rx_at=now - 5, last_repeat_heard_at=now - 3000, rx_frames=50,
                          started_at=now - 3600, tx_enabled=True, rx_silent_s=1800, now=now)
    assert quiet["verdict"] == "unknown"
    ok = health.assess(outcomes=rows + [_row(now - 10, True)], last_rx_at=now - 5, last_repeat_heard_at=now - 60,
                       rx_frames=50, started_at=now - 3600, tx_enabled=True, rx_silent_s=1800, now=now)
    assert ok["verdict"] == "ok" and ok["unheard_streak"] == 0 and ok["last_heard_send_at"] == now - 10
    two = health.assess(outcomes=rows[1:], last_rx_at=now - 5, last_repeat_heard_at=now - 60, rx_frames=50,
                        started_at=now - 3600, tx_enabled=True, rx_silent_s=1800, now=now)
    assert two["verdict"] == "ok" and "2 recent" in two["reason"]


def test_health_rx_silent_and_tx_off_and_idle():
    now = 10_000.0
    deaf = health.assess(outcomes=[], last_rx_at=now - 4000, last_repeat_heard_at=now - 4000, rx_frames=9,
                         started_at=now - 9000, tx_enabled=True, rx_silent_s=1800, now=now)
    assert deaf["verdict"] == "rx_silent" and "66 min" in deaf["reason"]
    never = health.assess(outcomes=[], last_rx_at=0.0, last_repeat_heard_at=0.0, rx_frames=0,
                          started_at=now - 2000, tx_enabled=True, rx_silent_s=1800, now=now)
    assert never["verdict"] == "rx_silent" and never["rx_age_s"] is None
    young = health.assess(outcomes=[], last_rx_at=0.0, last_repeat_heard_at=0.0, rx_frames=0,
                          started_at=now - 100, tx_enabled=True, rx_silent_s=1800, now=now)
    assert young["verdict"] == "idle"
    off = health.assess(outcomes=[_row(now - 50, False)] * 5, last_rx_at=now - 5, last_repeat_heard_at=now - 5,
                        rx_frames=5, started_at=now - 100, tx_enabled=False, rx_silent_s=1800, now=now)
    assert off["verdict"] == "tx_off"


def test_unheard_streak_ignores_dms_and_firmware_parse():
    rows = [_row(1, True), _row(2, False), (3, "dm", False, True, 0, None), _row(4, False)]
    assert health.unheard_streak(rows) == 2
    assert health.parse_version("v1.17.1-d929643") == (1, 17, 1) and health.parse_version("1.14") == (1, 14, 0)
    assert health.firmware_check("v1.17.1-d929643")["ok"] and not health.firmware_check("v1.14.2")["ok"]
    assert health.firmware_check(None) == {"ver": None, "parsed": None, "min": "1.15.0", "ok": False}


# -- delivery outcomes survive a restart --

def test_delivery_outcomes_persist_and_expire(tmp_path):
    path = tmp_path / "outcomes.json"
    t = DeliveryTracker(persist_path=path)
    now = time.time()
    t._outcomes.append((now - 100, "channel_data", True, False, 0, 800))
    t._outcomes.append((now - 90000, "channel_data", False, False, 1, None))       # older than a day
    t._save_outcomes()
    assert len(json.loads(path.read_text())["outcomes"]) == 1
    again = DeliveryTracker(persist_path=path)
    assert len(again.recent_outcomes()) == 1 and again.stats()["windows"]["24h"]["sent"] == 1
    assert again.last_rx_at == 0.0 and again.stats()["last_rx_at"] is None


# -- portal --

@pytest.fixture
def client(tmp_path, monkeypatch, prof_path):
    from fastapi.testclient import TestClient
    from meshcore_weather.main import WeatherBot
    from meshcore_weather.parser.weather import WeatherStore
    from meshcore_weather.portal.server import create_app
    from tests.test_admin_api import FakeRadio

    class SwapRadio(FakeRadio):
        device = {"model": "Heltec V3", "ver": "v1.17.1-d929643", "max_contacts": 350}
        pending_adoption = None
        adoption = None

        def profile_status(self):
            return {"path": str(prof_path), "profile": profile.public_summary(profile.load()), "matches": True,
                    "mode": settings.radio_adopt, "pending": self.pending_adoption, "last_adoption": self.adoption,
                    "note": None, "port": {"configured": settings.serial_port, "actual": "/dev/ttyACM0"}}

        async def test_transmit(self):
            if not settings.tx_enabled:
                raise ValueError("transmit is off")
            return {"sent": True, "bytes": 6, "heard": True, "result": "echoed", "echo_ms": 1200, "via": "AB",
                    "snr": 8.5, "attempts": 1, "skipped": None}

        async def save_profile_now(self, force=False):
            prof = {"public_key": KEY_A, "private_key": PRIV_A, "name": "WX-AUS", "contacts": [], "saved_at": time.time()}
            profile.save(prof)
            return profile.public_summary(prof)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "admin_key", "")
    monkeypatch.setattr(settings, "tx_enabled", True)
    bot = WeatherBot()
    bot.store = WeatherStore()
    bot.radio = SwapRadio()
    return TestClient(create_app(bot), headers={"X-Requested-With": "meshcore-portal"}), bot


def test_health_route_and_link_test(client):
    c, bot = client
    d = c.get("/api/radio/health").json()
    assert d["connected"] and d["firmware"]["ok"] and d["health"]["verdict"] in ("idle", "rx_silent", "ok")
    assert d["profile"]["port"]["actual"] == "/dev/ttyACM0" and d["profile"]["profile"] is None
    assert set(d["delivery"]) == {"1h", "24h"}
    t = c.post("/api/radio/testtx", json={}).json()
    assert t["heard"] and t["echo_ms"] == 1200
    s = c.post("/api/radio/profile/save", json={"force": False}).json()
    assert s["ok"] and s["profile"]["has_key"] and "private_key" not in s["profile"]
    assert c.get("/api/radio/health").json()["profile"]["profile"]["name"] == "WX-AUS"
    ov = c.get("/api/overview").json()["radio"]
    assert "health" in ov and ov["pending_adoption"] is False


def test_link_test_refused_when_tx_is_off(client, monkeypatch):
    c, bot = client
    monkeypatch.setattr(settings, "tx_enabled", False)
    assert c.post("/api/radio/testtx", json={}).status_code == 400


def test_adopt_setting_is_validated_and_live(client):
    c, bot = client
    assert c.post("/api/settings/env", json={"MCW_RADIO_ADOPT": "sometimes"}).status_code == 400
    r = c.post("/api/settings/env", json={"MCW_RADIO_ADOPT": "manual", "MCW_RADIO_RX_SILENT_MIN": "45"})
    assert r.status_code == 200 and settings.radio_adopt == "manual" and settings.radio_rx_silent_min == 45
    assert "MCW_RADIO_ADOPT=manual" in Path(".env").read_text()
