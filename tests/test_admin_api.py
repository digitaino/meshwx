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
        self.channel_idx, self.data_channel_idx = 1, 2
        self.name = "mesh-wx"
        self.params = {"radio_freq": 910.525, "radio_bw": 62.5, "radio_sf": 7, "radio_cr": 5, "tx_power": 22}
        self.channels = {0: "public", 1: "#digitaino-wx-bot", 2: "#aus-meshwx-v4"}
        self.adverts = 0
        self.data_sent = []
        self.path_hash = 1

    async def info(self):
        return {"name": self.name, "public_key": "ab" * 32, **self.params, "max_tx_power": 22,
                "adv_lat": 30.27, "adv_lon": -97.74, "battery_mv": 4100, "path_hash_size": self.path_hash,
                "channels": {"text": self.channel_idx, "data": self.data_channel_idx}}

    async def list_channels(self):
        return [{"idx": i, "name": self.channels.get(i, ""), "secret": "", "role": self._role_for(i),
                 "roles": [r for r, a in self._ROLES.items() if getattr(self, a) == i]}
                for i in range(8)]

    _ROLES = {"text": "channel_idx", "data": "data_channel_idx"}

    def _role_for(self, idx):
        """Text wins when one slot carries both roles, as on the real radio."""
        for r, a in self._ROLES.items():
            if getattr(self, a) == idx:
                return r
        return None

    async def set_path_hash_size(self, size):
        if size not in (1, 2, 3):
            raise ValueError("path hash size must be 1, 2 or 3 bytes")
        self.path_hash = size
        return size

    async def send_channel_data(self, data, data_type=0xFF10, ev=None):
        if not settings.tx_enabled or self.data_channel_idx is None:
            return False
        self.data_sent.append((self.data_channel_idx, data_type, bytes(data)))
        return True

    async def set_channel_name(self, idx, name, secret=None):
        if not 0 <= idx <= 7:
            raise ValueError("channel index must be 0-7")
        if idx == 0:
            raise ValueError("slot 0 is the public channel and is left alone")
        self.channels[idx] = name
        role = self._role_for(idx)
        if role and name:
            setattr(settings, {"text": "meshcore_channel", "data": "meshwx_channel"}[role], name)
        return role

    async def clear_channel(self, idx):
        if idx == 0:
            raise ValueError("channel 0 (public) cannot be cleared")
        if self._role_for(idx):
            raise ValueError("role slot")
        self.channels.pop(idx, None)

    async def assign_role(self, role, name):
        attr = self._ROLES[role]
        setting = {"text": "meshcore_channel", "data": "meshwx_channel"}[role]
        if not name:
            if role == "text":
                raise ValueError("the text channel is required")
            setattr(self, attr, None)          # never touches the text slot
            setattr(settings, setting, "")
            return None
        # Text and data may share one slot: point data at it, create nothing.
        if role == "data" and self.channel_idx is not None and name == settings.meshcore_channel:
            self.data_channel_idx = self.channel_idx
            settings.meshwx_channel = name
            return self.channel_idx
        target = next((i for i, n in self.channels.items() if n == name and i != 0), None)
        if target is None:
            cur = getattr(self, attr)
            other = {getattr(self, a) for r, a in self._ROLES.items() if r != role}
            target = cur if cur is not None and cur not in other else next(i for i in range(1, 8) if i not in self.channels)
            self.channels[target] = name
        setattr(self, attr, target)
        setattr(settings, setting, name)
        return target

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
    c = TestClient(create_app(bot), headers={"X-Requested-With": "meshcore-portal"})
    return c, bot


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
    assert c.delete("/api/radio/channel/1").status_code == 400            # role slot: refuse
    assert c.get("/api/radio/contacts").json()["contacts"][0]["name"] == "Tommy"


def test_path_hash_size_is_set_from_the_portal(client):
    c, bot = client
    assert c.get("/api/radio").json()["info"]["path_hash_size"] == 1
    r = c.post("/api/radio/pathhash", json={"bytes": 2}).json()
    assert r["ok"] and r["bytes"] == 2 and r["note"] == "the node now reports 2 bytes per hop"
    assert bot.radio.path_hash == 2 and c.get("/api/radio").json()["info"]["path_hash_size"] == 2
    assert c.post("/api/radio/pathhash", json={"bytes": 4}).status_code == 400
    assert c.post("/api/radio/pathhash", json={}).status_code == 400
    assert bot.radio.path_hash == 2                                       # a refusal changes nothing


