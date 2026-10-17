"""The EMWIN feed's health (emwin/fetcher.py SDRSource): what counts as the
dish writing, the backup's hysteresis and its refetch of the hour bundle,
the record of the last fetch, and how far behind the uplink the products run.

The Oct 14-16 outage and the replay of Oct 16 are the cases: NOAA's server
answered truncated ZIPs and 403s a few times an hour, and for ten hours
every product arrived hours after its creation time while every file was
seconds old."""

import asyncio
import io
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from meshcore_weather.config import Settings, settings
from meshcore_weather.emwin.fetcher import HOUR_BUNDLE_EVERY_S, SDRSource

FIX = Path(__file__).parent / "fixtures" / "swpc"
BODY = (FIX / "WATA20US_20260914_1551Z.txt").read_bytes()
ALERT = (FIX / "ALTEF3US_20260914_1106Z.txt").read_bytes()


def _name(ts: datetime, awips: str, seq: int = 1, tag: str = "KWIN") -> str:
    return f"A_WOXX20KWNP{ts:%d%H%M}_C_{tag}_{ts:%Y%m%d%H%M%S}_{seq:06d}-1-{awips}.TXT"


def _write(root: Path, ts: datetime, awips: str, seq: int = 1, age_s: float = 10,
           tag: str = "KWIN", body: bytes = BODY, name: str | None = None) -> Path:
    d = root / f"{ts:%Y-%m-%d}"
    d.mkdir(exist_ok=True)
    p = d / (name or _name(ts, awips, seq, tag))
    p.write_bytes(body)
    os.utime(p, (time.time() - age_s, time.time() - age_s))
    return p


