"""EMWIN product sources: NOAA's internet bundles (InternetSource), or the
goestools output directory fed by a GOES satellite receiver (SDRSource),
which takes the same internet bundles as its backup while the dish is quiet.

Both take NOAA's bundles the same way (_NoaaBundles): the last hour's bundle
first, then the 2-minute one every poll, the hour's again after any fetch
that failed and every half hour regardless; the file-name dedup makes a
product that comes twice cost nothing. Products are held for their retention
(emwin/retention.py: 12 hours, warnings and forecasts longer).

The EMWIN ZIP bundles use the same file names and bodies as the GOES
satellite downlink, so the parser cannot tell the two apart.
"""

import asyncio
import io
import json
import logging
import os
import random
import re
import shutil
import statistics
import threading
import time
import zipfile
from abc import ABC, abstractmethod
from collections import deque
from datetime import UTC, datetime, timezone, timedelta
from pathlib import Path

import httpx

from meshcore_weather import __version__
from meshcore_weather.config import settings
from meshcore_weather.emwin.nws_metar import NwsMetarFeed
from meshcore_weather.emwin.retention import is_expired, longest_hours, max_age_hours

logger = logging.getLogger(__name__)

EMWIN_FILENAME_RE = re.compile(
    r"A_(\w{4,6})(\w{4})(\d{6,8})_C_KWIN_\d+_\d+-\d+-(\w+)\.TXT",
    re.IGNORECASE,
)

EMWIN_TS_RE = re.compile(r"_(\d{14})_")

# The dish's own files carry the uplink's station tag and, after it, the
# product's creation time: `..._C_KWIN_20261016095401_...`. A file written by
# anything else (a disabled backup once left `_C_INET_` files in the dish's
# directory) is a product, but not a sign of life from the dish.
KWIN_TS_RE = re.compile(r"_C_KWIN_(\d{14})_")

# While the internet is the feed (InternetSource, or the dish's backup), the
# last-hour bundle is taken again this often, and after any fetch that
# failed: NOAA's server answered a truncated ZIP or 403 one to three times an
# hour during the Oct 14-16 outage, and 200 with 0 bytes twice on Oct 17 at
# 20:24Z, and each failed 2-minute bundle used to be lost for good.
HOUR_BUNDLE_EVERY_S = 1800

# A fetch that hangs gives up after this (per read, 5 s to connect): the
# 2-minute bundle is ~43 KB, and a WAN that hung used to hold a poll a minute.
FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# NOAA refusing (403), throttling (429) or failing (5xx): the internet source
# waits twice as long after each such answer, up to this, and goes back to
# the poll interval at the first bundle that comes in whole. Every wait is
# moved by up to POLL_JITTER either way, so bots started together (a power
# cut, a release) do not poll in step.
BACKOFF_MAX_S = 900
POLL_JITTER = 0.10

PROJECT_URL = "https://github.com/digitaino/meshwx"


def user_agent() -> str:
    """How the bot names itself to NOAA's server: the project, its version
    and, when MCW_OPERATOR_CONTACT is set, whom to ask, so an administrator
    who sees the traffic can write before blocking it. (httpx sent
    "python-httpx/x" before, which says nothing.)"""
    contact = "".join(c for c in (getattr(settings, "operator_contact", "") or "")
                      if " " <= c <= "~" and c not in "()").strip()
    return f"meshcore-weather/{__version__} (+{PROJECT_URL}{'; ' + contact if contact else ''})"


def read_bundle(data: bytes, source: str = "internet") -> list[dict]:
    """An EMWIN ZIP bundle (zips inside zips allowed) -> product dicts."""
    products = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".zip"):
                    try:
                        products.extend(read_bundle(zf.read(name), source))
                    except Exception:
                        pass
                elif name.lower().endswith(".txt"):
                    try:
                        raw_text = zf.read(name).decode("utf-8", errors="replace").strip()
                        if raw_text:
                            prod = parse_emwin_file(name, raw_text, source=source)
                            if prod:
                                products.append(prod)
                    except Exception:
                        pass
    except zipfile.BadZipFile:
        logger.warning("Invalid ZIP data")
    return products


class EMWINSource(ABC):
    @abstractmethod
    async def fetch_products(self) -> list[dict]:
        ...

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...


CACHE_FILE = Path(settings.data_dir) / "emwin_cache" / "products.jsonl"


