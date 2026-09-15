"""Console buffer categories and the channel-reply fallback for strangers."""

import asyncio
import logging
import time

import pytest

from meshcore_weather.config import settings
from meshcore_weather.main import WeatherBot
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.portal import logbuf
from meshcore_weather.sdr_monitor import SdrMonitor


def test_categories_and_tail():
    logbuf.install()
    for name in ("meshcore_weather.meshcore.radio", "meshcore_weather.emwin.fetcher"):
        logging.getLogger(name).setLevel(logging.INFO)
    assert logbuf.category_for("meshcore_weather.meshcore.radio") == "radio"
    assert logbuf.category_for("meshcore") == "radio"
    assert logbuf.category_for("meshcore_weather.emwin.fetcher") == "satellite"
    assert logbuf.category_for("meshcore_weather.sdr") == "satellite"
    assert logbuf.category_for("meshcore_weather.parser.pfm") == "satellite"
    assert logbuf.category_for("meshcore_weather.main") == "bot"
    logging.getLogger("meshcore_weather.meshcore.radio").warning("console-test radio line")
    logging.getLogger("meshcore_weather.emwin.fetcher").info("console-test satellite line")
    assert [l["cat"] for l in logbuf.tail(10, q="console-test")] == ["radio", "satellite"]
    assert [l["cat"] for l in logbuf.tail(10, cat="radio", q="console-test")] == ["radio"]
    assert [l["level"] for l in logbuf.tail(10, level="WARNING", q="console-test")] == ["WARNING"]
    assert logbuf.counts()["radio"] >= 1


def test_sdr_monitor_logs_only_changes(caplog):
    caplog.set_level(logging.INFO, logger="meshcore_weather.sdr")
    m = SdrMonitor("http://127.0.0.1:1")
    base = 1_000_000.0
    st = {"stats": {"locked": True, "vit_avg": 120, "drops": 0, "gain": 10.0}, "mode": "receive",
          "status": {"services": {"goesrecv": "active", "goesproc": "active"}}}
    m.observe(st, now=base)
    m.observe(st, now=base + 10)
    assert len([r for r in caplog.records]) == 1 and "locked" in caplog.records[0].message
    st2 = {**st, "stats": {**st["stats"], "locked": False, "drops": 7}}
    m.observe(st2, now=base + 20)
    st3 = {**st2, "status": {"services": {"goesrecv": "failed", "goesproc": "active"}}}
    m.observe(st3, now=base + 70)
    msgs = [r.message for r in caplog.records]
    assert any("LOST LOCK" in x for x in msgs)
    assert any("goesrecv is failed" in x for x in msgs)
    assert any("dropped 14 packets" in x for x in msgs)   # 7 at +20 s, 7 more at +70 s, summarised once


class ChannelFakeRadio:
    def __init__(self):
        self.connected = True
        self.channel_idx, self.data_channel_idx, self.discover_channel_idx = 1, 2, 3
        self.channel_sent: list[tuple[int, str]] = []
        self.dms: list[tuple[str, str]] = []
        self.adverts = 0
        self.contacts = {}
        self.peers = []
        self.public_key = "ff" * 32

    def peer_bots(self):
        return self.peers

    def channel_text_budget(self):
        return 147

    def find_contact_by_name(self, name):
        return self.contacts.get(name)

    def find_contact_by_key(self, prefix):
        return None

    async def send_channel_message(self, ch, text, ev=None):
        self.channel_sent.append((ch, text))

    async def send_dm(self, prefix, text, ev=None):
        self.dms.append((prefix, text))
        return True

    async def advert_if_stale(self, max_age_s=3600):
        self.adverts += 1
        return True


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "reply_mode", "dm")
    monkeypatch.setattr(settings, "channel_reply_max_hops", 2)
    from meshcore_weather.geodata import resolver
    resolver.load()
    resolver.set_home(30.27, -97.74)          # Austin
    b = WeatherBot()
    b.store = WeatherStore()
    b.radio = ChannelFakeRadio()
    return b


def test_stranger_gets_one_channel_reply_and_an_advert(bot):
    asyncio.run(bot._handle_channel_message("1", "Stranger", "help"))
    assert len(bot.radio.channel_sent) == 1
    ch, text = bot.radio.channel_sent[0]
    assert ch == 1 and "wx" in text and len(text) <= 160
    assert bot.radio.adverts == 1 and bot.radio.dms == []
    # Same sender again inside the window: silent (rate limit resets the 5 s check)
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "Stranger", "help"))
    assert len(bot.radio.channel_sent) == 1
    # Never on the public channel, never on the data channel
    asyncio.run(bot._handle_channel_message("0", "Other", "help"))
    asyncio.run(bot._handle_channel_message("2", "Other", "help"))
    assert len(bot.radio.channel_sent) == 1