def test_tx_switch_persists_and_gates_adverts(client, tmp_path):
    c, bot = client
    assert c.post("/api/radio/advert", json={}).json()["sent"] is False     # TX off
    r = c.post("/api/radio/tx", json={"enabled": True}).json()
    assert r["tx_enabled"] is True and r["adverted"] is True and bot.radio.adverts == 1   # first thing on air
    assert settings.tx_enabled is True
    assert "MCW_TX_ENABLED=true" in (tmp_path / ".env").read_text()
    assert c.post("/api/radio/advert", json={}).json()["sent"] is True and bot.radio.adverts == 2
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
    assert d["command"] == "help" and "wx" in d["reply"] and d["has_more"] is False and d["chars"] == len(d["reply"])
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


def test_channel_roles_follow_edits_from_either_page(client, tmp_path, monkeypatch):
    c, bot = client
    monkeypatch.setattr(settings, "meshcore_channel", "#digitaino-wx-bot")
    monkeypatch.setattr(settings, "meshwx_channel", "#aus-meshwx-v4")

    async def _no_broadcaster():          # the real one needs a real radio
        pass
    monkeypatch.setattr(bot, "_after_radio_connected", _no_broadcaster)
    # Text Bot page: rename data -> applied on the node, same slot, env persisted.
    # A stale discover_channel key from an old client is accepted and ignored.
    r = c.post("/api/settings/channels", json={"text_channel": "#digitaino-wx-bot", "data_channel": "#mesh-wx-aus",
                                               "discover_channel": "#mesh-wx-discover"}).json()
    assert r["applied"] and r["slots"] == {"text": 1, "data": 2}
    assert bot.radio.channels[2] == "#mesh-wx-aus" and settings.meshwx_channel == "#mesh-wx-aus"
    env = (tmp_path / ".env").read_text()
    assert "MCW_MESHWX_CHANNEL=#mesh-wx-aus" in env
    assert "DISCOVER" not in env
    d = c.get("/api/radio").json()
    assert d["configured_channels"]["data"] == "#mesh-wx-aus"
    assert "discover" not in d["configured_channels"]
    assert [ch["role"] for ch in d["channels"][:4]] == [None, "text", "data", None]
    # Radio page: renaming the tagged text slot moves the role with it
    r = c.post("/api/radio/channel", json={"idx": 1, "name": "#wx-bot"}).json()
    assert r["role"] == "text" and settings.meshcore_channel == "#wx-bot"
    assert "MCW_MESHCORE_CHANNEL=#wx-bot" in (tmp_path / ".env").read_text()
    # Validation: text is required, names start with '#' or are numeric
    assert c.post("/api/settings/channels", json={"text_channel": "", "data_channel": "#x"}).status_code == 400
    assert c.post("/api/settings/channels", json={"text_channel": "meshwx", "data_channel": ""}).status_code == 400


def test_text_and_data_can_share_one_slot(client, tmp_path, monkeypatch):
    c, bot = client
    monkeypatch.setattr(settings, "meshcore_channel", "#digitaino-wx-bot")
    monkeypatch.setattr(settings, "meshwx_channel", "#aus-meshwx-v4")

    async def _no_broadcaster():
        pass
    monkeypatch.setattr(bot, "_after_radio_connected", _no_broadcaster)
    # v5: one #meshwx carries text and data. Both roles land on slot 1 and the
    # old data slot is left alone rather than a second one being taken.
    r = c.post("/api/settings/channels", json={"text_channel": "#meshwx", "data_channel": "#meshwx"}).json()
    assert r["slots"] == {"text": 1, "data": 1}
    assert bot.radio.channel_idx == 1 and bot.radio.data_channel_idx == 1
    assert bot.radio.channels[1] == "#meshwx"
    assert settings.meshcore_channel == "#meshwx" and settings.meshwx_channel == "#meshwx"
    env = (tmp_path / ".env").read_text()
    assert "MCW_MESHCORE_CHANNEL=#meshwx" in env and "MCW_MESHWX_CHANNEL=#meshwx" in env
    # The shared slot reports as "text"; nothing claims slot 2 any more.
    d = c.get("/api/radio").json()
    assert [ch["role"] for ch in d["channels"][:3]] == [None, "text", None]
    assert d["channels"][1]["roles"] == ["text", "data"]     # one slot, both roles
    # Clearing the data role leaves the text slot alone
    r = c.post("/api/settings/channels", json={"text_channel": "#meshwx", "data_channel": ""}).json()
    assert r["slots"] == {"text": 1, "data": None}
    assert bot.radio.channel_idx == 1 and bot.radio.data_channel_idx is None
    assert bot.radio.channels[1] == "#meshwx"


