"""Area sweep (type 10, spec 7C, revision 9): the national picture in runs.

Three layers, in the order the bytes travel: the wire codec (protocol/v5.py),
the builder that turns active warnings into ordered entries
(protocol/v5_builders.py), and the `>wmap` request with the airtime limits
that are the whole point of the feature (protocol/broadcaster.py).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from meshcore_weather.protocol import v5
from meshcore_weather.protocol import v5_builders as b

BOT = 0x4C7A
NOW = 29823900  # 2026-09-15 01:00 UTC


def _entry(event, state, county, start, run):
    return (event, state, county, start, run)


def _warning(phen="SV", sig="W", ugcs=("TXC453",), onset=0):
    """Only the fields the sweep reads: the event, its significance, its areas
    and when it began."""
    return {
        "event_code": b.tables.event_code(f"{phen}.{sig}"),
        "vtec_phenomenon": phen,
        "vtec_significance": sig,
        "ugcs": list(ugcs),
        "onset_unix_min": onset,
    }


# ---------------------------------------------------------------------------
# The wire (spec 7C)
# ---------------------------------------------------------------------------


def test_entry_packing_offset_by_offset():
    """One packet, checked byte by byte against the table in section 7C."""
    data = v5.encode_area_sweep(
        32,
        BOT,
        built_min=NOW,
        group=32,
        idx=1,
        total=3,
        entries=[
            _entry(1, 42, True, 453, 1),      # TO.W, TX, county 453, one
            _entry(24, 7, False, 1023, 64),   # WS.W, zone, the two maxima
        ],
        cut=True,
        advisories=True,
        source=v5.SOURCE_INTERNET,
    )
    assert len(data) == 11 + 2 * 4
    assert data[0] == 32                                   # seq
    assert int.from_bytes(data[1:3], "little") == BOT
    # type 10 in the high nibble; flags = cut | advisories | source 2 << 2.
    assert data[3] == (v5.TYPE_AREA_SWEEP << 4) | 0x1 | 0x2 | (2 << 2) == 0xAB
    assert int.from_bytes(data[4:8], "little") == NOW      # built, Unix minutes
    assert (data[8], data[9], data[10]) == (32, 1, 3)      # group, idx, total

    assert data[11] == 1                                   # event
    assert data[12] == (42 << 1) | 1                       # state 42, county
    assert int.from_bytes(data[13:15], "little") == 453 | (0 << 10)

    assert data[15] == 24
    assert data[16] == (7 << 1) | 0                        # forecast zone
    assert int.from_bytes(data[17:19], "little") == 1023 | (63 << 10)


def test_packet_roundtrips_and_a_full_packet_fits():
    entries = [
        _entry(1 + (i % 20), i % 128, i % 2 == 0, (i * 7) % 1024, 1 + i % 64)
        for i in range(v5.MAX_SWEEP_ENTRIES_PER_PACKET)
    ]
    data = v5.encode_area_sweep(
        7, BOT, built_min=NOW, group=7, idx=0, total=8, entries=entries
    )
    assert len(data) == 163 <= v5.MAX_DATA
    d = v5.decode(data)
    assert d["name"] == "area_sweep" and d["seq"] == 7 and d["bot"] == BOT
    assert (d["group"], d["idx"], d["total"]) == (7, 0, 8)
    assert d["built_min"] == NOW
    assert d["cut"] is False and d["advisories"] is False
    assert d["source"] == v5.SOURCE_UNSTATED
    assert [
        (e["event"], e["state"], e["county"], e["start"], e["run"])
        for e in d["entries"]
    ] == entries


def test_flags_are_independent():
    def flags(**kw):
        return v5.decode(
            v5.encode_area_sweep(
                1, BOT, built_min=NOW, group=1, idx=0, total=1,
                entries=[_entry(3, 1, False, 5, 1)], **kw
            )
        )

    assert (flags()["cut"], flags()["advisories"]) == (False, False)
    assert (flags(cut=True)["cut"], flags(cut=True)["advisories"]) == (True, False)
    d = flags(advisories=True, source=v5.SOURCE_GOES)
    assert (d["cut"], d["advisories"], d["source"]) == (False, True, v5.SOURCE_GOES)


@pytest.mark.parametrize(
    "kw",
    [
        {"total": 0}, {"total": 9}, {"idx": 3, "total": 3},
        {"entries": [_entry(3, 128, False, 1, 1)]},        # state over 7 bits
        {"entries": [_entry(3, 1, False, 1024, 1)]},       # start over 10 bits
        {"entries": [_entry(3, 1, False, 1, 65)]},         # run over 6 bits
        {"entries": [_entry(3, 1, False, 1, 0)]},          # a run is at least 1
        {"entries": [_entry(3, 1, False, 1, 1)] * 39},     # over 38 a packet
    ],
)
def test_encode_range_checks(kw):
    args = dict(
        built_min=NOW, group=1, idx=0, total=1,
        entries=[_entry(3, 1, False, 1, 1)],
    )
    args.update(kw)
    with pytest.raises(ValueError):
        v5.encode_area_sweep(1, BOT, **args)


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("207a4ca0c913c701201f"),          # header short of `total`
        bytes.fromhex("207a4ca0c913c70120000101c501"),  # a 3-byte entry
    ],
)
def test_truncated_sweep_raises(data):
    with pytest.raises(ValueError):
        v5.decode(data)


def test_sweep_packets_number_themselves_like_text():
    entries = [_entry(3, 42, True, n, 1) for n in range(1, 200, 2)]  # 100 runs
    msgs = v5.sweep_packets(254, BOT, built_min=NOW, entries=entries)
    decoded = [v5.decode(m) for m in msgs]
    assert [d["seq"] for d in decoded] == [254, 255, 0]   # the counter wraps
    assert {d["group"] for d in decoded} == {254}         # the first packet's seq
    assert [d["idx"] for d in decoded] == [0, 1, 2]
    assert {d["total"] for d in decoded} == {3}
    assert [len(d["entries"]) for d in decoded] == [38, 38, 24]
    assert v5.sweep_packets(1, BOT, built_min=NOW, entries=[]) == []


def test_sweep_packets_cut_beyond_eight_packets():
    entries = [_entry(3, 42, True, n, 1) for n in range(0, 1024, 2)]  # 512 runs
    msgs = v5.sweep_packets(1, BOT, built_min=NOW, entries=entries)
    decoded = [v5.decode(m) for m in msgs]
    assert len(msgs) == v5.MAX_SWEEP_PACKETS == 8
    assert sum(len(d["entries"]) for d in decoded) == v5.MAX_SWEEP_ENTRIES == 304
    # Set on EVERY packet, so losing the last one does not lose the fact.
    assert all(d["cut"] for d in decoded)
    # What was kept is the head of the list the caller ordered.
    kept = [e["start"] for d in decoded for e in d["entries"]]
    assert kept == [e[3] for e in entries[:304]]


# ---------------------------------------------------------------------------
# Building the sweep (spec 7C, the bot side)
# ---------------------------------------------------------------------------


def test_runs_split_at_sixty_four():
    """Six bits hold `run - 1`, so a hundred consecutive counties are two
    entries, not one."""
    entries = b.sweep_entries(
        [_warning(ugcs=[f"TXC{n:03d}" for n in range(1, 101)])]
    )
    tx = b.tables.states.index("TX")
    sv_w = b.tables.event_code("SV.W")
    assert entries == [
        (sv_w, tx, True, 1, 64),
        (sv_w, tx, True, 65, 36),
    ]


def test_each_area_keeps_the_most_severe_event_and_ties_go_to_the_newest():
    tx = b.tables.states.index("TX")
    # A watch over four counties, a warning over two of them: the warning wins
    # those two and the watch keeps the rest.
    entries = b.sweep_entries([
        _warning("SV", "A", [f"TXC{n:03d}" for n in (453, 454, 455, 456)], onset=400),
        _warning("TO", "W", ["TXC453", "TXC454"], onset=500),
    ])
    assert entries == [
        (b.tables.event_code("TO.W"), tx, True, 453, 2),
        (b.tables.event_code("SV.A"), tx, True, 455, 2),
    ]
    # Same significance: the newer onset takes the area.
    entries = b.sweep_entries([
        _warning("SV", "W", ["TXC453"], onset=100),
        _warning("FF", "W", ["TXC453"], onset=900),
    ])
    assert entries == [(b.tables.event_code("FF.W"), tx, True, 453, 1)]


def test_entries_are_ordered_most_severe_then_state_then_start():
    tx, ok = b.tables.states.index("TX"), b.tables.states.index("OK")
    entries = b.sweep_entries(
        [
            _warning("SV", "A", ["OKC101"]),
            _warning("HT", "Y", ["TXZ191"]),
            _warning("WS", "W", ["OKZ010"]),
            _warning("TO", "W", ["TXC453"]),
            _warning("TO", "A", ["TXC100"]),
            _warning("SV", "W", ["TXC209"]),
        ],
        advisories=True,
    )
    rank = {"W": 0, "A": 1, "Y": 2, "S": 3}
    order = [
        (rank[b._event_significance(e[0])], e[1], e[3]) for e in entries
    ]
    assert order == sorted(order)
    # Severity first, whatever the state: both warnings precede both watches,
    # and the lone advisory is last.
    assert [b.tables.states[e[1]] for e in entries][:2] in (
        ["TX", "OK"], ["OK", "TX"]
    )
    assert [e[0] for e in entries][-1] == b.tables.event_code("HT.Y")
    # Inside one severity, by state index and then by the run's first number.
    warnings = [e for e in entries if b._event_significance(e[0]) == "W"]
    assert warnings == sorted(warnings, key=lambda e: (e[1], e[3]))
    assert {e[1] for e in entries} == {tx, ok}


def test_advisories_are_only_in_an_all_sweep():
    w = [
        _warning("TO", "W", ["TXC453"]),
        _warning("SV", "A", ["TXC454"]),
        _warning("HT", "Y", ["TXZ191"]),
        _warning("AF", "Y", ["TXZ192"]),
    ]
    plain = {b._event_significance(e[0]) for e in b.sweep_entries(w)}
    every = {b._event_significance(e[0]) for e in b.sweep_entries(w, advisories=True)}
    assert plain == {"W", "A"}
    assert every == {"W", "A", "Y"}


def test_unknown_events_and_unusable_ugcs_are_dropped():
    tx = b.tables.states.index("TX")
    entries = b.sweep_entries([
        # An event the tables do not know is not sent as 0, which would read
        # as "unknown event" over a real county.
        dict(_warning(ugcs=["TXC453"]), event_code=0),
        _warning(ugcs=["TXC454", "XXC001", "TX453", "TXQ001", "TXC12a", "ZZC001"]),
    ])
    assert entries == [(b.tables.event_code("SV.W"), tx, True, 454, 1)]
    assert b.sweep_entries([]) == []


# ---------------------------------------------------------------------------
# `>wmap` (spec 7C and 8.2) and the airtime limits
# ---------------------------------------------------------------------------


def _responder(monkeypatch, active=None):
    """An AppResponder whose radio keeps what it was handed, and whose store
    answers with `active` however empty it really is."""
    import meshcore_weather.schedule.scheduler as sched_mod
    from meshcore_weather.parser.weather import WeatherStore
    from meshcore_weather.protocol import warnings as warnings_mod
    from meshcore_weather.protocol.broadcaster import AppResponder

    monkeypatch.setattr(sched_mod, "TX_SPACING", 0)
    monkeypatch.setattr(
        warnings_mod, "extract_active_warnings",
        lambda store, coverage=None, now=None: list(active or []),
    )
    radio = MagicMock()
    radio._mc = MagicMock()
    radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
    sent = []

    async def cap(data, data_type=0xFF10, ev=None):
        sent.append(data)
        return True

    radio.send_channel_data = cap
    return AppResponder(WeatherStore(), radio, render_text=lambda c, a: None), sent


def _sweep_of(sent):
    return [v5.decode(m) for m in sent if m[3] >> 4 == v5.TYPE_AREA_SWEEP]


@pytest.mark.asyncio
async def test_wmap_answers_a_sweep_stamped_with_one_group(monkeypatch):
    active = [
        _warning("TO", "W", ["TXC453", "TXC454"], onset=500),
        _warning("SV", "A", [f"OKC{n:03d}" for n in range(1, 120, 2)], onset=400),
    ]
    r, sent = _responder(monkeypatch, active)
    out = await r.handle_request(">wmap", "a")
    packets = _sweep_of(sent)
    # 1 Texas run + 60 single-county Oklahoma runs: two packets.
    assert len(packets) == len(sent) == 2 and "packet(s)" in out
    # The group is the seq the first packet went out with (spec 7C), stamped
    # by the scheduler like a Text reply's, and idx runs 0..total-1.
    assert {p["group"] for p in packets} == {packets[0]["seq"]}
    assert [p["idx"] for p in packets] == list(range(len(packets)))
    assert {p["total"] for p in packets} == {len(packets)}
    assert not any(p["cut"] for p in packets)
    assert not any(p["advisories"] for p in packets)
    assert abs(packets[0]["built_min"] - b.now_min()) <= 1
    tx = b.tables.states.index("TX")
    first = packets[0]["entries"][0]
    assert (first["event"], first["state"], first["county"], first["start"],
            first["run"]) == (b.tables.event_code("TO.W"), tx, True, 453, 2)


@pytest.mark.asyncio
async def test_wmap_all_adds_advisories_and_sets_the_flag(monkeypatch):
    active = [
        _warning("TO", "W", ["TXC453"]),
        _warning("HT", "Y", ["TXZ191"]),
    ]
    r, sent = _responder(monkeypatch, active)
    await r.handle_request(">wmap", "a")
    plain = _sweep_of(sent)
    assert len(plain) == 1 and plain[0]["advisories"] is False
    assert [e["event"] for e in plain[0]["entries"]] == [b.tables.event_code("TO.W")]

    r._last_sweep = 0.0            # the ten-minute window is tested below
    await r.handle_request(">wmap all", "b")
    every = _sweep_of(sent)[1:]
    assert len(every) == 1 and every[0]["advisories"] is True
    assert [e["event"] for e in every[0]["entries"]] == [
        b.tables.event_code("TO.W"), b.tables.event_code("HT.Y"),
    ]


@pytest.mark.asyncio
async def test_one_sweep_every_five_minutes_across_all_senders(monkeypatch):
    from meshcore_weather.protocol.broadcaster import SWEEP_COOLDOWN_S

    # The owner's number (2026-09-20): long enough that two people never pay
    # twice for one picture, short enough that a refresh never feels blocked.
    assert SWEEP_COOLDOWN_S == 300.0
    r, sent = _responder(monkeypatch, [_warning("TO", "W", ["TXC453"])])
    await r.handle_request(">wmap", "a")
    assert len(_sweep_of(sent)) == 1

    # A different sender, so the 5-second per-sender limit is not what answers:
    # the window is across all senders.
    n = len(sent)
    await r.handle_request(">wmap", "b")
    assert len(sent) == n + 1 and len(_sweep_of(sent)) == 1
    d = v5.decode(sent[-1])
    assert (d["name"], d["request"], d["reason"]) == (
        "not_available", "w", v5.REASON_RATE_LIMITED
    )
    assert len(sent[-1]) == 6

    # Just inside the window, still rate limited; just outside, a sweep again.
    r._last_sweep = time.time() - (SWEEP_COOLDOWN_S - 1)
    await r.handle_request(">wmap", "c")
    assert v5.decode(sent[-1])["reason"] == v5.REASON_RATE_LIMITED
    r._last_sweep = time.time() - (SWEEP_COOLDOWN_S + 1)
    await r.handle_request(">wmap", "d")
    assert len(_sweep_of(sent)) == 2


@pytest.mark.asyncio
async def test_the_window_starts_when_the_sweep_went_out_not_when_asked(monkeypatch):
    """A sweep the radio refused must not lock out the next ten minutes."""
    r, sent = _responder(monkeypatch, [_warning("TO", "W", ["TXC453"])])

    async def refuse(data, data_type=0xFF10, ev=None):
        return False

    r.radio.send_channel_data = refuse
    await r.handle_request(">wmap", "a")
    assert r._last_sweep == 0.0

    r.radio.send_channel_data = (
        lambda data, data_type=0xFF10, ev=None: _accept(sent, data)
    )
    await r.handle_request(">wmap", "b")
    assert len(_sweep_of(sent)) == 1
    assert r._last_sweep > 0.0


async def _accept(sent, data):
    sent.append(data)
    return True


@pytest.mark.asyncio
async def test_a_sweep_does_not_start_without_eight_packets_of_budget(monkeypatch):
    from meshcore_weather.protocol.broadcaster import PER_HOUR

    r, sent = _responder(monkeypatch, [_warning("TO", "W", ["TXC453"])])
    # Seven packets of the hour's budget left: not enough for a whole sweep.
    r._sent.extend([time.time()] * (PER_HOUR - v5.MAX_SWEEP_PACKETS + 1))
    await r.handle_request(">wmap", "a")
    d = v5.decode(sent[-1])
    assert (d["name"], d["reason"]) == ("not_available", v5.REASON_RATE_LIMITED)
    assert _sweep_of(sent) == []
    assert r._last_sweep == 0.0

    # Exactly eight left, and it goes. (The Not available above spent one of
    # the hour's packets itself, so the budget is set again rather than popped.)
    r._sent.clear()
    r._sent.extend([time.time()] * (PER_HOUR - v5.MAX_SWEEP_PACKETS))
    await r.handle_request(">wmap", "b")
    assert len(_sweep_of(sent)) == 1


@pytest.mark.asyncio
async def test_a_sweep_with_nothing_active_is_not_available(monkeypatch):
    r, sent = _responder(monkeypatch, [])
    await r.handle_request(">wmap", "a")
    d = v5.decode(sent[-1])
    assert (d["name"], d["request"], d["reason"]) == (
        "not_available", "w", v5.REASON_NO_DATA
    )
    # Nothing went out, so the next request is not inside a ten-minute window.
    assert r._last_sweep == 0.0


@pytest.mark.asyncio
async def test_a_national_sweep_is_capped_at_eight_packets_and_flagged_cut(monkeypatch):
    """450 runs do not fit in 304 entries; the least severe are what go."""
    active = [
        _warning("SV", "W", [f"TXC{n:03d}" for n in range(1, 300, 2)]),   # 150
        _warning("SV", "A", [f"OKC{n:03d}" for n in range(1, 600, 2)]),   # 300
    ]
    r, sent = _responder(monkeypatch, active)
    await r.handle_request(">wmap", "a")
    packets = _sweep_of(sent)
    assert len(packets) == v5.MAX_SWEEP_PACKETS == 8
    assert all(p["cut"] for p in packets)
    assert {p["total"] for p in packets} == {8}
    entries = [e for p in packets for e in p["entries"]]
    assert len(entries) == v5.MAX_SWEEP_ENTRIES == 304
    assert all(len(m) <= v5.MAX_DATA for m in sent)
    # Every warning survived; the watches were cut from the tail.
    sv_w, sv_a = b.tables.event_code("SV.W"), b.tables.event_code("SV.A")
    assert sum(1 for e in entries if e["event"] == sv_w) == 150
    assert sum(1 for e in entries if e["event"] == sv_a) == 154
    assert [e["event"] for e in entries[:150]] == [sv_w] * 150
