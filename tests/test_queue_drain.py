"""The node's message queue is drained by the bot itself (radio.py _drain_queue):
meshcore-py's auto-fetch did not accept a channel datagram as the reply to a
fetch, so a Request datagram in the queue stalled it and surfaced only when
the next one arrived."""
import asyncio

import pytest
from meshcore import EventType
from meshcore.events import Event

from meshcore_weather.meshcore.radio import MeshcoreRadio


class _Commands:
    def __init__(self, script):
        self.script = list(script)          # events to answer successive fetches with
        self.fetches = []                    # (frame, expected, timeout)
        self.on_fetch = None                 # hook run before answering (a tickle mid-drain)

    async def send(self, frame, expect, timeout=None):
        self.fetches.append((bytes(frame), list(expect), timeout))
        if self.on_fetch:
            hook, self.on_fetch = self.on_fetch, None
            await hook()
        if not self.script:
            return Event(EventType.NO_MORE_MSGS, {})
        return self.script.pop(0)


class _MC:
    def __init__(self, script):
        self.commands = _Commands(script)


def _radio(script):
    r = MeshcoreRadio()
    r._mc = _MC(script)
    r._running = True
    return r


@pytest.mark.asyncio
async def test_a_datagram_is_accepted_as_the_reply_and_the_drain_continues_to_the_end():
    r = _radio([Event(EventType.CHANNEL_DATA_RECV, {"channel_idx": 1}),
                Event(EventType.CONTACT_MSG_RECV, {}),
                Event(EventType.NO_MORE_MSGS, {})])
    await r._drain_queue()
    fetches = r._mc.commands.fetches
    assert [f[0] for f in fetches] == [b"\x0a"] * 3, "three fetches: datagram, DM, then the end"
    assert all(EventType.CHANNEL_DATA_RECV in f[1] for f in fetches)
    assert all(f[2] == MeshcoreRadio.QUEUE_FETCH_TIMEOUT_S for f in fetches)
    assert r._draining is False


@pytest.mark.asyncio
async def test_a_tickle_during_a_drain_asks_for_one_more_pass():
    r = _radio([Event(EventType.NO_MORE_MSGS, {}),
                Event(EventType.CHANNEL_DATA_RECV, {"channel_idx": 1}),
                Event(EventType.NO_MORE_MSGS, {})])

    async def tickle():
        await r._on_messages_waiting(None)     # lands while the first fetch is out

    r._mc.commands.on_fetch = tickle
    await r._drain_queue()
    # The first pass ended in NO_MORE_MSGS, but the tickle that landed meanwhile
    # is not dropped: a second pass fetched the datagram and the end.
    assert len(r._mc.commands.fetches) == 3
    assert r._draining is False and r._drain_again is False


@pytest.mark.asyncio
async def test_a_timed_out_fetch_ends_the_drain_quietly():
    r = _radio([Event(EventType.ERROR, {"reason": "no_event_received"})])
    await r._drain_queue()
    assert len(r._mc.commands.fetches) == 1
    assert r._draining is False


@pytest.mark.asyncio
async def test_a_tickle_starts_a_drain_when_none_is_running():
    r = _radio([Event(EventType.NO_MORE_MSGS, {})])
    await r._on_messages_waiting(None)
    await asyncio.sleep(0)
    await r._drain_task
    assert len(r._mc.commands.fetches) == 1
