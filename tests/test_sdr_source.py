"""SDRSource: the goestools output directory as the bot's EMWIN feed."""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meshcore_weather.emwin.fetcher import InternetSource, SDRSource, parse_emwin_file

FIX = Path(__file__).parent / "fixtures" / "swpc"


def _name(ts: datetime, awips: str, seq: int = 1) -> str:
    return f"A_WOXX20KWNP{ts:%d%H%M}_C_KWIN_{ts:%Y%m%d%H%M%S}_{seq:06d}-1-{awips}.TXT"


def _write(root: Path, ts: datetime, awips: str, body: bytes, seq: int = 1, age_s: float = 10) -> Path:
    d = root / f"{ts:%Y-%m-%d}"
    d.mkdir(exist_ok=True)
    p = d / _name(ts, awips, seq)
    p.write_bytes(body)
    os.utime(p, (time.time() - age_s, time.time() - age_s))
    return p


def test_scan_picks_up_fresh_files_and_skips_old_and_unsettled(tmp_path):
    now = datetime.now(timezone.utc)
    body = (FIX / "WATA20US_20260914_1551Z.txt").read_bytes()
    fresh = _write(tmp_path, now - timedelta(minutes=5), "WATA20US", body)
    _write(tmp_path, now - timedelta(hours=13), "WATA20US", body, seq=2)   # expired
    _write(tmp_path, now - timedelta(minutes=1), "ALTEF3US", body, seq=3, age_s=0)  # still being written
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "2026-01-01").mkdir()                                       # old day dir, never scanned
    src = SDRSource(root=tmp_path)
    assert src.scan() == 1
    prods = asyncio.run(src.fetch_products())
    assert [p["filename"] for p in prods] == [fresh.name]
    assert prods[0]["awips_id"] == "WATA20US" and prods[0]["station"] == "KWNP"
    assert "\r\r\n" in prods[0]["raw_text"], "bodies must keep EMWIN line endings"
    # Second scan: the unsettled file has settled, nothing else is new
    os.utime(fresh.parent / _name(now - timedelta(minutes=1), "ALTEF3US", 3), (time.time() - 5,) * 2)
    assert src.scan() == 1
    assert src.scan() == 0


def test_expiry_drops_products_as_time_passes(tmp_path):
    now = datetime.now(timezone.utc)
    body = (FIX / "ALTEF3US_20260914_1106Z.txt").read_bytes()
    _write(tmp_path, now - timedelta(hours=11), "ALTEF3US", body)
    src = SDRSource(root=tmp_path)
    assert src.scan(now=now) == 1
    assert src.scan(now=now + timedelta(hours=2)) == 0
    assert asyncio.run(src.fetch_products()) == []


def test_start_requires_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        asyncio.run(SDRSource(root=tmp_path / "missing").start())


def test_parse_emwin_file_is_shared_with_zip_path():
    p = parse_emwin_file("A_WOXX20KWNP141551_C_KWIN_20260914155132_374891-1-WATA20US.TXT", "WOXX20 KWNP 141551\r\r\nWATA20")
    assert p["product_id"] == "WOXX20" and p["station"] == "KWNP" and p["awips_id"] == "WATA20US"
    assert p["timestamp"] == datetime(2026, 9, 14, 15, 51, 32, tzinfo=timezone.utc)
    assert p["source"] == ""                    # a caller that does not say


def test_each_source_stamps_its_own_products(tmp_path):
    """The wire's source field is only as honest as this stamp (spec 2.2.1)."""
    now = datetime.now(timezone.utc)
    body = (FIX / "WATA20US_20260914_1551Z.txt").read_bytes()
    _write(tmp_path, now - timedelta(minutes=5), "WATA20US", body)
    src = SDRSource(root=tmp_path)
    src.scan()
    assert [p["source"] for p in asyncio.run(src.fetch_products())] == ["sdr"]

    net = InternetSource()
    p = net._parse_emwin_file(
        "A_WOXX20KWNP141551_C_KWIN_20260914155132_374891-1-WATA20US.TXT", "WOXX20 KWNP"
    )
    assert p["source"] == "internet"


# ---------------------------------------------------------------------------
# The internet backup: NOAA's bundle while the dish is quiet
# ---------------------------------------------------------------------------