def test_env_settings_apply_live_where_possible(client, tmp_path, monkeypatch):
    c, bot = client
    monkeypatch.setattr(settings, "timezone", "America/Chicago")
    r = c.post("/api/settings/env", json={"MCW_TIMEZONE": "America/New_York", "MCW_EMWIN_SOURCE": "sdr"}).json()
    assert r["applied"] == ["MCW_TIMEZONE"] and r["restart_needed"] == ["MCW_EMWIN_SOURCE"]
    assert settings.timezone == "America/New_York"
    assert "MCW_TIMEZONE=America/New_York" in (tmp_path / ".env").read_text()


def test_mutations_need_the_portal_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "tx_enabled", False)       # the Pi's .env has TX on
    bot = WeatherBot()
    bot.radio = FakeRadio()
    bare = TestClient(create_app(bot))                       # no X-Requested-With: a cross-site page
    assert bare.get("/api/system").status_code == 200
    assert bare.post("/api/radio/tx", json={"enabled": True}).status_code == 403
    assert bare.post("/api/settings/env", json={"MCW_TIMEZONE": "UTC"}).status_code == 403
    assert settings.tx_enabled is False


def test_env_values_are_single_printable_lines(client, tmp_path):
    c, _ = client
    r = c.post("/api/settings/env", json={"MCW_TIMEZONE": "America/Chicago\nMCW_TX_ENABLED=true"})
    assert r.status_code == 400
    assert "MCW_TX_ENABLED=true" not in (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else True


# -- The revamped portal: one overview call, validated settings, removed routes --


class FakeEmwin:
    async def fetch_products(self):
        return []


def test_overview_bundles_everything_the_landing_page_shows(client, monkeypatch):
    c, bot = client
    bot.emwin = FakeEmwin()
    d = c.get("/api/overview").json()
    for key in ("satellite", "feed", "radio", "textbot", "broadcasts", "problems", "audit", "host", "bot", "recent"):
        assert key in d, key
    assert d["radio"]["connected"] is True and d["radio"]["name"] == "mesh-wx" and d["radio"]["tx_enabled"] is False
    assert d["radio"]["reply_mode"] == settings.reply_mode
    assert d["broadcasts"]["running"] is False and d["broadcasts"]["jobs_total"] == 0
    assert "requests_1h" in d["textbot"] and isinstance(d["recent"], list)
    assert isinstance(d["problems"]["last_hour"], int) and d["host"]["hostname"]


def test_system_reports_coverage_and_live_keys(client):
    c, _ = client
    d = c.get("/api/system").json()
    assert "summary" in d["coverage"] and "zones" in d["coverage"] and "cities" in d["coverage"]
    assert "MCW_TIMEZONE" in d["live_keys"] and "MCW_EMWIN_SOURCE" not in d["live_keys"]


def test_bad_settings_are_refused_before_env_is_touched(client, tmp_path):
    c, _ = client
    env = tmp_path / ".env"
    assert c.post("/api/settings/env", json={"MCW_HOME_RADIUS_KM": "far"}).status_code == 400
    assert c.post("/api/settings/env", json={"MCW_REPLY_MODE": "shout"}).status_code == 400
    assert c.post("/api/settings/env", json={"MCW_CONTACT_HOUSEKEEPING": "maybe"}).status_code == 400
    assert c.post("/api/settings/env", json={"MCW_LOG_LEVEL": "LOUD"}).status_code == 400
    assert c.post("/api/settings/env", json={}).status_code == 400
    assert not env.exists() or "MCW_HOME_RADIUS_KM=far" not in env.read_text()
    r = c.post("/api/settings/env", json={"MCW_HOME_RADIUS_KM": "90", "MCW_CONTACT_KEEP_FREE": "5"}).json()
    assert r["applied"] == ["MCW_CONTACT_KEEP_FREE", "MCW_HOME_RADIUS_KM"] and r["note"] == "Applied now"
    assert settings.home_radius_km == 90 and settings.contact_keep_free == 5


def test_job_form_metadata_covers_every_product(client):
    from meshcore_weather.schedule.models import LOCATION_TYPES, PRODUCT_TYPES
    c, _ = client
    meta = c.get("/api/schedule/meta").json()
    assert set(meta["product_info"]) == PRODUCT_TYPES
    for p, info in meta["product_info"].items():
        assert info["locations"] and set(info["locations"]) <= LOCATION_TYPES, p


def test_legacy_routes_are_gone(client):
    c, _ = client
    for path in ("/api/status", "/api/warnings", "/api/coverage/save", "/api/coverage/preview",
                 "/api/autocomplete/city", "/api/radio/stats", "/config", "/schedule", "/data"):
        assert c.get(path).status_code in (404, 405), path
    assert c.post("/api/actions/v2-request", json={}).status_code in (404, 405)
    assert c.get("/").status_code == 200 and "Meshcore Weather" in c.get("/").text


# -- Limits and cooldowns ---------------------------------------------------------


def test_limits_are_visible_and_resettable(client, monkeypatch):
    """The five gates between a request and an answer, and the button that
    opens one by hand. Until this card an operator could not tell a refusal
    from a silence."""
    import time

    from meshcore_weather.protocol import broadcaster as bc

    c, bot = client
    rows = c.get("/api/limits").json()["rows"]
    by_id = {r["id"]: r for r in rows}
    # With no broadcaster the channel row still stands on its own.
    assert "channel" in by_id

    # Give the bot a responder holding a spent hour and a sender inside the floor.
    now = time.time()
    bot._broadcaster = SimpleNamespace(
        _sent=[now - 60] * bc.PER_HOUR,
        _last_by_sender={"abc": now - 1.0},
        _last_sweep_at=now - 60,
    )
    rows = c.get("/api/limits").json()["rows"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["budget"]["state"] == "spent"
    assert by_id["budget"]["used"] == bc.PER_HOUR
    assert by_id["budget"]["opens_in_s"] > 3000
    assert by_id["sender"]["state"] == "cooling"
    assert 0 < by_id["sender"]["opens_in_s"] <= bc.PER_SENDER_S
    assert by_id["sweep"]["state"] == "cooling"
    assert by_id["sweep"]["opens_in_s"] > 0

    assert c.post("/api/limits/reset", json={"id": "budget"}).status_code == 200
    assert not bot._broadcaster._sent
    c.post("/api/limits/reset", json={"id": "sweep"})
    assert bot._broadcaster._last_sweep_at == 0.0
    rows = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}
    assert rows["budget"]["state"] == "ready" and rows["sweep"]["state"] == "ready"

    assert c.post("/api/limits/reset", json={"id": "nonsense"}).status_code == 400


def test_the_radar_row_shows_the_tiles_inside_their_window(client, monkeypatch):
    """`>radar` (spec 7D): the row appears with a responder that knows radar,
    says whether there are pictures at all, and its reset opens the window."""
    from pathlib import Path

    from meshcore_weather.radar import service as radar_service
    from meshcore_weather.radar.service import RadarService

    c, bot = client
    monkeypatch.setattr(radar_service, "_shared",
                        RadarService(Path(__file__).parent / "fixtures" / "radar"))
    cooling = [{"south": 32, "west": -98, "zoom": 0, "taken_min": 29832458, "remaining_s": 240}]
    bot._broadcaster = SimpleNamespace(
        _sent=[], _last_by_sender={},
        radar_cooldowns=lambda now=None: list(cooling),
        clear_radar_cooldowns=cooling.clear,
    )
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["radar"]
    assert row["state"] == "cooling" and row["opens_in_s"] == 240 and row["resettable"]
    assert "1 tile inside the window" in row["detail"] and "pictures under 30 min old" in row["detail"]

    assert c.post("/api/limits/reset", json={"id": "radar"}).status_code == 200
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["radar"]
    assert row["state"] == "ready" and not row["resettable"]

    monkeypatch.setattr(radar_service, "_shared", RadarService(None))
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["radar"]
    assert "no dish directory" in row["detail"]


def test_the_sweep_row_counts_the_states_inside_their_own_window(client):
    """Since revision 10 the sweep cooldown is per state, so the national
    figure is only half of what is holding a request back."""
    import time

    from meshcore_weather.protocol import broadcaster as bc

    c, bot = client
    now = time.time()
    bot._broadcaster = SimpleNamespace(
        _sent=[], _last_by_sender={},
        _last_sweep=now - 3600,                     # the country is free again
        _last_sweep_state={"TX": (now - 10, False), "OK": (now - 20, True),
                           "CO": (now - 3600, False)},
        _parts=bc.PartsCache(),
    )
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["sweep"]
    assert row["state"] == "cooling" and row["used"] == 2      # TX and OK, not CO
    assert "2 states inside their own window" in row["detail"]
    assert 0 < row["opens_in_s"] <= bc.SWEEP_COOLDOWN_S

    # One button clears the national window and every state's with it.
    c.post("/api/limits/reset", json={"id": "sweep"})
    assert bot._broadcaster._last_sweep == 0.0
    assert bot._broadcaster._last_sweep_state == {}
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["sweep"]
    assert row["state"] == "ready" and row["opens_in_s"] == 0


def test_the_parts_row_shows_what_is_still_askable_for(client):
    """`>part` can only answer for what the bot still holds, so the row says
    how much that is and how old the oldest of it is."""
    import time

    from meshcore_weather.protocol import broadcaster as bc

    c, bot = client
    now = time.time()
    cache = bc.PartsCache()
    bot._broadcaster = SimpleNamespace(
        _sent=[], _last_by_sender={}, _last_sweep=0.0, _last_sweep_state={},
        _parts=cache,
    )
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["parts"]
    assert row["state"] == "ready" and row["detail"] == "nothing held"
    assert row["resettable"] is False

    for idx in range(3):
        cache.remember(212, idx, b"packet", now=now - 200)
    cache.remember(97, 0, b"packet", now=now - 5)
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["parts"]
    assert row["used"] == 2 and "2 groups held (4 packets)" in row["detail"]
    assert "oldest 3m" in row["detail"]
    assert row["state"] == "ready"           # held, but nothing inside the floor

    # A packet just resent holds the next ask for it, and Reset opens that.
    cache.stamp(212, [1], now=now)
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["parts"]
    assert row["state"] == "cooling" and 0 < row["opens_in_s"] <= bc.PART_RESEND_FLOOR_S
    assert row["resettable"] is True
    c.post("/api/limits/reset", json={"id": "parts"})
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["parts"]
    assert row["state"] == "ready"
    # The packets themselves stay: dropping them would close this gate.
    assert row["used"] == 2 and cache.lookup(212, [1])[0] == [b"packet"]


def test_the_channel_reply_limits_show_what_they_are_holding(client):
    import time

    c, bot = client
    now = time.time()
    bot._channel_replies = [now - 30] * WeatherBot.CHANNEL_REPLY_PER_HOUR
    bot._channel_reply_by_sender = {"stranger": now - 30}
    row = {r["id"]: r for r in c.get("/api/limits").json()["rows"]}["channel"]
    assert row["state"] == "spent"
    assert row["used"] == WeatherBot.CHANNEL_REPLY_PER_HOUR
    assert row["opens_in_s"] > 3000
    c.post("/api/limits/reset", json={"id": "channel"})
    assert not bot._channel_replies and not bot._channel_reply_by_sender
