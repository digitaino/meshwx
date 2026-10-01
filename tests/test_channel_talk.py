"""People talking on the request channel get no reply; requests still do.

Every message on #meshwx from 26 September to 1 October 2026 is here, from
the bot's own traffic record. Seven of the 33 were people talking, and each
was looked up as a place: "Unknown location: Meshwx make my thingy work"
flooded across the mesh, and a 🙌 got the national warning summary.
"""

import asyncio
import time

import pytest

from meshcore_weather.config import settings
from meshcore_weather.geodata import resolver
from meshcore_weather.main import HELP_TEXT, VALID_STATES, WeatherBot
from meshcore_weather.nlp import is_conversation, parse_intent, strip_mentions
from meshcore_weather.parser.weather import WeatherStore
from tests.test_console import ChannelFakeRadio

REQUESTS = [
    "metar kaus", "Weather Austin tx", "Austin tx", "Help", "Forecast austin", "forecast lockhart", "help",
    "warn 78641", "radar 78641", "forecast 78644", "more", "warn 78676", "forecast woodcreek tx",
    "Forecast round rock tx", "More", "forecast lockhart TX", "radar 78676", "wx 00907", "00907", "wx 78737",
    "wx 78676", "wx 78644",
]
TALK = [
    "Weather saying it can’t ask until it announces itself",
    "Meshwx make my thingy work",
    "@[🤷♂️NBDY] 👇",
    "Weather bot: wx/forecast/warn/radar <city ST|ZIP> |",
    "🙌",
    "weird",
    "Weird should default to Austin, no?",
]


@pytest.fixture(autouse=True)
def _home():
    resolver.load()
    resolver.set_home(30.27, -97.74)
    yield
    resolver._home = None


def _talk(text: str) -> bool:
    return is_conversation(text, resolve=resolver.resolve, states=VALID_STATES)


@pytest.mark.parametrize("text", REQUESTS)
def test_every_request_on_the_channel_is_still_a_request(text):
    assert not _talk(text)


@pytest.mark.parametrize("text", TALK)
def test_people_talking_on_the_channel_are_left_alone(text):
    assert _talk(text)


@pytest.mark.parametrize("text", [
    # Towns and airports a bare word would find, and state codes that are words.
    "nice", "cool", "hope", "lol", "hey", "here", "ok", "hi", "oh hi", "ok ok", "in tx", "see you in la",
    "good morning all", "Anyone near Lockhart TX?",
    # Command words in sentences.
    "weather is crazy today", "forecast looks bad tonight", "radar looks scary", "more rain coming",
])
def test_words_that_only_look_like_requests(text):
    assert _talk(text)


@pytest.mark.parametrize("text", [
    "Round Rock, TX", "Lockhart TX?", "san juan pr", "78701-1234", "@[WX-AUS] wx austin tx", "more storm",
    "more please", "storm TX", "wx austn", "forecast zzqqx", "weather", "weather lockhart", "sat", "cov",
])
def test_requests_that_look_like_talk(text):
    assert not _talk(text)


def test_weather_is_a_command_word():
    run = lambda t: asyncio.run(parse_intent(t))
    assert run("Weather Austin tx") == {"command": "wx", "location": "Austin tx"}
    assert run("weather 78644") == {"command": "wx", "location": "78644"}
    # On its own it is somebody who does not know the commands yet.
    assert run("weather") == {"command": "help", "location": ""}
    # The bot cannot know where "here" is: not Hereford, TX or Port Angeles, WA.
    for text in ("weather here", "wx now", "forecast today", "radar here?"):
        assert run(text) == {"command": "help", "location": ""}, text


def test_a_mention_is_not_part_of_the_request():
    assert strip_mentions("@[WX-AUS] wx austin tx") == "wx austin tx"
    assert strip_mentions("@[A] @[B]  forecast 78644") == "forecast 78644"


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "reply_mode", "channel")
    monkeypatch.setattr(settings, "admin_key", "")
    bot = WeatherBot()
    bot.store = WeatherStore()
    bot.radio = ChannelFakeRadio()
    return bot


def _say(bot, text, who):
    bot._rate_limit[who] = time.time() - 10
    bot.radio.channel_sent.clear()
    asyncio.run(bot._handle_channel_message("1", who, text, 0))
    return [t for _ch, t in bot.radio.channel_sent]


def test_on_the_air_talk_gets_nothing_and_requests_get_their_answer(bot):
    for i, text in enumerate(TALK):
        assert _say(bot, text, f"talker{i}") == [], text
    assert _say(bot, "Weather Austin tx", "A")[0].startswith("Austin, TX")
    assert _say(bot, "@[WX-AUS] wx 78701", "B")[0].startswith("Austin, TX 78701")
    assert _say(bot, "weather", "C") == [HELP_TEXT]