def _bundle(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _backup(tmp_path, monkeypatch, bundles: dict, failing: set | None = None):
    """A source over tmp dish/ and backup/ folders whose fetches answer from
    `bundles` (url -> bytes), fail for the urls in `failing`, and are recorded."""
    monkeypatch.setattr(settings, "sdr_internet_fallback_min", 10)
    monkeypatch.setattr(settings, "emwin_poll_interval", 120)
    dish, backup = tmp_path / "dish", tmp_path / "backup"
    dish.mkdir()
    src = SDRSource(root=dish, fallback_root=backup)
    asked: list[str] = []
    failing = failing if failing is not None else set()

    async def fake_get(url):
        asked.append(url)
        return None if url in failing else bundles.get(url)

    monkeypatch.setattr(src, "_get", fake_get)
    return src, dish, backup, asked, failing


def _due(src: SDRSource) -> None:
    """The poll interval has passed since the last fetch."""
    src._fallback_fetched -= settings.emwin_poll_interval


# -- what counts as the dish writing -------------------------------------------


def test_only_the_dishs_own_files_count_as_the_dish_writing(tmp_path):
    now = datetime.now(timezone.utc)
    src = SDRSource(root=tmp_path)
    # A `_C_INET_` file in the dish's directory (a disabled backup once left
    # some) is a product, but no sign of life from the dish.
    _write(tmp_path, now - timedelta(minutes=5), "WATA20US", tag="INET")
    assert src.scan() == 1
    assert src.newest_mtime is None and src.dish_quiet_min() == float("inf")
    assert src.dish_files_recent() == 0 and src.feed_lag_min is None and src.newest_product_ts is None
    _write(tmp_path, now - timedelta(minutes=4), "WATA20US", seq=2)
    assert src.scan() == 1
    assert src.newest_mtime == pytest.approx(time.time() - 10, abs=2)
    assert src.dish_files_recent() == 1 and src.dish_quiet_min() < 1


def test_the_default_needs_a_steady_dish_before_the_backup_goes_off():
    assert Settings.model_fields["sdr_resume_min_files"].default == 20
    assert Settings.model_fields["sdr_internet_fallback_min"].default == 10


# -- the backup's hysteresis --------------------------------------------------


def test_a_trickle_from_a_marginal_lock_keeps_the_backup_on(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sdr_resume_min_files", 3)
    now = datetime.now(timezone.utc)
    src, dish, _, asked, _ = _backup(tmp_path, monkeypatch, {
        settings.emwin_base_url: _bundle({_name(now - timedelta(minutes=12), "ALTEF3US", 7): ALERT}),
        # The same product every time: a bundle that came in whole, nothing new.
        settings.emwin_poll_url: _bundle({_name(now - timedelta(minutes=12), "ALTEF3US", 7): ALERT}),
    })
    assert asyncio.run(src.check_fallback(now=now)) == 1 and src.fallback_active

    # One file, then another a while later: the dish is writing, but not
    # steadily. The backup stays on and keeps polling.
    for seq, age in ((11, 240), (12, 60)):
        _write(dish, now, "WATA20US", seq=seq, age_s=age)
        src.scan()
        assert src.dish_quiet_min() < settings.sdr_internet_fallback_min
        _due(src)
        asyncio.run(src.check_fallback(now=now))
        assert src.fallback_active, f"still on after {src.dish_files_recent()} files"
    assert asked[-1] == settings.emwin_poll_url

    # The third file inside ten minutes makes the dish steady: off.
    _write(dish, now, "WATA20US", seq=13, age_s=5)
    src.scan()
    assert src.dish_files_recent() == 3
    assert asyncio.run(src.check_fallback(now=now)) == 0 and not src.fallback_active
    assert len(asyncio.run(src.fetch_products())) == 4         # what the backup brought stays

    # Quiet again for long enough: back on, and the hour bundle first.
    src.newest_mtime -= 11 * 60
    src._dish_mtimes.clear()
    asyncio.run(src.check_fallback(now=now))
    assert src.fallback_active and asked[-1] == settings.emwin_base_url


def test_old_writes_do_not_count_towards_a_steady_dish(tmp_path):
    now = datetime.now(timezone.utc)
    src = SDRSource(root=tmp_path)
    for seq in range(1, 4):
        _write(tmp_path, now - timedelta(minutes=30), "WATA20US", seq=seq, age_s=1800)
    assert src.scan() == 3
    assert src.dish_files_recent() == 0 and src.dish_quiet_min() == pytest.approx(30, abs=0.1)
    _write(tmp_path, now, "WATA20US", seq=9, age_s=120)
    src.scan()
    assert src.dish_files_recent() == 1 and src.dish_files_recent(window_s=60) == 0


# -- the hour bundle after a failure, and every half hour ---------------------


def test_a_failed_fetch_is_made_good_with_the_hour_bundle_and_nothing_is_held_twice(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    t1, t2, t3 = (now - timedelta(minutes=m) for m in (12, 8, 2))
    src, _, backup, asked, failing = _backup(tmp_path, monkeypatch, {
        settings.emwin_base_url: _bundle({_name(t1, "ALTEF3US", 1): ALERT, _name(t2, "WATA20US", 2): BODY}),
        settings.emwin_poll_url: _bundle({_name(t3, "WATA20US", 3): BODY}),
    })
    assert asyncio.run(src.check_fallback(now=now)) == 2 and asked == [settings.emwin_base_url]
    assert src.fallback_last_ok == now and src.fallback_last_error is None

    # The 2-minute bundle fails: nothing added, the failure on record, and
    # the next poll takes the hour bundle instead of the 2-minute one.
    failing.add(settings.emwin_poll_url)
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 0 and asked[-1] == settings.emwin_poll_url
    assert src.fallback_last_error == f"no response at {now:%H:%MZ}" and src.fallback_last_error_at == now
    assert src.fallback_last_ok == now                              # unchanged: nothing came
    failing.clear()
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 0 and asked[-1] == settings.emwin_base_url
    assert src.fallback_last_error is None                          # a bundle came in whole
    assert src.fallback_added == 2                                  # the same two, held once

    # Back to the 2-minute bundle; then, half an hour on, the hour bundle
    # again regardless, and still nothing twice.
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 1 and asked[-1] == settings.emwin_poll_url
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 0 and asked[-1] == settings.emwin_poll_url
    src._hour_fetched -= HOUR_BUNDLE_EVERY_S
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 0 and asked[-1] == settings.emwin_base_url
    assert src.fallback_added == 3 and len(asyncio.run(src.fetch_products())) == 3
    assert sorted(p.name for p in backup.rglob("*.TXT")) == sorted(
        [_name(t1, "ALTEF3US", 1), _name(t2, "WATA20US", 2), _name(t3, "WATA20US", 3)])

    # Inside the poll interval nothing is fetched, failure or not.
    failing.add(settings.emwin_base_url)
    n = len(asked)
    assert asyncio.run(src.check_fallback(now=now)) == 0 and len(asked) == n


def test_the_hour_bundle_failing_at_switch_on_is_tried_again_at_the_next_poll(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    src, _, _, asked, failing = _backup(tmp_path, monkeypatch,
                                        {settings.emwin_base_url: _bundle({_name(now, "ALTEF3US"): ALERT})},
                                        failing={settings.emwin_base_url})
    assert asyncio.run(src.check_fallback(now=now)) == 0 and src.fallback_active
    failing.clear()
    _due(src)
    assert asyncio.run(src.check_fallback(now=now)) == 1 and asked == [settings.emwin_base_url] * 2
    _due(src)
    asyncio.run(src.check_fallback(now=now))
    assert asked[-1] == settings.emwin_poll_url


# -- what the last fetch ran into -----------------------------------------------


def _source_over(transport_handler, tmp_path) -> SDRSource:
    src = SDRSource(root=tmp_path / "dish", fallback_root=tmp_path / "backup")
    (tmp_path / "dish").mkdir()
    src._client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    return src


def test_the_error_on_record_says_what_noaa_answered(tmp_path):
    now = datetime(2026, 10, 16, 14, 3, tzinfo=timezone.utc)
    answer = {"what": "403"}

    def handler(request: httpx.Request) -> httpx.Response:
        what = answer["what"]
        if what == "403":
            return httpx.Response(403)
        if what == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if what == "junk":
            return httpx.Response(200, content=b"<html>not a zip</html>")
        if what == "empty":
            return httpx.Response(200, content=_bundle({}))
        return httpx.Response(200, content=_bundle({_name(now, "ALTEF3US"): ALERT}))

    src = _source_over(handler, tmp_path)
    try:
        for what, said in (("403", "403 Forbidden"), ("timeout", "timeout"), ("junk", "invalid zip"),
                           ("empty", "empty bundle")):
            answer["what"] = what
            assert asyncio.run(src._fetch_internet(settings.emwin_poll_url, now)) is None, what
            assert src.fallback_last_error == f"{said} at 14:03Z", what
            assert src.fallback_last_ok is None
        answer["what"] = "ok"
        assert asyncio.run(src._fetch_internet(settings.emwin_poll_url, now)) == 1
        assert src.fallback_last_error is None and src.fallback_last_error_at is None
        assert src.fallback_last_ok == now
    finally:
        asyncio.run(src._client.aclose())


def test_the_backup_fetch_cannot_hold_the_dish_watcher_for_a_minute(tmp_path):
    client = SDRSource(root=tmp_path)._make_client()
    try:
        assert client.timeout == httpx.Timeout(10.0, connect=5.0)
    finally:
        asyncio.run(client.aclose())


# -- how far behind the uplink the products run ---------------------------------


def test_lag_is_the_median_over_the_dishs_recent_files(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    src = SDRSource(root=tmp_path)
    # Four files created five hours ago and written just now: a lag, but
    # not yet enough to go by.
    for i in range(4):
        _write(tmp_path, now - timedelta(hours=5, minutes=i), "WATA20US", seq=i, age_s=30 + i)
    assert src.scan() == 4 and src.feed_lag_min is None
    assert src.newest_product_ts == now - timedelta(hours=5)
    # The fifth makes five: ~300 minutes behind.
    _write(tmp_path, now - timedelta(hours=5, minutes=4), "WATA20US", seq=4, age_s=34)
    assert src.scan() == 1 and 298 <= src.feed_lag_min <= 302
    # A current product written now moves the newest creation time, not the
    # median; a file created after it was written (a clock out of step) is
    # not a negative lag.
    _write(tmp_path, now - timedelta(seconds=20), "WATA20US", seq=5, age_s=15)
    _write(tmp_path, now + timedelta(minutes=2), "WATA20US", seq=6, age_s=15)
    src.scan()
    assert src.newest_product_ts == now + timedelta(minutes=2)
    assert 298 <= src.feed_lag_min <= 302 and min(lag for _, lag in src._lag_samples) == 0.0
    # Current files outnumbering the late ones bring it down.
    for i in range(7, 16):
        _write(tmp_path, now - timedelta(seconds=10 * i), "WATA20US", seq=i, age_s=5)
    src.scan()
    assert src.feed_lag_min < 5


def test_the_backlog_at_start_does_not_say_how_late_the_feed_is_now(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    src = SDRSource(root=tmp_path)
    # Files from hours ago, created hours before they were written: the
    # replay of the night. They set the newest creation time and nothing else.
    for i in range(6):
        _write(tmp_path, now - timedelta(hours=8, minutes=i), "WATA20US", seq=i, age_s=3 * 3600 + i)
    assert src.scan() == 6
    assert src.feed_lag_min is None and len(src._lag_samples) == 0
    assert src.newest_product_ts == now - timedelta(hours=8)


def test_a_malformed_name_neither_breaks_the_scan_nor_counts(tmp_path):
    now = datetime.now(timezone.utc)
    src = SDRSource(root=tmp_path)
    _write(tmp_path, now, "WATA20US", name="A_WOXX20KWNP_C_KWIN_NOTATIME_000001-1-WATA20US.TXT")
    _write(tmp_path, now, "WATA20US", name="A_WOXX20KWNP_C_KWIN_20261399999999_000002-1-WATA20US.TXT")
    assert src.scan() == 2                      # both are still products
    assert src.newest_mtime is not None         # written by the dish, by their tag
    assert src.newest_product_ts is None and src.feed_lag_min is None and len(src._lag_samples) == 0


def test_the_lag_of_a_replay_that_has_ended_is_not_reported_as_current(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    src = SDRSource(root=tmp_path)
    for i in range(6):
        _write(tmp_path, now - timedelta(hours=5, minutes=i), "WATA20US", seq=i, age_s=60 + i)
    src.scan()
    assert 298 <= src.feed_lag_min <= 306
    # The dish then goes quiet: an hour on, those samples are history.
    src._lag_samples = type(src._lag_samples)(((m - 3600, lag) for m, lag in src._lag_samples),
                                              maxlen=src._lag_samples.maxlen)
    assert src.feed_lag_min is None
