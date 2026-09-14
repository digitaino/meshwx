"""Admin portal API: radio management, console, logs, settings, auth."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from meshcore_weather.config import settings
from meshcore_weather.main import WeatherBot
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.portal.server import create_app


class FakeRadio:
    """Stands in for MeshcoreRadio with a connected node."""

    def __init__(self, connected=True):
        self.connected = connected
        self.channel_idx, self.data_channel_idx, self.discover_channel_idx = 1, 2, 3
        self.name = "mesh-wx"
        self.params = {"radio_freq": 910.525, "radio_bw": 62.5, "radio_sf": 7, "radio_cr": 5, "tx_power": 22}
        self.channels = {0: "public", 1: "#digitaino-wx-bot", 2: "#aus-meshwx-v4", 3: "#meshwx-discover"}
        self.adverts = 0

    async def info(self):
        return {"name": self.name, "public_key": "ab" * 32, **self.params, "max_tx_power": 22,
                "adv_lat": 30.27, "adv_lon": -97.74, "battery_mv": 4100,
                "channels": {"text": 1, "data": 2, "discover": 3}}

    async def list_channels(self):
        return [{"idx": i, "name": self.channels.get(i, ""), "secret": "", "role": {1: "text", 2: "data", 3: "discover"}.get(i)} for i in range(8)]

    async def set_channel_name(self, idx, name, secret=None):
        if not 0 <= idx <= 7:
            raise ValueError("channel index must be 0-7")
        self.channels[idx] = name

    async def clear_channel(self, idx):
        if idx == 0:
            raise ValueError("channel 0 (public) cannot be cleared")
        self.channels.pop(idx, None)

    async def set_name(self, name):
        if not name.strip():
            raise ValueError("name must be 1-31 bytes")
        self.name = name

    async def set_radio_params(self, f, bw, sf, cr):
        if not (400 <= f <= 1000):
            raise ValueError("frequency must be 400-1000 MHz")
        self.params.update({"radio_freq": f, "radio_bw": bw, "radio_sf": sf, "radio_cr": cr})

    async def set_tx_power(self, dbm):
        self.params["tx_power"] = dbm

    async def set_coords(self, lat, lon):
        pass

    async def advert_now(self, flood=True):
        if not settings.tx_enabled:
            return False
        self.adverts += 1
        return True

    async def reboot(self):
        pass

    async def contacts(self):
        return [{"public_key": "cd" * 32, "name": "Tommy", "type": 1, "last_advert": 1, "lat": 0, "lon": 0, "out_path_len": 2}]

    async def stats(self):
        return {"core": {"uptime": 1}, "radio": None, "packets": None}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)              # .env writes land in a temp dir
    monkeypatch.setattr(settings, "admin_key", "")
    monkeypatch.setattr(settings, "tx_enabled", False)
    bot = WeatherBot()
    bot.store = WeatherStore()
    bot.radio = FakeRadio()
    return TestClient(create_app(bot)), bot


def test_radio_state_and_edits(client):
    c, bot = client
    d = c.get("/api/radio").json()
    assert d["connected"] and d["info"]["name"] == "mesh-wx" and len(d["channels"]) == 8
    assert d["channels"][1]["role"] == "text" and "us_meshcore" in d["presets"]
    assert c.post("/api/radio/name", json={"name": "wx-austin"}).json()["ok"]
    assert bot.radio.name == "wx-austin"
    assert c.post("/api/radio/name", json={"name": " "}).status_code == 400
    r = c.post("/api/radio/params", json={"preset": "us_meshcore"}).json()
    assert r["freq_mhz"] == 910.525 and r["sf"] == 7
    assert c.post("/api/radio/params", json={"freq_mhz": 100, "bw_khz": 62.5, "sf": 7, "cr": 5}).status_code == 400
    assert c.post("/api/radio/channel", json={"idx": 4, "name": "#test"}).json()["ok"]
    assert bot.radio.channels[4] == "#test"
    assert c.delete("/api/radio/channel/4").json()["ok"] and 4 not in bot.radio.channels
    assert c.delete("/api/radio/channel/0").status_code == 400
    assert c.get("/api/radio/contacts").json()["contacts"][0]["name"] == "Tommy"


def test_tx_switch_persists_and_gates_adverts(client, tmp_path):
    c, bot = client
    assert c.post("/api/radio/advert", json={}).json()["sent"] is False     # TX off
    assert c.post("/api/radio/tx", json={"enabled": True}).json()["tx_enabled"] is True
    assert settings.tx_enabled is True
    assert "MCW_TX_ENABLED=true" in (tmp_path / ".env").read_text()
    assert c.post("/api/radio/advert", json={}).json()["sent"] is True and bot.radio.adverts == 1
    c.post("/api/radio/tx", json={"enabled": False})
    assert "MCW_TX_ENABLED=false" in (tmp_path / ".env").read_text()


def test_radio_offline(client):
    c, bot = client
    bot.radio = FakeRadio(connected=False)
    bot._radio_last_error = "no companion response on /dev/ttyUSB0"
    d = c.get("/api/radio").json()
    assert d["connected"] is False and "no companion" in d["error"]
    assert c.post("/api/radio/name", json={"name": "x"}).status_code == 503


def test_console_matches_dm_path(client):
    c, bot = client
    d = c.post("/api/console", json={"text": "help"}).json()
    assert d["command"] == "help" and "wx" in d["reply"] and d["chunks"]
    d = c.post("/api/console", json={"text": "wx round rock tx"}).json()
    assert d["command"] == "wx" and d["location"] == "round rock tx"
    assert d["reply"].startswith("Round Rock, TX")
    assert c.get("/api/console/help").json()["help"]


def test_settings_env_whitelist(client, tmp_path):
    c, _ = client
    r = c.post("/api/settings/env", json={"MCW_HOME_RADIUS_KM": "150", "MCW_TIMEZONE": "America/Chicago"}).json()
    assert r["ok"]
    env = (tmp_path / ".env").read_text()
    assert "MCW_HOME_RADIUS_KM=150" in env and "MCW_TIMEZONE=America/Chicago" in env
    assert c.post("/api/settings/env", json={"MCW_ADMIN_KEY": "x"}).status_code == 400
    s = c.get("/api/system").json()
    assert "admin_key" not in s["settings"] and s["host"]["hostname"]
    assert isinstance(c.get("/api/logs").json()["lines"], list)


def test_portal_has_no_login(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "admin_key", "s3cret")   # only used by the mesh-side admin commands
    bot = WeatherBot()
    bot.radio = FakeRadio()
    assert TestClient(create_app(bot)).get("/api/system").status_code == 200