def parse_emwin_file(filename: str, raw_text: str, source: str = "") -> dict | None:
    """Filename + body -> product dict. Shared by the zip and directory sources.

    `source` is how the bot got the file — "sdr" off its own GOES dish,
    "internet" from NOAA — and rides through the store onto the wire
    (spec 2.2.1, revision 7). Each source stamps its own products; a caller
    that does not say leaves it unstated.
    """
    product_id = "UNKNOWN"
    station = "UNKNOWN"
    awips_id = ""

    m = EMWIN_FILENAME_RE.search(filename)
    if m:
        product_id = m.group(1)
        station = m.group(2)
        awips_id = m.group(4)

    if product_id == "UNKNOWN":
        for line in raw_text.splitlines()[:5]:
            parts = line.strip().split()
            if len(parts) >= 2 and len(parts[0]) >= 4 and parts[0].isalnum():
                product_id = parts[0]
                station = parts[1]
                break

    # Extract timestamp from filename
    ts = datetime.now(timezone.utc)
    m_ts = EMWIN_TS_RE.search(filename)
    if m_ts:
        try:
            ts = datetime.strptime(m_ts.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return {
        "product_id": product_id,
        "station": station,
        "awips_id": awips_id,
        "timestamp": ts,
        "raw_text": raw_text,
        "filename": filename,
        "source": source,
    }


# The internet source's cache file is a journal: each poll appends the
# products it brought, and the file is rewritten as the live products alone
# once it holds more than CACHE_COMPACT_FACTOR lines per live product plus
# CACHE_COMPACT_SLACK. At the dish's measured 21.6 files a minute that is an
# append of ~85 KB a minute and a rewrite about twice a day; the whole store
# (~100 MB) used to be rewritten on the event loop after every poll.
CACHE_COMPACT_FACTOR = 2
CACHE_COMPACT_SLACK = 2000


def _cache_line(prod: dict) -> str:
    rec = dict(prod)
    rec["timestamp"] = rec["timestamp"].isoformat()
    return json.dumps(rec) + "\n"


class _NoaaBundles:
    """NOAA's internet bundles, taken the same way by both sources.

    The first fetch is the last hour's bundle (`emwin_base_url`), then the
    2-minute one (`emwin_poll_url`) at every poll. A fetch that fails (no
    answer, an error status, a timeout, 0 bytes, not a ZIP — a truncated one
    is not — or a ZIP with no product in it) is made good by the hour's
    bundle at the next poll, and the hour's is taken every
    HOUR_BUNDLE_EVERY_S regardless. Products are keyed by file name, so a
    bundle taken twice holds nothing twice.

    A class using this sets `_client`, `_hour_fetched`, `_refetch_hour`,
    `_get_error`, `_get_status`, `_lag_samples` and `newest_product_ts`, and
    says what a failed fetch leaves on record (`_fetch_failed`).
    """

    _net_label = "EMWIN internet"          # how its log lines start

    def _make_client(self, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        # `transport` is for tests (httpx.MockTransport); the bot passes none.
        return httpx.AsyncClient(timeout=FETCH_TIMEOUT, headers={"User-Agent": user_agent()},
                                 transport=transport)

    async def _get(self, url: str) -> bytes | None:
        if self._client is None:
            self._client = self._make_client()
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.TimeoutException:
            self._get_error = "timeout"
        except httpx.HTTPStatusError as e:
            self._get_status = e.response.status_code
            self._get_error = f"{e.response.status_code} {e.response.reason_phrase}".strip()
        except httpx.HTTPError as e:
            self._get_error = type(e).__name__
        logger.warning("%s: %s (%s)", self._net_label, self._get_error, url)
        return None

    def _bundle_url(self, mono: float) -> str:
        """The bundle the next fetch takes."""
        hour_due = self._hour_fetched is None or mono - self._hour_fetched >= HOUR_BUNDLE_EVERY_S
        return settings.emwin_base_url if self._refetch_hour or hour_due else settings.emwin_poll_url

    def _bundle_done(self, url: str, ok: bool, mono: float) -> None:
        if not ok:
            self._refetch_hour = True              # make it good with the hour bundle next time
        elif url == settings.emwin_base_url:
            self._hour_fetched = mono
            self._refetch_hour = False

    async def _read_internet(self, url: str, now: datetime) -> list[dict] | None:
        """The bundle at `url` as product dicts, or None when the fetch failed."""
        self._get_error = None
        self._get_status = None
        data = await self._get(url)
        if not data:
            # 200 with 0 bytes: twice on 2026-10-17 at 20:24Z.
            what = self._get_error or ("0 bytes" if data is not None else "no response")
        elif not zipfile.is_zipfile(io.BytesIO(data)):
            what = "invalid zip"
        else:
            products = await asyncio.get_running_loop().run_in_executor(None, read_bundle, data, "internet")
            if products:
                return products
            what = "empty bundle"
        if self._get_error is None:                # _get has said its own
            logger.warning("%s: %s (%s)", self._net_label, what, url)
        self._fetch_failed(what, now)
        return None

    def _fetch_failed(self, what: str, now: datetime) -> None:
        raise NotImplementedError

    # -- how late the products are ----------------------------------------------

    @property
    def feed_lag_min(self) -> int | None:
        """How far behind the uplink is running, in minutes: the median over
        the last 50 products that arrived fresh, once there are five to go
        by. Only arrivals in the last hour count: a feed that has gone quiet
        says nothing about how late the uplink runs now, and the lag of a
        replay that has ended must not be reported as current."""
        since = time.time() - 3600
        # The newest 50 by arrival, not the last 50 appended: the startup
        # scan reads day folders in no particular order, and the replayed
        # files of an older folder read last would pass for the current lag
        # ("~5h behind" at a restart on 17 Oct while the feed ran 22 min late).
        recent = sorted((s for s in self._lag_samples if s[0] >= since), reverse=True)[:50]
        if len(recent) < 5:
            return None
        return int(statistics.median(lag for _, lag in recent))

    def _note_created(self, name: str) -> datetime | None:
        """The creation time in a product's name, kept when it is the newest."""
        m = KWIN_TS_RE.search(name)
        if not m:
            return None
        try:
            created = datetime.strptime(m.group(1) + "+0000", "%Y%m%d%H%M%S%z")    # the name's time is UTC
        except ValueError:
            return None
        if self.newest_product_ts is None or created > self.newest_product_ts:
            self.newest_product_ts = created
        return created

    # -- NWS's own METAR feed (emwin/nws_metar.py) ----------------------------

    _nws: "NwsMetarFeed | None" = None

    async def _poll_nws_metar(self, now: datetime) -> list[dict]:
        """New files from NWS's METAR feed as products, when it is on and due.

        Off (`nws_metar_enabled`) drops the feed's state, so switching it on
        again starts over with the last 75 minutes. It shares the bundles'
        client, its timeouts and its User-Agent."""
        if not settings.nws_metar_enabled:
            if self._nws is not None:
                logger.info("NWS METAR feed off")
            self._nws = None
            return []
        if self._nws is None:
            self._nws = NwsMetarFeed()
            logger.info("NWS METAR feed on: current airport reports from %s", self._nws.url)
        if not self._nws.due(now):
            return []
        if self._client is None:
            self._client = self._make_client()
        return await self._nws.poll(self._client, now)

    def _note_lag(self, name: str, mtime: float, wall: float) -> None:
        """A product that arrived at `mtime`: how long after its creation."""
        created = self._note_created(name)
        if created is None:
            return
        # The backlog at start is days of files in no particular order; only
        # what arrived in the last hour says anything about now.
        if mtime >= wall - 3600:
            self._lag_samples.append((mtime, max(0.0, (mtime - created.timestamp()) / 60)))


class InternetSource(_NoaaBundles, EMWINSource):
    """NOAA's EMWIN bundles over the internet: the feed of every bot without
    a dish.

    **Fetching.** The hour's bundle at start, then the 2-minute bundle every
    `emwin_poll_interval`, with the hour's again after any failed fetch and
    every half hour (_NoaaBundles). When NOAA answers 403, 429 or 5xx the
    wait doubles with each such answer, up to BACKOFF_MAX_S, until a bundle
    comes in whole; every wait is moved by up to ±POLL_JITTER. Requests name
    the bot and its operator (`user_agent`).

    **Health**, under SDRSource's names where they mean the same:
    `feed_lag_min` (median minutes between a product's creation and its
    arrival in the 2-minute bundle; the hour's bundle is catch-up and does
    not count), `newest_product_ts`, `last_ok` (the last fetch that brought
    a product) and `last_error` / `last_error_at` ("403 Forbidden at
    14:03Z", until a bundle comes in whole).

    **The cache file** (data/emwin_cache/products.jsonl, one product per
    line) lets a restart answer at once. Each poll appends what it brought,
    in the executor; the file is rewritten as the live products, through a
    temp file and os.replace, only once it has grown to twice their number
    (CACHE_COMPACT_FACTOR). A line torn by a crash is skipped at load.
    """

    _net_label = "EMWIN internet"

    def __init__(self, cache_file: Path | None = None):
        self.cache_file = Path(cache_file) if cache_file is not None else \
            Path(settings.data_dir) / "emwin_cache" / "products.jsonl"
        self._client: httpx.AsyncClient | None = None
        self._products: dict[str, dict] = {}  # keyed by filename for dedup
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._rng = random.Random()
        # The fetching (_NoaaBundles)
        self._hour_fetched: float | None = None       # monotonic time the hour bundle last came in whole
        self._refetch_hour = False                    # a fetch failed: take the hour bundle next
        self._get_error: str | None = None            # what the last _get ran into, for the record
        self._get_status: int | None = None           # ... and the HTTP status NOAA answered with
        self._refusals = 0                            # 403/429/5xx in a row: the backoff
        # Health
        self.last_ok: datetime | None = None          # last fetch that brought a product
        self.last_error: str | None = None            # "403 Forbidden at 14:03Z", until a bundle comes in whole
        self.last_error_at: datetime | None = None
        self.newest_product_ts: datetime | None = None  # the newest creation time NOAA gave us
        self._lag_samples: deque[tuple[float, float]] = deque(maxlen=1000)   # (arrival, minutes behind)
        # The cache file
        self._cache_lock = threading.Lock()
        self._unsaved: list[dict] = []    # taken, not yet on the file
        self._cache_lines = 0             # lines in the file, live or not
        self._cache_loaded = False        # it was read: only then may it be rewritten
        self._cache_dirty = False         # a write failed: rewrite it whole next time
        self._cache_torn = False          # it ends mid-line (a crash in an append)

    async def start(self) -> None:
        if self._client is None:
            self._client = self._make_client()
        self._running = True

        # Restore cached products from disk. In a thread: it is thousands of
        # JSON lines, and the radio is already listening by now (main.py
        # loads the backlog behind the radio).
        await asyncio.get_running_loop().run_in_executor(None, self._load_cache)

        # The first fetch is the last hour's bundle, for the time the bot was down.
        try:
            await self.poll_once()
        except Exception:
            logger.exception("Error in the first EMWIN fetch")

        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "EMWIN source started (%d products, polling every %ds)",
            len(self._products),
            settings.emwin_poll_interval,
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client is None:
            return          # never started
        # Every product went to the file as it came; this is only what a
        # cancelled poll or a failed write left over.
        if self._unsaved or self._cache_dirty:
            await self._persist()
        await self._client.aclose()
        self._client = None
        logger.info("EMWIN source stopped (%d products in store)", len(self._products))

    # -- polling ----------------------------------------------------------------

    def poll_wait_s(self, jitter: bool = True) -> float:
        """Seconds to the next poll: the interval, doubled for each refusal
        in a row up to BACKOFF_MAX_S, moved by up to ±POLL_JITTER."""
        interval = max(1.0, float(settings.emwin_poll_interval))
        wait = interval
        if self._refusals:
            wait = min(interval * 2 ** min(self._refusals, 16), max(float(BACKOFF_MAX_S), interval))
        if jitter:
            wait *= self._rng.uniform(1 - POLL_JITTER, 1 + POLL_JITTER)
        return wait

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.poll_wait_s())
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Error polling EMWIN data")

    async def poll_once(self, now: datetime | None = None) -> int | None:
        """One fetch of the bundle due, into the store and onto the cache
        file. Returns how many products were new, or None when it failed."""
        now = now or datetime.now(UTC)
        mono = time.monotonic()
        url = self._bundle_url(mono)
        new = await self._fetch(url, now)
        self._bundle_done(url, new is not None, mono)
        if new is not None:
            self._refusals = 0
        elif self._get_status is not None and (self._get_status in (403, 429) or self._get_status >= 500):
            self._refusals += 1
            logger.warning("EMWIN internet: NOAA answered %s; waiting ~%.0f s before the next poll",
                           self._get_error, self.poll_wait_s(jitter=False))
        try:
            self._take(await self._poll_nws_metar(now), now, fresh=False)
        except Exception:
            logger.exception("Error in the NWS METAR feed")
        self._expire_old(now)
        if new:
            logger.info("Poll: +%d new products (%d total)", len(new), len(self._products))
        await self._persist()
        return None if new is None else len(new)

    async def _fetch(self, url: str, now: datetime) -> list[dict] | None:
        """The bundle at `url` into the store: the products that were new,
        or None when the fetch failed."""
        products = await self._read_internet(url, now)
        if products is None:
            return None
        new = self._take(products, now, fresh=url != settings.emwin_base_url)
        self.last_error = self.last_error_at = None
        if new:
            self.last_ok = now
        return new

    def _fetch_failed(self, what: str, now: datetime) -> None:
        self.last_error = f"{what} at {now:%H:%MZ}"
        self.last_error_at = now

    def _take(self, products: list[dict], now: datetime, fresh: bool) -> list[dict]:
        """Hold the products not held yet. `fresh`: they came in the
        2-minute bundle, so their arrival says how late the uplink runs; the
        hour's bundle is catch-up and says nothing about that."""
        wall = now.timestamp()
        new: list[dict] = []
        for prod in products:
            name = prod.get("filename") or ""
            if not name or name in self._products:
                continue
            if is_expired((prod.get("awips_id") or "")[:3], prod["timestamp"], now):
                continue
            self._products[name] = prod
            new.append(prod)
            if fresh:
                self._note_lag(name, wall, wall)
            else:
                self._note_created(name)
        self._unsaved.extend(new)
        return new

    def _expire_old(self, now: datetime | None = None) -> None:
        """Remove products past their retention (warnings keep longer)."""
        now = now or datetime.now(UTC)
        before = len(self._products)
        self._products = {
            k: v for k, v in self._products.items()
            if not is_expired((v.get("awips_id") or "")[:3], v.get("timestamp") or now, now)
        }
        removed = before - len(self._products)
        if removed:
            logger.debug("Expired %d old products", removed)

    # -- the cache file -----------------------------------------------------------

    async def _persist(self) -> None:
        """What came since the last write onto the cache file, off the event
        loop: appended, or the file rewritten as the live products once it
        has more than CACHE_COMPACT_FACTOR lines per live product (expired
        and torn lines only go then)."""
        compact = self._cache_loaded and (
            self._cache_dirty
            or self._cache_lines + len(self._unsaved)
            > CACHE_COMPACT_FACTOR * len(self._products) + CACHE_COMPACT_SLACK)
        if not compact and not self._unsaved:
            return
        new, self._unsaved = self._unsaved, []
        live = list(self._products.values()) if compact else None
        lines = await asyncio.get_running_loop().run_in_executor(None, self._write_cache, new, live)
        if lines is None:
            # Kept for the next write, as long as they are held: a file that
            # cannot be written for days must not hold every product ever taken.
            self._unsaved = [p for p in new + self._unsaved if p.get("filename") in self._products]
            self._cache_dirty = self._cache_loaded
        elif live is not None:
            self._cache_lines, self._cache_dirty = lines, False
        else:
            self._cache_lines += lines

    def _write_cache(self, new: list[dict], live: list[dict] | None) -> int | None:
        """In the executor: append `new`, or with `live` rewrite the file as
        exactly those (temp file, fsync, os.replace: a crash leaves the old
        file or the new one, never half of one). Returns the lines written,
        None when it could not."""
        path = self.cache_file
        tmp = path.with_name(path.name + ".tmp")
        with self._cache_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if live is not None:
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.writelines(_cache_line(p) for p in live)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                    self._cache_torn = False
                    logger.info("EMWIN cache: rewrote %s with the %d live products", path, len(live))
                    return len(live)
                with open(path, "a", encoding="utf-8") as f:
                    if self._cache_torn:
                        f.write("\n")          # the torn line stays one bad line, not two
                        self._cache_torn = False
                    f.writelines(_cache_line(p) for p in new)
                return len(new)
            except OSError as e:
                logger.warning("EMWIN cache: could not write %s: %s", path, e)
            except Exception:
                logger.exception("EMWIN cache: could not write %s", path)
            if live is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    logger.debug("EMWIN cache: could not remove %s", tmp)
            return None

    def _load_cache(self) -> None:
        """Products from the cache file, at start (in the executor). The old
        whole-file saves and the journal are the same format, one product
        per line; an expired line, a file name already loaded and a torn or
        garbled line are skipped, and the rest still load."""
        path = self.cache_file
        now = datetime.now(UTC)
        count = lines = bad = 0
        last = ""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    lines += 1
                    last = raw
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw)
                        ts = datetime.fromisoformat(rec["timestamp"])
                        fname = rec.get("filename") or ""
                    except (ValueError, KeyError, TypeError, AttributeError):
                        bad += 1
                        continue
                    rec["timestamp"] = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                    # This cache is only ever this source's own products, so a
                    # line written before revision 7 is still an internet one.
                    rec.setdefault("source", "internet")
                    if not fname or fname in self._products \
                            or is_expired((rec.get("awips_id") or "")[:3], rec["timestamp"], now):
                        continue
                    self._products[fname] = rec
                    self._note_created(fname)
                    count += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            # Not loaded, so never rewritten: what is in it may still be read next time.
            logger.warning("EMWIN cache: could not read %s: %s", path, e)
            return
        self._cache_lines = lines
        self._cache_torn = bool(last) and not last.endswith("\n")
        self._cache_loaded = True
        if bad:
            logger.warning("EMWIN cache: skipped %d unreadable line(s) in %s", bad, path)
        if count:
            logger.info("Restored %d products from cache", count)

    def _parse_emwin_file(self, filename: str, raw_text: str) -> dict | None:
        return parse_emwin_file(filename, raw_text, source="internet")

    async def fetch_products(self) -> list[dict]:
        return list(self._products.values())


