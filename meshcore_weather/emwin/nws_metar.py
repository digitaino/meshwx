"""Current airport reports from NWS's own text feed, for when EMWIN runs late.

EMWIN (the dish, and NOAA's internet bundles of the same broadcast) can fall
hours behind its own uplink queue. On 2026-10-17/18 everything it carried
arrived 2 to 3.5 h after KWIN filed it, while the same METARs were on NWS's
ordinary text feed on tgftp within minutes: KATT 181651Z was in DS.metar's
sn.0190.txt, written 16:55Z, and the dish still had nothing newer than
181051Z at 17:11Z.

That feed (`nws_metar_url`) is a directory of rolling files, one about every
five minutes, each a run of single-station bulletins:

    ####018000072####
    SAUS44 KEWX 181651
    MTRATT
    METAR KATT 181651Z AUTO VRB03KT 10SM CLR 19/05 A3039 RMK AO2 SLP287
    T01940050 $

Each file is repacked as one hourly collective in the layout the store already
reads (`WeatherStore._find_metar_raw`): one report per line ending in "=",
under a `SAHOURLY` name. The store takes a station's report from the newest
product it is in, so a fresher NWS report wins over the dish's and the dish's
wins again once EMWIN catches up. The products say "internet", which is where
they came from. Off unless `nws_metar_enabled`.
"""

import logging
import re
import zlib
from datetime import UTC, datetime, timedelta

import httpx

from meshcore_weather.config import settings

logger = logging.getLogger(__name__)

# `<td><a href="sn.0190.txt">sn.0190.txt</a></td><td align="right">18-Oct-2026 16:55  </td>`;
# the listing's times are UTC.
_LISTING_RE = re.compile(r'href="(sn\.\d{4}\.txt)".*?(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})')
_SEPARATOR_RE = re.compile(r"^#{4}\d+#{4}$")
_REPORT_RE = re.compile(r"^(?:(?:METAR|SPECI)\s+)?([A-Z0-9]{4}\s+(?:COR\s+)?\d{6}Z.*)$")

# A first poll takes this much of the feed: every station reports at least
# hourly, so the last 75 minutes hold each one's latest.
FIRST_WINDOW = timedelta(minutes=75)
# At most this many files a poll, so a long outage does not turn into one
# burst of downloads: the 00Z file alone is ~570 KB.
MAX_FILES_PER_POLL = 20


def parse_listing(html: str) -> list[tuple[str, datetime]]:
    """(file name, written at) for every feed file in the directory page."""
    out = []
    for name, when in _LISTING_RE.findall(html):
        try:
            out.append((name, datetime.strptime(when, "%d-%b-%Y %H:%M").replace(tzinfo=UTC)))
        except ValueError:
            continue
    return out


def reports_in(text: str) -> list[str]:
    """Every METAR/SPECI report in one feed file, each on one line ending "=".

    Bulletins are split at the `####...####` lines; a report starts at a line
    that opens with a station and a DDHHMMZ group (after an optional METAR or
    SPECI) and runs until "=" or the next report. Wrapped lines are joined."""
    reports: list[str] = []
    current: str | None = None

    def close() -> None:
        nonlocal current
        if current:
            reports.append(current.rstrip("= ") + "=")
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SEPARATOR_RE.match(line):
            close()
            continue
        m = _REPORT_RE.match(line)
        if m:
            close()
            current = m.group(1)
        elif current is not None:
            current += " " + line
        if current is not None and current.endswith("="):
            close()
    close()
    return reports


def collective(reports: list[str], written: datetime, sequence: int) -> dict:
    """One feed file's reports as a product dict, named like an EMWIN file
    (`A_<WMO><stn><ddhhmm>_C_<center>_<ts>_<seq>-N-<AWIPS>.TXT`) so the store
    reads its type (SAH) and time from the name as it does the dish's."""
    ddhhmm = f"{written:%d%H%M}"
    body = "\n".join(["SAUS99 NWSF " + ddhhmm, "SAHOURLY", "METAR", *reports])
    return {
        "product_id": "SAUS99",
        "station": "NWSF",
        "awips_id": "SAHOURLY",
        "timestamp": written,
        "raw_text": body,
        "filename": f"A_SAUS99NWSF{ddhhmm}_C_NWSF_{written:%Y%m%d%H%M%S}_{sequence}-2-SAHOURLY.TXT",
        "source": "internet",
    }


class NwsMetarFeed:
    """Polls the feed directory and hands back each new file as a product."""

    def __init__(self, url: str | None = None):
        self.url = (url or settings.nws_metar_url).rstrip("/") + "/"
        self._taken: set[tuple[str, datetime]] = set()     # (file, written at) already turned into a product
        self._last_poll: datetime | None = None
        self.newest_file: datetime | None = None            # for the log and the portal
        self.last_error: str | None = None

    def due(self, now: datetime) -> bool:
        return self._last_poll is None or now - self._last_poll >= timedelta(seconds=settings.nws_metar_poll_interval)

    async def poll(self, client: httpx.AsyncClient, now: datetime | None = None) -> list[dict]:
        """The files written since the last poll (the last FIRST_WINDOW on the
        first), oldest first, as product dicts. A failure costs that poll only:
        the next one takes whatever it missed, by the listing."""
        now = now or datetime.now(UTC)
        self._last_poll = now
        try:
            resp = await client.get(self.url)
            resp.raise_for_status()
            listing = parse_listing(resp.text)
        except httpx.HTTPError as e:
            self.last_error = f"{type(e).__name__} at {now:%H:%MZ}"
            logger.warning("NWS METAR feed: listing failed (%s)", e)
            return []
        since = now - FIRST_WINDOW
        todo = sorted((when, name) for name, when in listing
                      if when >= since and (name, when) not in self._taken)[-MAX_FILES_PER_POLL:]
        products = []
        for when, name in todo:
            try:
                resp = await client.get(self.url + name)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                self.last_error = f"{type(e).__name__} at {now:%H:%MZ}"
                logger.warning("NWS METAR feed: %s failed (%s)", name, e)
                continue
            self._taken.add((name, when))
            reports = reports_in(resp.content.decode("latin-1"))
            if not reports:
                continue
            sequence = int(name[3:7]) * 100000 + zlib.crc32(resp.content) % 100000
            products.append(collective(reports, when, sequence))
            if self.newest_file is None or when > self.newest_file:
                self.newest_file = when
        # The listing rolls over daily: forget what can no longer come back.
        self._taken = {k for k in self._taken if k[1] >= now - timedelta(hours=26)}
        if products:
            self.last_error = None
            logger.info("NWS METAR feed: +%d files, %d reports, newest written %s",
                        len(products), sum(p["raw_text"].count("=") for p in products),
                        f"{self.newest_file:%H:%MZ}")
        return products
