"""Coming up and going down: the radio before the products, honest answers
while the backlog loads, no broadcast from an empty store, and a stop that
ends the process with 0 and nothing left running."""

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from meshcore_weather.config import settings
from meshcore_weather.main import NOT_READY_TEXT, WeatherBot
from meshcore_weather.parser.weather import WeatherStore
from tests.test_console import ChannelFakeRadio


class StartFakeRadio(ChannelFakeRadio):
    """A node that takes a moment to answer, like a real one waiting for its
    ESP32 to boot, and records when it did."""

    def __init__(self, connect_s: float = 0.05, on_connect=None):
        super().__init__()
        self.data_channel_idx = None          # no broadcaster unless a test asks for one
        self.connect_s = connect_s
        self.connect_at: float | None = None
        self.stopped = False
        self._on_connect = on_connect

    def on_channel_message(self, h): self._ch = h
    def on_dm(self, h): self._dm = h
    def on_advert(self, h): self._adv = h
    def on_disconnect(self, h): self._lost = h

    async def start(self):
        await asyncio.sleep(self.connect_s)
        self.connect_at = time.monotonic()
        if self._on_connect:
            self._on_connect()

    async def stop(self):
        self.stopped = True


class SlowStore:
    """A store whose parse really takes time, in whatever thread the bot puts
    it in."""

    def __init__(self, per_chunk: float = 0.2):
        self._products: dict[str, dict] = {}
        self.per_chunk = per_chunk
        self.done_at: float | None = None

    def ingest(self, products, log=True):
        time.sleep(self.per_chunk)
        for p in products:
            self._products[p["filename"]] = p
        self.done_at = time.monotonic()
        return len(products)


class FakeSource:
    """An EMWIN source with a backlog on disk."""

    def __init__(self, products, start_s: float = 0.05):
        self.products = products
        self.start_s = start_s
        self.started = self.stopped = False

    async def start(self):
        await asyncio.sleep(self.start_s)
        self.started = True

    async def stop(self):
        self.stopped = True

    async def fetch_products(self):
        return list(self.products)


def _products(n: int) -> list[dict]:
    return [{"filename": f"A_WOUS64KWNS{i:06d}_C_KWIN_20260916000000_1-2-TORPAHKY.TXT",
             "raw_text": "test"} for i in range(n)]