# Product types worth a console line when they arrive from the dish.
_NOTABLE = {"TOR", "SVR", "SVS", "FFW", "FFS", "FLW", "FLS", "NPW", "WSW", "MWW", "SMW",
            "SPS", "CFW", "RFW", "EWW", "HLS", "TCV", "WOU", "WCN", "SEL", "ALT", "WAR", "WAT"}


class SDRSource(_NoaaBundles, EMWINSource):
    """EMWIN products from the goestools output directory on disk.

    goesproc's `emwin` handler writes every product as its own file under
    `<sdr_emwin_dir>/YYYY-MM-DD/`, named exactly like the internet bundle
    (`A_<WMO><stn><ddhhmm>_C_KWIN_<ts>_<seq>-N-<AWIPS>.TXT`), with the same
    `\\r\\r\\n` bodies. So this source is a directory watcher and nothing
    more: scan the date directories that can still hold products younger
    than `emwin_max_age_hours`, read each new file as bytes (text mode
    would turn `\\r\\r\\n` into blank lines and break pyIEM), and hand it to
    the same parser the zip path uses. No network, no cache file: the
    files on disk are the cache.

    **The internet backup.** When the dish has written nothing for
    `sdr_internet_fallback_min` minutes, the source also takes NOAA's
    internet bundle (the hour's bundle once, to cover the gap, then the
    2-minute one) until the dish writes again. NOAA names every product
    exactly as goesproc does, so a product either way is one file name and
    is held once, and each keeps the source it came by. The ones from the
    internet are written under `fallback_root` in the same day folders, so
    a restart in the middle of an outage still has them; that folder is
    scanned with the dish's and pruned the same way.

    The backup goes off again only once the dish has written
    `sdr_resume_min_files` of its own files in the last ten minutes: a
    marginal lock that trickles one file every few minutes keeps it on. Any
    fetch that fails is made good by taking the hour's bundle at the next
    poll (and every half hour regardless); the file-name dedup makes a bundle
    taken twice cost nothing.

    **How late the products are.** goesproc writes a file the moment it comes
    off the air, so the file's mtime against the creation time in its name is
    how far behind the uplink is running: on 2026-10-16 NOAA replayed a
    backlog for ten hours and every product arrived five to ten hours old,
    on the dish and on the internet alike, while every file was "17 s old".
    `feed_lag_min` is the median over the last 50 of the dish's files, of
    those written in the last hour.
    """

    _net_label = "EMWIN internet backup"

    def __init__(self, root: Path | None = None, fallback_root: Path | None = None):
        self.root = Path(root or settings.sdr_emwin_dir).expanduser()
        # The production source keeps its internet products next to the bot's
        # other data; a source pointed somewhere else (a test) has no backup
        # unless it is given a folder for one.
        if fallback_root is None and root is None:
            fallback_root = Path(settings.data_dir) / "emwin_internet"
        self.fallback_root = Path(fallback_root).expanduser() if fallback_root else None
        self._products: dict[str, dict] = {}
        self._seen: set[str] = set()
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._seen_initial = False
        self.newest_mtime: float | None = None    # newest file the DISH wrote, for `sat`
        self._dish_mtimes: deque[float] = deque(maxlen=5000)   # its writes in the last ten minutes
        self._lag_samples: deque[tuple[float, float]] = deque(maxlen=1000)   # (mtime, minutes behind), per file
        self.newest_product_ts: datetime | None = None         # the newest creation time the dish gave us
        self.fallback_since: datetime | None = None   # the internet backup is on (since when)
        self.fallback_added = 0                       # products it brought in this time
        self.fallback_last_ok: datetime | None = None     # last fetch that brought a product
        self.fallback_last_error: str | None = None       # "403 Forbidden at 14:03Z", until a fetch works
        self.fallback_last_error_at: datetime | None = None
        self._fallback_fetched = 0.0                  # monotonic time of its last fetch
        self._hour_fetched: float | None = None       # monotonic time the hour bundle last came in whole
        self._refetch_hour = False                    # a fetch failed: take the hour bundle next
        self._get_error: str | None = None            # what the last _get ran into, for the record
        self._get_status: int | None = None           # ... and the HTTP status NOAA answered with
        self._client: "httpx.AsyncClient | None" = None

    async def start(self) -> None:
        if not self.root.is_dir():
            # The cleanup used to remove an emwin/ root left empty by a long
            # outage, and the bot then ran with no directory to watch and no
            # internet backup. goesproc writes into a directory that already
            # exists just the same.
            logger.warning("SDR EMWIN directory %s does not exist (MCW_SDR_EMWIN_DIR); creating it "
                           "so the watcher and the internet backup can run", self.root)
            self.root.mkdir(parents=True, exist_ok=True)
        self._running = True
        added = await asyncio.get_running_loop().run_in_executor(None, self.scan)
        logger.info("SDR source: %d products on disk under %s", added, self.root)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(settings.sdr_poll_interval)
            try:
                new = await asyncio.get_running_loop().run_in_executor(None, self.scan)
                if new:
                    logger.info("SDR source: +%d new products (%d total)", new, len(self._products))
            except Exception:
                logger.exception("Error scanning SDR EMWIN directory")
            try:
                await self.check_fallback()
            except Exception:
                logger.exception("Error in the internet backup")
            try:
                await self.check_nws_metar()
            except Exception:
                logger.exception("Error in the NWS METAR feed")

    # -- the internet backup ------------------------------------------------

    @property
    def fallback_active(self) -> bool:
        return self.fallback_since is not None

    def dish_quiet_min(self, now: float | None = None) -> float:
        """Minutes since the dish last wrote a file this source took in."""
        if self.newest_mtime is None:
            return float("inf")
        return ((now or time.time()) - self.newest_mtime) / 60

    def dish_files_recent(self, now: float | None = None, window_s: int = 600) -> int:
        """How many of its own files the dish wrote in the last `window_s`."""
        since = (now or time.time()) - window_s
        return sum(1 for m in self._dish_mtimes if m >= since)

    # `feed_lag_min` (the median over the dish's last 50 files written in the
    # last hour) and the fetching itself are _NoaaBundles'.

    async def check_fallback(self, now: datetime | None = None, wall: float | None = None) -> int:
        """Turn the internet backup on or off by how long the dish has been
        quiet, and fetch when it is on.  Returns the products it added."""
        limit = settings.sdr_internet_fallback_min
        if self.fallback_root is None or limit <= 0:
            return 0
        now = now or datetime.now(timezone.utc)
        quiet = self.dish_quiet_min(wall)
        mono = time.monotonic()
        if self.fallback_active:
            # Off only when the dish is really back: writing, and steadily.
            if quiet < limit and self.dish_files_recent(wall) >= settings.sdr_resume_min_files:
                logger.info("EMWIN: the dish is writing again; internet backup off after %s, "
                            "%d products came that way", _span(now - self.fallback_since), self.fallback_added)
                self.fallback_since = None
                return 0
            if mono - self._fallback_fetched < settings.emwin_poll_interval:
                return 0
            url = self._bundle_url(mono)
        elif quiet >= limit:
            self.fallback_since = now
            self.fallback_added = 0
            self._fallback_fetched = 0.0
            self._hour_fetched = None
            self._refetch_hour = False
            logger.warning("EMWIN: nothing from the dish for %s; taking NOAA's internet feed until it writes again",
                           "ever" if quiet == float("inf") else f"{quiet:.0f} min")
            url = settings.emwin_base_url          # the last hour, to cover the gap
        else:
            return 0
        added = await self._fetch_internet(url, now)
        self._fallback_fetched = mono
        self._bundle_done(url, added is not None, mono)   # a failure: the hour bundle next time
        if added is None:
            return 0
        self.fallback_added += added
        if added:
            logger.info("EMWIN (internet backup): +%d new products (%d total)", added, len(self._products))
        return added

    async def check_nws_metar(self, now: datetime | None = None) -> int:
        """NWS's METAR feed into the store when it is on: how many products
        were new. Held in memory only; after a restart the first poll takes
        the last 75 minutes again."""
        now = now or datetime.now(timezone.utc)
        added = 0
        for prod in await self._poll_nws_metar(now):
            if prod["filename"] not in self._products:
                self._products[prod["filename"]] = prod
                added += 1
        return added

    # The fetch runs inside the scan loop: _NoaaBundles' client gives up
    # after 10 s, where a WAN that hung used to hold the dish watcher for up
    # to 60 s a cycle.

    async def _fetch_internet(self, url: str, now: datetime) -> int | None:
        """The bundle at `url` into the store: how many products were new, or
        None when the fetch failed (no answer, not a ZIP, nothing in it)."""
        products = await self._read_internet(url, now)
        if products is None:
            return None
        added = await asyncio.get_running_loop().run_in_executor(None, self._take_internet, products, now)
        self.fallback_last_error = self.fallback_last_error_at = None
        if added:
            self.fallback_last_ok = now
        return added

    def _fetch_failed(self, what: str, now: datetime) -> None:
        self.fallback_last_error = f"{what} at {now:%H:%MZ}"
        self.fallback_last_error_at = now

    def _take_internet(self, products: list[dict], now: datetime) -> int:
        """Hold the bundle's products the dish has not given us, and write
        each one down so that a restart keeps it."""
        added = 0
        notable: list[str] = []
        for prod in products:
            name = prod.get("filename") or ""
            if not name or name in self._seen or name in self._products:
                continue
            if is_expired((prod.get("awips_id") or "")[:3], prod["timestamp"], now):
                self._seen.add(name)
                continue
            folder = self.fallback_root / f"{prod['timestamp']:%Y-%m-%d}"
            try:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(prod["raw_text"].encode("utf-8"))
            except OSError as e:
                logger.warning("EMWIN internet backup: could not keep %s on disk: %s", name, e)
            self._seen.add(name)
            self._products[name] = prod
            added += 1
            if (prod.get("awips_id") or "")[:3] in _NOTABLE:
                notable.append(prod["awips_id"])
        if notable:
            logger.info("EMWIN (internet): %s%s", " ".join(notable[:10]),
                        f" +{len(notable) - 10} more" if len(notable) > 10 else "")
        self._prune_fallback(now)
        return added

    def _prune_fallback(self, now: datetime) -> None:
        """Day folders of internet products no product can still come from."""
        if self.fallback_root is None or not self.fallback_root.is_dir():
            return
        keep = set(d.name for d in self._candidate_dirs(now, self.fallback_root))
        for d in self.fallback_root.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)

    # -- the directories -------------------------------------------------------

    def _candidate_dirs(self, now: datetime, root: "Path | None" = None) -> list[Path]:
        """Date directories that can still contain unexpired products."""
        root = root or self.root
        cutoff_day = (now - timedelta(hours=longest_hours() + 24)).date()
        dirs = []
        if not root.is_dir():
            return dirs
        for d in root.iterdir():
            if not d.is_dir():
                continue
            try:
                day = datetime.strptime(d.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= cutoff_day:
                dirs.append(d)
        return sorted(dirs)

    def scan(self, now: datetime | None = None) -> int:
        """Pick up new .TXT files, drop expired ones. Returns the number added."""
        now = now or datetime.now(timezone.utc)
        oldest = now - timedelta(hours=longest_hours())
        wall = now.timestamp()
        settle = wall - 2                     # skip files goesproc may still be writing
        added = 0
        notable: list[str] = []
        folders = [(d, "sdr") for d in self._candidate_dirs(now)]
        if self.fallback_root is not None:
            folders += [(d, "internet") for d in self._candidate_dirs(now, self.fallback_root)]
        for d, source in folders:
            with os.scandir(d) as it:
                for entry in it:
                    name = entry.name
                    if name in self._seen or not name.upper().endswith(".TXT"):
                        continue
                    m_ts = EMWIN_TS_RE.search(name)
                    if m_ts:
                        try:
                            ts = datetime.strptime(m_ts.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                        except ValueError:
                            ts = None
                        ptype = name.rsplit("-", 1)[-1][:3].upper()
                        if ts is not None and (ts < oldest or ts < now - timedelta(hours=max_age_hours(ptype))):
                            self._seen.add(name)      # too old, never look again
                            continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    if st.st_mtime > settle:
                        continue
                    try:
                        raw = Path(entry.path).read_bytes().decode("utf-8", errors="replace").strip()
                    except OSError:
                        continue
                    self._seen.add(name)
                    if not raw:
                        continue
                    prod = parse_emwin_file(name, raw, source=source)
                    if prod:
                        self._products[name] = prod
                        added += 1
                        if source == "sdr" and "_C_KWIN_" in name.upper():
                            self.newest_mtime = max(self.newest_mtime or 0.0, st.st_mtime)
                            if st.st_mtime >= wall - 600:
                                self._dish_mtimes.append(st.st_mtime)
                            self._note_lag(name, st.st_mtime, wall)
                        if (prod.get("awips_id") or "")[:3] in _NOTABLE:
                            notable.append(prod["awips_id"])
        if notable and self._seen_initial:
            logger.info("EMWIN: %s%s", " ".join(notable[:10]), f" +{len(notable) - 10} more" if len(notable) > 10 else "")
        self._seen_initial = True      # the first scan is the backlog, not news
        # Expire
        before = len(self._products)
        self._products = {k: v for k, v in self._products.items()
                          if not is_expired((v.get("awips_id") or "")[:3], v["timestamp"], now)}
        if before != len(self._products):
            logger.debug("SDR source: expired %d products", before - len(self._products))
        return added

    # `_note_lag` (one of the dish's files: how long after its creation it
    # was written) is _NoaaBundles'.

    async def fetch_products(self) -> list[dict]:
        return list(self._products.values())


def _span(delta: timedelta) -> str:
    m = int(delta.total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def create_source() -> EMWINSource:
    if settings.emwin_source == "sdr":
        return SDRSource()
    return InternetSource()
