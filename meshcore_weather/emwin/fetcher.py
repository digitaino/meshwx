"""EMWIN product sources: the NOAA internet bundle, or the goestools
output directory fed by a GOES satellite receiver (SDRSource).

Internet strategy:
1. On startup, download the 3-hour bundle for initial coverage
2. Every 2 minutes, download the 2-minute bundle (~43KB) for new products
3. Accumulate products over time, expire after 12 hours
4. This ensures zone forecasts (issued every 6-12h) stay available

The EMWIN ZIP bundles use the same file format as the GOES satellite
downlink, so switching to SDR later requires no parser changes.
"""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from meshcore_weather.config import settings
from meshcore_weather.emwin.retention import is_expired, longest_hours, max_age_hours

logger = logging.getLogger(__name__)

EMWIN_FILENAME_RE = re.compile(
    r"A_(\w{4,6})(\w{4})(\d{6,8})_C_KWIN_\d+_\d+-\d+-(\w+)\.TXT",
    re.IGNORECASE,
)

EMWIN_TS_RE = re.compile(r"_(\d{14})_")


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


class InternetSource(EMWINSource):
    """Fetch EMWIN products, accumulate over time, expire old ones."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._products: dict[str, dict] = {}  # keyed by filename for dedup
        self._poll_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)
        self._running = True

        # Restore cached products from disk. In a thread: it is thousands of
        # JSON lines, and the radio is already listening by now (main.py
        # loads the backlog behind the radio).
        await asyncio.get_running_loop().run_in_executor(None, self._load_cache)

        # Initial load: 3-hour bundle for broad coverage
        logger.info("Initial load from 3-hour bundle...")
        await self._fetch_bundle(settings.emwin_base_url)

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
            return          # a start cut short: saving now would empty the cache file
        self._save_cache()
        await self._client.aclose()
        logger.info("EMWIN source stopped (%d products in store)", len(self._products))

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(settings.emwin_poll_interval)
            try:
                # Poll the small 2-minute bundle for new products
                new = await self._fetch_bundle(settings.emwin_poll_url)
                self._expire_old()
                if new:
                    logger.info(
                        "Poll: +%d new products (%d total)",
                        new, len(self._products),
                    )
                    self._save_cache()
            except Exception:
                logger.exception("Error polling EMWIN data")

    async def _fetch_bundle(self, url: str) -> int:
        """Download a ZIP bundle and add products to the store. Returns count of new products."""
        logger.info("Fetching %s", url)
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("HTTP error: %s", e)
            return 0

        # Unzipping and splitting a bundle is CPU work; off the event loop it
        # cannot stall the radio's serial reads.
        extracted = await asyncio.get_running_loop().run_in_executor(None, self._extract_zip, resp.content)
        new_count = 0
        for prod in extracted:
            fname = prod.get("filename", "")
            if fname and fname not in self._products:
                self._products[fname] = prod
                new_count += 1
        return new_count

    def _expire_old(self) -> None:
        """Remove products past their retention (warnings keep longer)."""
        now = datetime.now(timezone.utc)
        before = len(self._products)
        self._products = {
            k: v for k, v in self._products.items()
            if not is_expired((v.get("awips_id") or "")[:3], v.get("timestamp") or now, now)
        }
        removed = before - len(self._products)
        if removed:
            logger.debug("Expired %d old products", removed)

    def _load_cache(self) -> None:
        """Load products from disk cache on startup."""
        if not CACHE_FILE.exists():
            return
        now = datetime.now(timezone.utc)
        count = 0
        try:
            with open(CACHE_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    rec["timestamp"] = datetime.fromisoformat(rec["timestamp"])
                    # This cache is only ever this source's own products, so a
                    # line written before revision 7 is still an internet one.
                    rec.setdefault("source", "internet")
                    if is_expired((rec.get("awips_id") or "")[:3], rec["timestamp"], now):
                        continue
                    fname = rec.get("filename", "")
                    if fname and fname not in self._products:
                        self._products[fname] = rec
                        count += 1
        except Exception:
            logger.exception("Error loading cache")
        if count:
            logger.info("Restored %d products from cache", count)

    def _save_cache(self) -> None:
        """Persist current products to disk."""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                for prod in self._products.values():
                    rec = dict(prod)
                    rec["timestamp"] = rec["timestamp"].isoformat()
                    f.write(json.dumps(rec) + "\n")
            logger.info("Saved %d products to cache", len(self._products))
        except Exception:
            logger.exception("Error saving cache")

    def _extract_zip(self, data: bytes) -> list[dict]:
        return read_bundle(data, source="internet")

    def _parse_emwin_file(self, filename: str, raw_text: str) -> dict | None:
        return parse_emwin_file(filename, raw_text, source="internet")

    async def fetch_products(self) -> list[dict]:
        return list(self._products.values())


# Product types worth a console line when they arrive from the dish.
_NOTABLE = {"TOR", "SVR", "SVS", "FFW", "FFS", "FLW", "FLS", "NPW", "WSW", "MWW", "SMW",
            "SPS", "CFW", "RFW", "EWW", "HLS", "TCV", "WOU", "WCN", "SEL", "ALT", "WAR", "WAT"}


class SDRSource(EMWINSource):
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
    """

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
        self.fallback_since: datetime | None = None   # the internet backup is on (since when)
        self.fallback_added = 0                       # products it brought in this time
        self._fallback_fetched = 0.0                  # monotonic time of its last fetch
        self._client: "httpx.AsyncClient | None" = None

    async def start(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"SDR EMWIN directory {self.root} does not exist "
                "(MCW_SDR_EMWIN_DIR; goesproc must be writing there)")
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

    # -- the internet backup ------------------------------------------------

    @property
    def fallback_active(self) -> bool:
        return self.fallback_since is not None

    def dish_quiet_min(self, now: float | None = None) -> float:
        """Minutes since the dish last wrote a file this source took in."""
        if self.newest_mtime is None:
            return float("inf")
        return ((now or time.time()) - self.newest_mtime) / 60

    async def check_fallback(self, now: datetime | None = None, wall: float | None = None) -> int:
        """Turn the internet backup on or off by how long the dish has been
        quiet, and fetch when it is on.  Returns the products it added."""
        limit = settings.sdr_internet_fallback_min
        if self.fallback_root is None or limit <= 0:
            return 0
        now = now or datetime.now(timezone.utc)
        quiet = self.dish_quiet_min(wall)
        if quiet < limit:
            if self.fallback_active:
                logger.info("EMWIN: the dish is writing again; internet backup off after %s, "
                            "%d products came that way", _span(now - self.fallback_since), self.fallback_added)
                self.fallback_since = None
            return 0
        if not self.fallback_active:
            self.fallback_since = now
            self.fallback_added = 0
            self._fallback_fetched = 0.0
            logger.warning("EMWIN: nothing from the dish for %s; taking NOAA's internet feed until it writes again",
                           "ever" if quiet == float("inf") else f"{quiet:.0f} min")
            url = settings.emwin_base_url          # the last hour, to cover the gap
        elif time.monotonic() - self._fallback_fetched >= settings.emwin_poll_interval:
            url = settings.emwin_poll_url
        else:
            return 0
        added = await self._fetch_internet(url, now)
        self._fallback_fetched = time.monotonic()
        self.fallback_added += added
        if added:
            logger.info("EMWIN (internet backup): +%d new products (%d total)", added, len(self._products))
        return added

    async def _get(self, url: str) -> "bytes | None":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as e:
            logger.warning("EMWIN internet backup: %s", e)
            return None

    async def _fetch_internet(self, url: str, now: datetime) -> int:
        data = await self._get(url)
        if not data:
            return 0
        loop = asyncio.get_running_loop()
        products = await loop.run_in_executor(None, read_bundle, data, "internet")
        return await loop.run_in_executor(None, self._take_internet, products, now)

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
        settle = now.timestamp() - 2          # skip files goesproc may still be writing
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
                        if source == "sdr":
                            self.newest_mtime = max(self.newest_mtime or 0.0, st.st_mtime)
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

    async def fetch_products(self) -> list[dict]:
        return list(self._products.values())


def _span(delta: timedelta) -> str:
    m = int(delta.total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def create_source() -> EMWINSource:
    if settings.emwin_source == "sdr":
        return SDRSource()
    return InternetSource()
