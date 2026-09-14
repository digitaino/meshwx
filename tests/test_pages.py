"""Replies longer than one message: page splitting, and the 'more' flow over
the channel, by DM, and across the two."""

import asyncio
import time

import pytest

from meshcore_weather.config import settings
from meshcore_weather.core import render_text as rt
from meshcore_weather.core.pages import split_pages
from meshcore_weather.main import WeatherBot
from meshcore_weather.parser.weather import WeatherStore
from tests.test_console import ChannelFakeRadio

ITEMS = [f"Item{i} at Place Number {i}" for i in range(40)]
LONG = "40 things TX: " + "; ".join(ITEMS)


def test_short_reply_is_one_untagged_page():
    assert split_pages("Round Rock, TX: 91F", 147) == ["Round Rock, TX: 91F"]
    assert split_pages("line one\nline two", 147) == ["line one line two"]


def test_pages_fit_the_budget_and_never_cut_an_item():
    pages = split_pages(LONG, 147)
    assert len(pages) > 3
    for i, p in enumerate(pages, 1):
        assert len(p) <= 147, p
        assert p.endswith(f" ({i}/{len(pages)}) more") if i < len(pages) else p.endswith(f" ({i}/{len(pages)})")
    body = [p.rsplit(" (", 1)[0] for p in pages]
    for b in body:
        for item in b.replace("40 things TX: ", "").split("; "):
            assert item in ITEMS, item                      # whole items only, no leading separators
    assert "; ".join(x.replace("40 things TX: ", "") for x in body) == "; ".join(ITEMS)   # nothing lost


def test_long_single_item_is_hard_cut_and_many_pages_still_fit():
    pages = split_pages("x" * 1000, 100)
    assert len(pages) == 12 and all(len(p) <= 100 for p in pages)
    assert pages[0].endswith("(1/12) more") and pages[-1].endswith("(12/12)")
    assert "".join(p.rsplit(" (", 1)[0] for p in pages) == "x" * 1000


def test_renderers_no_longer_trim():
    out = rt.fit_list("16 storm reports NY: ", ITEMS[:12], cap=100)
    assert out.endswith(ITEMS[11]) and " more" not in out
    assert rt._cap("z" * 500) == "z" * 500


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "reply_mode", "channel")
    monkeypatch.setattr(settings, "admin_key", "")
    from meshcore_weather.geodata import resolver
    resolver.load()
    resolver.set_home(30.27, -97.74)
    b = WeatherBot()
    b.store = WeatherStore()
    b.radio = ChannelFakeRadio()
    monkeypatch.setattr(b, "_process_command", lambda command, location: LONG if command == "storm" else "short answer")
    return b


def _say(bot, who, text, hops=0, ago=10.0):
    bot._rate_limit[who] = time.time() - ago
    asyncio.run(bot._handle_channel_message("1", who, text, hops))
    return bot.radio.channel_sent[-1][1] if bot.radio.channel_sent else None


def test_more_walks_the_pages_on_the_channel(bot):
    n = len(split_pages(LONG, 147))
    first = _say(bot, "Digitaino", "storm TX")
    assert first.endswith(f"(1/{n}) more")
    for i in range(2, n + 1):
        page = _say(bot, "Digitaino", "more", ago=3)          # 3 s after the last reply: allowed for 'more'
        assert f"({i}/{n})" in page
    assert page.endswith(f"({n}/{n})")
    assert _say(bot, "Digitaino", "more", ago=3).startswith("That was the whole reply to 'storm TX'")
    assert _say(bot, "Nobody", "more").startswith("Nothing to continue")


def test_more_needs_two_seconds_a_new_command_needs_five(bot):
    _say(bot, "Digitaino", "storm TX")
    sent = len(bot.radio.channel_sent)
    _say(bot, "Digitaino", "more", ago=1)                      # too soon even for a follow-up
    assert len(bot.radio.channel_sent) == sent
    _say(bot, "Digitaino", "wx", ago=3)                        # a new command 3 s later: gated
    assert len(bot.radio.channel_sent) == sent
    assert "(2/" in _say(bot, "Digitaino", "more", ago=3)


def test_more_wx_and_wx_more_both_continue(bot):
    _say(bot, "D", "storm TX")
    assert "(2/" in _say(bot, "D", "more storm")
    assert "(3/" in _say(bot, "D", "storm more")


def test_single_page_reply_still_answers_more(bot):
    assert _say(bot, "D", "wx austin") == "short answer"
    assert _say(bot, "D", "more").startswith("That was the whole reply to 'wx austin'")


def test_session_started_on_the_channel_continues_by_dm(bot):
    _say(bot, "Tommy", "storm TX")
    assert "ch:Tommy" in bot._paging
    bot._rate_limit.clear()
    asyncio.run(bot._handle_dm("ab" * 32, "Tommy", "more"))
    assert bot.radio.dms and "(2/" in bot.radio.dms[-1][1]
    assert "ch:Tommy" not in bot._paging and "ab" * 6 in bot._paging
    # Once known, their next channel request keys on the same identity.
    bot.radio.contacts["Tommy"] = {"public_key": "ab" * 32, "adv_name": "Tommy"}
    _say(bot, "Tommy", "more")
    assert "(3/" in bot.radio.channel_sent[-1][1]


def test_pages_follow_the_node_name_budget(bot):
    bot.radio.channel_text_budget = lambda: 130
    for p in split_pages(LONG, bot.page_budget()):
        assert len(p) <= 130
    first = _say(bot, "D", "storm TX")
    assert len(first) <= 130


def test_sessions_expire_and_are_bounded(bot):
    _say(bot, "Old", "storm TX")
    bot._paging["ch:Old"]["ts"] = time.time() - WeatherBot.PAGE_SESSION_TTL_S - 1
    for i in range(WeatherBot.PAGE_SESSIONS_MAX + 5):
        bot._start_session(f"k{i}", "x", LONG)
    assert "ch:Old" not in bot._paging and len(bot._paging) <= WeatherBot.PAGE_SESSIONS_MAX + 5
    bot.reply_chunk("more", "", "nobody")
    assert len(bot._paging) <= WeatherBot.PAGE_SESSIONS_MAX


def test_strangers_in_dm_mode_get_one_message_and_no_session(bot, monkeypatch):
    monkeypatch.setattr(settings, "reply_mode", "dm")
    out = _say(bot, "Stranger", "storm TX", hops=1)
    assert len(out) <= 147 and out.endswith("… DM me for all") and "Stranger" not in str(bot._paging)