@pytest.fixture
def bot(monkeypatch, tmp_path):
    """A bot with no hardware, no portal and no receiver monitor."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "portal_enabled", False)
    monkeypatch.setattr(settings, "emwin_source", "internet")
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "reply_mode", "dm")
    b = WeatherBot()
    b.radio = StartFakeRadio()
    b.store = SlowStore()
    b.emwin = FakeSource(_products(2))

    async def _no_warm():                     # pyIEM on two fake products proves nothing
        return None
    monkeypatch.setattr(b, "_warm_warnings", _no_warm)
    return b


# -- The radio comes first ---------------------------------------------------


def test_the_radio_connects_before_the_backlog_is_parsed(bot, caplog):
    caplog.set_level("INFO", logger="meshcore_weather.main")
    bot.emwin = FakeSource(_products(4), start_s=0.0)
    bot.store = SlowStore(per_chunk=0.3)

    async def go():
        t0 = time.monotonic()
        await bot.start()
        to_radio = time.monotonic() - t0
        assert bot.store.done_at is None      # the products are still loading
        assert not bot.store_ready
        await bot._backlog_task               # ... and land behind the radio
        await bot.stop()
        return to_radio

    to_radio = asyncio.run(go())
    assert bot.radio.connect_at is not None and bot.store.done_at is not None
    assert bot.radio.connect_at < bot.store.done_at
    assert to_radio < 0.3                     # start() returns without waiting for the parse
    assert bot.store_ready
    lines = [r.message for r in caplog.records]
    assert any("Meshcore radio connected" in m for m in lines)
    assert any(m.startswith("Backlog loaded: 4 products in ") for m in lines)


def test_the_backlog_is_parsed_off_the_event_loop(bot):
    """The chunks go to a worker thread: the loop keeps running while a Pi
    grinds through thousands of products, so the radio's serial reads and the
    echo matching in delivery.py are not held up."""
    threads = []
    bot.emwin = FakeSource(_products(3), start_s=0.0)
    ticks = 0

    class ThreadStore(SlowStore):
        def ingest(self, products, log=True):
            import threading
            threads.append(threading.current_thread().name)
            return super().ingest(products, log)

    bot.store = ThreadStore(per_chunk=0.2)

    async def go():
        nonlocal ticks
        await bot.start()
        while not bot.store_ready:            # the loop is free the whole time
            await asyncio.sleep(0.01)
            ticks += 1
        await bot.stop()

    asyncio.run(go())
    import threading
    assert threads and all(t != threading.main_thread().name for t in threads)
    assert ticks > 3


def test_a_store_that_cannot_be_loaded_still_leaves_a_working_radio(bot, caplog):
    class Broken(FakeSource):
        async def start(self):
            raise FileNotFoundError("no EMWIN directory")

    bot.emwin = Broken([])

    async def go():
        await bot.start()
        await bot._backlog_task
        await bot.stop()

    asyncio.run(go())
    assert bot.radio.connect_at is not None
    assert bot.store_ready                    # honest: it holds what it holds, nothing more coming


# -- What a request gets while the backlog loads -----------------------------


def _dm_bot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "tx_enabled", True)
    monkeypatch.setattr(settings, "reply_mode", "dm")
    from meshcore_weather.geodata import resolver
    resolver.load()
    resolver.set_home(30.27, -97.74)
    b = WeatherBot()
    b.store = WeatherStore()
    b.radio = ChannelFakeRadio()
    b._store_ready.clear()                    # as a restart leaves it
    return b


def test_a_text_dm_before_the_backlog_gets_the_truth_and_costs_nothing(monkeypatch, tmp_path):
    b = _dm_bot(monkeypatch, tmp_path)
    asyncio.run(b._handle_dm("aa" * 32, "Tommy", "wx austin tx"))
    assert b.radio.dms and b.radio.dms[-1][1] == NOT_READY_TEXT
    # Nothing of the sender's hour is spent, and no paging session is opened
    # on a reply that holds no weather.
    assert b._reply_history == {} and b._all_replies == [] and b._rate_limit == {}
    assert b._paging == {}


def test_what_does_not_need_the_store_is_answered_anyway(monkeypatch, tmp_path):
    b = _dm_bot(monkeypatch, tmp_path)
    asyncio.run(b._handle_dm("aa" * 32, "Tommy", "help"))         # help never needed the store
    assert "wx" in b.radio.dms[-1][1] and b.radio.dms[-1][1] != NOT_READY_TEXT
    b._rate_limit.clear()
    asyncio.run(b._handle_dm("aa" * 32, "Tommy", "cov"))          # nor does cov
    assert b.radio.dms[-1][1] != NOT_READY_TEXT
    # And the real answer comes the moment the products are in.
    b._store_ready.set()
    b._rate_limit.clear()
    asyncio.run(b._handle_dm("aa" * 32, "Tommy", "wx austin tx"))
    assert b.radio.dms[-1][1] != NOT_READY_TEXT


def test_no_transport_reads_an_empty_store_as_no_weather(monkeypatch, tmp_path):
    """The channel, the portal console and the CLI all render through
    _process_command, so none of them can claim "no warnings" while the
    products are still loading."""
    b = _dm_bot(monkeypatch, tmp_path)
    for command, location in (("wx", ""), ("warn", "TX"), ("forecast", "austin tx"),
                              ("metar", "KAUS"), ("space", ""), ("storm", "TX")):
        assert b._process_command(command, location) == NOT_READY_TEXT, command
    assert b._process_command("help", "") != NOT_READY_TEXT
    b._store_ready.set()
    assert b._process_command("warn", "TX") != NOT_READY_TEXT


def _responder(monkeypatch, ready):
    from unittest.mock import MagicMock
    import meshcore_weather.schedule.scheduler as sched_mod
    from meshcore_weather.protocol.broadcaster import AppResponder
    monkeypatch.setattr(sched_mod, "TX_SPACING", 0)
    radio = MagicMock()
    radio._mc = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    sent = []

    async def cap(data, data_type=0xFF10, ev=None):
        sent.append(bytes(data))
        return True
    radio.send_channel_data = cap
    return AppResponder(WeatherStore(), radio, render_text=lambda c, a: None, ready=ready), sent


@pytest.mark.asyncio
async def test_an_app_request_before_the_backlog_gets_not_available_reason_0(monkeypatch):
    from meshcore_weather.protocol import v5
    ready = asyncio.Event()
    r, sent = _responder(monkeypatch, ready)
    assert await r.handle_request(">w", "app") == "starting up"
    d = v5.decode(sent[-1])
    assert d["name"] == "not_available" and d["reason"] == v5.REASON_NO_DATA and d["request"] == "w"
    assert len(sent) == 1                       # one packet, not a digest built from nothing
    assert list(r._sent) == []                  # and not a packet of the hourly budget
    ready.set()
    r._last_by_sender.clear()
    assert await r.handle_request(">w", "app") != "starting up"
    assert v5.decode(sent[-1])["name"] == "digest"


@pytest.mark.asyncio
async def test_a_dm_app_request_before_the_backlog_spends_no_reply_budget(monkeypatch, tmp_path):
    from meshcore_weather.protocol import v5
    b = _dm_bot(monkeypatch, tmp_path)
    ready = b._store_ready
    b._broadcaster, sent = _responder(monkeypatch, ready)
    await b._handle_dm("aa" * 32, "App", ">w", sender_ts=1000)
    assert v5.decode(sent[-1])["reason"] == v5.REASON_NO_DATA
    assert b._reply_history == {} and b._all_replies == []
    assert b.radio.dms == []                    # an app request is never answered by DM
    # The app resends the same request; once the products are in it gets a
    # real answer instead of the "no data yet" it was told first.
    ready.set()
    b._broadcaster._last_by_sender.clear()
    await b._handle_dm("aa" * 32, "App", ">w", sender_ts=1000)
    assert v5.decode(sent[-1])["name"] == "digest"


# -- The scheduler holds its broadcasts --------------------------------------


@pytest.mark.asyncio
async def test_the_scheduler_broadcasts_nothing_before_the_backlog_is_in(monkeypatch, tmp_path):
    import meshcore_weather.schedule.scheduler as sched_mod
    from meshcore_weather.schedule import store as store_module
    monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_path / "broadcast_config.json")
    monkeypatch.setattr(sched_mod, "FIRST_TICK_DELAY_S", 0.0)
    monkeypatch.setattr(sched_mod, "TICK_INTERVAL_SECONDS", 0.01)
    ready = asyncio.Event()
    s, _ = _responder(monkeypatch, ready)
    sched = s.scheduler
    ticks = []

    async def tick():
        ticks.append(time.monotonic())
        return 0
    monkeypatch.setattr(sched, "tick", tick)

    await sched.start()
    await asyncio.sleep(0.05)
    assert ticks == []                          # held: an empty store has nothing true to say
    ready.set()
    await asyncio.sleep(0.05)
    assert ticks                                # and runs as soon as the products are in
    await sched.stop()


# -- Going down --------------------------------------------------------------


def test_a_signal_during_start_up_stops_cleanly_and_leaves_no_task(bot):
    """SIGTERM while the bot is still coming up: the one case that used to end
    in "Event loop stopped before Future completed"."""
    from meshcore_weather import main as main_mod

    bot.radio = StartFakeRadio(connect_s=0.3)
    bot.emwin = FakeSource(_products(2), start_s=0.3)

    async def go():
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        await main_mod.run(bot)
        return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}

    assert asyncio.run(go()) == set()
    assert not bot._running and bot.radio.stopped


def test_a_signal_stops_a_running_bot_and_leaves_no_task(bot):
    from meshcore_weather import main as main_mod

    async def go():
        # Signal once the bot is up and the backlog is in: the ordinary stop.
        asyncio.get_running_loop().call_later(0.4, os.kill, os.getpid(), signal.SIGTERM)
        await main_mod.run(bot)
        return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}

    assert asyncio.run(go()) == set()
    assert bot.store_ready and bot.radio.stopped and bot.emwin.stopped


def test_the_portal_restart_button_takes_the_same_road(bot):
    from meshcore_weather import main as main_mod

    async def go():
        asyncio.get_running_loop().call_later(0.4, bot.request_restart, 0.0)
        await main_mod.run(bot)
        return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}

    assert asyncio.run(go()) == set()
    assert bot.radio.stopped


def test_a_radio_that_will_not_open_is_not_a_failed_start(bot):
    """A missing radio is a state to keep serving in, not a crash: the retry
    loop takes over and the store, portal and CLI carry on."""
    class Dead(StartFakeRadio):
        async def start(self):
            raise ConnectionError("no companion response")

    bot.radio = Dead()

    async def go():
        await bot.start()
        assert bot._radio_task is not None and "no companion" in bot._radio_last_error
        await bot._backlog_task
        await bot.stop()

    asyncio.run(go())


def test_a_start_that_fails_for_another_reason_is_still_an_error(bot):
    """Anything else must reach the logs and the exit status, as it did when
    start() was run straight off run_until_complete."""
    from meshcore_weather import main as main_mod

    class Broken(StartFakeRadio):
        def on_dm(self, h):
            raise RuntimeError("wiring is wrong")

    bot.radio = Broken()
    with pytest.raises(RuntimeError, match="wiring is wrong"):
        asyncio.run(main_mod.run(bot))
    assert not bot._running                            # stopped on the way out all the same


@pytest.mark.asyncio
async def test_the_portal_does_not_take_the_process_signals(bot, monkeypatch):
    """uvicorn calls signal.signal inside serve(); left alone it would own
    SIGTERM and shut the web server down while the radio and the scheduler
    ran on. The bot's handler must still be the one on the signal."""
    pytest.importorskip("uvicorn")
    from meshcore_weather.portal.server import PortalServer
    monkeypatch.setattr(settings, "portal_host", "127.0.0.1")
    monkeypatch.setattr(settings, "portal_port", _free_port())
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, bot.request_stop)
    ours = signal.getsignal(signal.SIGTERM)
    portal = PortalServer(bot)
    try:
        await portal.start()
        await asyncio.sleep(0.2)                # let serve() get past capture_signals
        assert signal.getsignal(signal.SIGTERM) is ours
    finally:
        await portal.stop()
        loop.remove_signal_handler(signal.SIGTERM)


