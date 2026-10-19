"""Airport reports and forecasts from NWS's own text feeds, for the internet fill.

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
they came from.

The airport forecasts are the same kind of feed (`nws_taf_url`, DS.tafst):
bulletins of `TAF PABI 191400Z ...`, kept as they are under a `TAFALLUS`
name, which is what the TAF replies look in.

The internet fill (emwin/fill.py) decides which files to take: only those the
feed has not covered in time.
"""

import logging
import re
import zlib
from datetime import UTC, datetime, timedelta

import httpx

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


def metar_file(text: str, written: datetime, sequence: int) -> dict | None:
    """One METAR feed file as a product, or None when it holds no report."""
    reports = reports_in(text)
    return collective(reports, written, sequence) if reports else None


def taf_file(text: str, written: datetime, sequence: int) -> dict | None:
    """One TAF feed file as a product: its bulletins as they are, under a
    TAFALLUS name, or None when it holds no TAF."""
    # A bulletin's `####...####` line becomes a blank one: where a TAF ends.
    lines = ["" if _SEPARATOR_RE.match(ln.strip()) else ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    if not any(ln.lstrip().startswith("TAF") for ln in lines):
        return None
    ddhhmm = f"{written:%d%H%M}"
    body = "\r\r\n".join(["FTUS99 NWSF " + ddhhmm, "TAFALLUS", *lines]).strip()
    return {
        "product_id": "FTUS99",
        "station": "NWSF",
        "awips_id": "TAFALLUS",
        "timestamp": written,
        "raw_text": body,
        "filename": f"A_FTUS99NWSF{ddhhmm}_C_NWSF_{written:%Y%m%d%H%M%S}_{sequence}-2-TAFALLUS.TXT",
        "source": "internet",
    }


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


class NwsFeed:
    """Polls one feed directory and hands back each new file it is asked for
    as a product (`make`: metar_file or taf_file)."""

    def __init__(self, url: str, make, kind: str = "SAH"):
        self.url = url.rstrip("/") + "/"
        self.make = make
        self.kind = kind
        self._taken: set[tuple[str, datetime]] = set()     # (file, written at) already turned into a product
        self.newest_file: datetime | None = None            # for the log and the portal
        self.last_error: str | None = None

    async def poll(self, client: httpx.AsyncClient, now: datetime | None = None, keep=None) -> list[dict]:
        """The files of the last FIRST_WINDOW not taken yet that `keep(written)`
        accepts (all, without one), oldest first, as product dicts. A failure
        costs that poll only: the next one takes whatever it missed, by the
        listing. A file `keep` turns down is asked about again next time."""
        now = now or datetime.now(UTC)
        try:
            resp = await client.get(self.url)
            resp.raise_for_status()
            listing = parse_listing(resp.text)
        except httpx.HTTPError as e:
            self.last_error = f"{type(e).__name__} at {now:%H:%MZ}"
            logger.warning("NWS %s feed: listing failed (%s)", self.kind, e)
            return []
        since = now - FIRST_WINDOW
        todo = sorted((when, name) for name, when in listing
                      if when >= since and (name, when) not in self._taken and (keep is None or keep(when)))
        products = []
        for when, name in todo[-MAX_FILES_PER_POLL:]:
            try:
                resp = await client.get(self.url + name)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                self.last_error = f"{type(e).__name__} at {now:%H:%MZ}"
                logger.warning("NWS %s feed: %s failed (%s)", self.kind, name, e)
                continue
            self._taken.add((name, when))
            sequence = int(name[3:7]) * 100000 + zlib.crc32(resp.content) % 100000
            prod = self.make(resp.content.decode("latin-1"), when, sequence)
            if prod is None:
                continue
            products.append(prod)
            if self.newest_file is None or when > self.newest_file:
                self.newest_file = when
        # The listing rolls over daily: forget what can no longer come back.
        self._taken = {k for k in self._taken if k[1] >= now - timedelta(hours=26)}
        if products:
            self.last_error = None
        return products
