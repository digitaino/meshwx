"""The SSE helper every live stream goes through: hello, items, heartbeat, teardown."""

import asyncio

from meshcore_weather.portal.sse import sse_response


async def _take(gen, n):
    out = []
    async for frame in gen:
        out.append(frame)
        if len(out) == n:
            break
    await gen.aclose()
    return out


def test_hello_then_items():
    async def src():
        yield {"a": 1}
        yield {"b": 2}

    frames = asyncio.run(_take(sse_response(src()).body_iterator, 3))
    assert frames == ['data: {"hello": true}\n\n', 'data: {"a": 1}\n\n', 'data: {"b": 2}\n\n']


def test_heartbeat_while_idle_and_no_item_lost():
    closed = []

    async def src():
        try:
            await asyncio.sleep(0.15)
            yield {"late": True}
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    async def until_item():
        out = []
        gen = sse_response(src(), ping_s=0.05).body_iterator
        async for frame in gen:
            out.append(frame)
            if frame.startswith('data: {"late"'):
                break
        await gen.aclose()
        return out

    frames = asyncio.run(until_item())
    assert frames[0] == 'data: {"hello": true}\n\n' and frames[-1] == 'data: {"late": true}\n\n'
    assert ": ping\n\n" in frames[1:-1]          # at least one heartbeat while the source was idle
    assert closed == [True]                       # closing the response stops the source too