# The whole thing, as systemd runs it: the exit status is what the unit sees.
_DRIVER = """
import asyncio, os, signal, sys
from meshcore_weather.config import settings
settings.portal_enabled = True
settings.portal_host = "127.0.0.1"
settings.portal_port = %(port)s
settings.emwin_source = "internet"
settings.tx_enabled = False
from meshcore_weather import main as m


class Radio:
    channel_idx, data_channel_idx = 1, None
    def on_channel_message(self, h): pass
    def on_channel_request(self, h): pass
    def on_dm(self, h): pass
    def on_advert(self, h): pass
    def on_disconnect(self, h): pass
    async def start(self): await asyncio.sleep(0.05)
    async def stop(self): pass


class Source:
    async def start(self): await asyncio.sleep(0.05)
    async def stop(self): pass
    async def fetch_products(self): return []


async def _quit():
    await asyncio.sleep(%(after)s)
    os.kill(os.getpid(), signal.SIGTERM)


class Bot(m.WeatherBot):
    def __init__(self):
        super().__init__()
        self.radio, self.emwin = Radio(), Source()

    async def start(self):
        asyncio.ensure_future(_quit())          # a systemd stop, at %(after)s s
        await super().start()


m.WeatherBot = Bot                              # main() builds its own
sys.exit(m.main())
"""


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("after", ["0.02", "0.5"])
def test_the_process_exits_0_on_sigterm(tmp_path, after):
    """A stop during the start-up and a stop of a running bot both end with
    status 0, no traceback and no task destroyed pending — systemd showed
    status=1/FAILURE on every deploy before. The portal runs too: uvicorn
    installs signal handlers of its own unless it is told not to."""
    script = tmp_path / "run_bot.py"
    script.write_text(textwrap.dedent(_DRIVER) % {"after": after, "port": _free_port()})
    p = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                       capture_output=True, text=True, timeout=60)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "Weather bot stopped" in out
    assert "Traceback" not in out and "Event loop stopped" not in out
    assert "was destroyed but it is pending" not in out