def test_known_sender_gets_a_dm(bot):
    bot.radio.contacts["Tommy"] = {"public_key": "ab" * 32, "adv_name": "Tommy"}
    asyncio.run(bot._handle_channel_message("1", "Tommy", "help"))
    assert bot.radio.channel_sent == [] and len(bot.radio.dms) == 1


def test_hourly_channel_budget(bot):
    bot._channel_replies = [time.time()] * WeatherBot.CHANNEL_REPLY_PER_HOUR
    asyncio.run(bot._handle_channel_message("1", "Someone", "help"))
    assert bot.radio.channel_sent == []


def test_far_strangers_get_no_channel_reply(bot):
    asyncio.run(bot._handle_channel_message("1", "Far", "help", 5))
    assert bot.radio.channel_sent == []
    asyncio.run(bot._handle_channel_message("1", "Near", "help", 1))
    assert len(bot.radio.channel_sent) == 1


def test_reply_modes(bot, monkeypatch):
    monkeypatch.setattr(settings, "reply_mode", "dm_only")
    asyncio.run(bot._handle_channel_message("1", "Stranger", "help", 0))
    assert bot.radio.channel_sent == [] and bot.radio.adverts == 0
    monkeypatch.setattr(settings, "reply_mode", "channel")
    bot.radio.contacts["Tommy"] = {"public_key": "ab" * 32, "adv_name": "Tommy"}
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "Tommy", "help", 6))     # far, known: still on channel
    assert len(bot.radio.channel_sent) == 1 and bot.radio.dms == []
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "Tommy", "help", 6))     # no per-sender budget in channel mode
    assert len(bot.radio.channel_sent) == 2


def test_bots_ignore_bots(bot):
    asyncio.run(bot._handle_channel_message("1", "WX-SAT", "Round Rock, TX: 91F", 0))
    asyncio.run(bot._handle_channel_message("1", "wx-dfw", "help", 0))
    assert bot.radio.channel_sent == [] and bot.radio.dms == []


def test_nearest_bot_answers_place_requests(bot):
    # A San Antonio bot is in our contact list. Round Rock is ours; San Marcos is theirs.
    bot.radio.peers = [{"name": "WX-SAT", "public_key": "00" * 32, "lat": 29.42, "lon": -98.49}]
    bot.radio.contacts["User"] = {"public_key": "ab" * 32, "adv_name": "User"}
    asyncio.run(bot._handle_channel_message("1", "User", "wx round rock tx", 0))
    assert len(bot.radio.dms) == 1
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "User", "wx san antonio tx", 0))
    assert len(bot.radio.dms) == 1                                        # theirs, we stay quiet
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "User", "help", 0))      # no place: everyone answers by DM
    assert len(bot.radio.dms) == 2


def test_over_the_air_strings_are_cleaned():
    from meshcore_weather.meshcore.radio import clean_text
    assert clean_text("Bob\n2026-09-14 [ERROR] fake line", 40) == "Bob2026-09-14 [ERROR] fake line"
    assert clean_text("\x1b[31mred\x1b[0m", 40) == "[31mred[0m"
    assert len(clean_text("x" * 500, 200)) == 200


def test_per_sender_and_global_reply_budgets(bot):
    bot.radio.contacts["Tommy"] = {"public_key": "ab" * 32, "adv_name": "Tommy"}
    for i in range(WeatherBot.REPLIES_PER_SENDER_PER_HOUR + 5):
        bot._rate_limit.clear()                     # defeat only the 5-second spacing
        asyncio.run(bot._handle_channel_message("1", "Tommy", "help", 0))
    assert len(bot.radio.dms) == WeatherBot.REPLIES_PER_SENDER_PER_HOUR
    # Global budget: many different senders
    bot._all_replies = [time.time()] * WeatherBot.REPLIES_PER_HOUR
    bot._rate_limit.clear()
    asyncio.run(bot._handle_channel_message("1", "Tommy", "help", 0))
    assert len(bot.radio.dms) == WeatherBot.REPLIES_PER_SENDER_PER_HOUR


def test_bad_coordinates_are_ignored(bot):
    bot.radio.contacts["Tommy"] = {"public_key": "ab" * 32, "adv_name": "Tommy"}
    asyncio.run(bot._handle_dm("ab" * 6, "Tommy", "@999,-97.7 wx"))
    assert getattr(bot, "_user_locations", {}) == {}
    bot._rate_limit.clear()                     # the 5-second spacing, not what is under test
    asyncio.run(bot._handle_dm("ab" * 6, "Tommy", "@30.27,-97.74 help"))
    assert bot._user_locations[bot._normalize_key("ab" * 6)] == (30.27, -97.74)