def _bundle(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _backup(tmp_path, monkeypatch, bundles):
    """A source over tmp dish/ and backup/ folders whose fetches answer from
    `bundles` (url -> bytes, or None for a failure) and are recorded."""
    from meshcore_weather.config import settings
    monkeypatch.setattr(settings, "sdr_internet_fallback_min", 10)
    monkeypatch.setattr(settings, "emwin_poll_interval", 120)
    dish, backup = tmp_path / "dish", tmp_path / "backup"
    dish.mkdir()
    src = SDRSource(root=dish, fallback_root=backup)
    asked = []

    async def fake_get(url):
        asked.append(url)
        return bundles.get(url)

    monkeypatch.setattr(src, "_get", fake_get)
    return src, dish, backup, asked


def test_the_backup_takes_noaa_while_the_dish_is_quiet_and_stops_when_it_writes(tmp_path, monkeypatch):
    from meshcore_weather.config import settings
    now = datetime.now(timezone.utc)
    body = (FIX / "WATA20US_20260914_1551Z.txt").read_bytes()
    alert = (FIX / "ALTEF3US_20260914_1106Z.txt").read_bytes()
    t_dish, t_new, t_later = now - timedelta(minutes=40), now - timedelta(minutes=12), now - timedelta(minutes=1)
    bundles = {
        settings.emwin_base_url: _bundle({_name(t_dish, "WATA20US"): body,          # the dish has this one
                                          _name(t_new, "ALTEF3US", 7): alert}),     # this came after it went quiet
        settings.emwin_poll_url: _bundle({_name(t_later, "WATA20US", 9): body}),
    }
    src, dish, backup, asked = _backup(tmp_path, monkeypatch, bundles)
    _write(dish, t_dish, "WATA20US", body, age_s=25 * 60)                          # the dish's last file, 25 min ago
    assert src.scan() == 1 and src.dish_quiet_min() >= 24

    added = asyncio.run(src.check_fallback(now=now))
    assert added == 1 and src.fallback_active and asked == [settings.emwin_base_url]
    prods = {p["filename"]: p for p in asyncio.run(src.fetch_products())}
    assert prods[_name(t_dish, "WATA20US")]["source"] == "sdr"                      # held once, as the dish gave it
    assert prods[_name(t_new, "ALTEF3US", 7)]["source"] == "internet"
    kept = backup / f"{t_new:%Y-%m-%d}" / _name(t_new, "ALTEF3US", 7)
    assert kept.read_bytes() == alert.decode("utf-8", errors="replace").strip().encode("utf-8")

    # Inside the poll interval nothing is fetched; past it, the 2-minute bundle.
    assert asyncio.run(src.check_fallback(now=now)) == 0 and len(asked) == 1
    src._fallback_fetched -= settings.emwin_poll_interval
    assert asyncio.run(src.check_fallback(now=now)) == 1 and asked[-1] == settings.emwin_poll_url
    assert src.fallback_added == 2

    # The dish writes again: the backup goes off and what it brought stays.
    _write(dish, now, "WATA20US", body, seq=11, age_s=5)
    src.scan()
    assert asyncio.run(src.check_fallback(now=now)) == 0 and not src.fallback_active
    assert len(asyncio.run(src.fetch_products())) == 4


def test_a_restart_in_an_outage_keeps_what_the_backup_brought(tmp_path, monkeypatch):
    from meshcore_weather.config import settings
    now = datetime.now(timezone.utc)
    alert = (FIX / "ALTEF3US_20260914_1106Z.txt").read_bytes()
    t_new = now - timedelta(minutes=12)
    src, dish, backup, _ = _backup(tmp_path, monkeypatch, {
        settings.emwin_base_url: _bundle({_name(t_new, "ALTEF3US", 7): alert})})
    asyncio.run(src.check_fallback(now=now))                                        # no dish file at all: quiet "ever"
    assert src.fallback_active
    for f in backup.rglob("*.TXT"):                                                  # written a while before the restart
        os.utime(f, (time.time() - 60, time.time() - 60))
    again = SDRSource(root=dish, fallback_root=backup)
    assert again.scan() == 1
    (prod,) = asyncio.run(again.fetch_products())
    assert prod["source"] == "internet" and "\r\r\n" in prod["raw_text"]
    assert again.newest_mtime is None                                               # a backup file is not the dish writing


def test_the_backup_can_be_turned_off_and_survives_a_failed_fetch(tmp_path, monkeypatch):
    from meshcore_weather.config import settings
    src, dish, backup, asked = _backup(tmp_path, monkeypatch, {})                   # every fetch fails
    assert asyncio.run(src.check_fallback()) == 0 and src.fallback_active and len(asked) == 1
    monkeypatch.setattr(settings, "sdr_internet_fallback_min", 0)
    off = SDRSource(root=dish, fallback_root=backup)
    assert asyncio.run(off.check_fallback()) == 0 and not off.fallback_active
    assert SDRSource(root=dish).fallback_root is None                               # a test source has no backup folder


def test_old_backup_days_are_pruned(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    src, dish, backup, _ = _backup(tmp_path, monkeypatch, {})
    (backup / "2026-01-01").mkdir(parents=True)
    (backup / f"{now:%Y-%m-%d}").mkdir()
    src._take_internet([], now)
    assert sorted(d.name for d in backup.iterdir()) == [f"{now:%Y-%m-%d}"]
