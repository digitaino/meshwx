"""Space weather from SWPC products received over EMWIN.

Two daily products carry everything a mesh user needs:

  DAYTDF  "3-Day Forecast"          max Kp last 24 h, Kp per 3-h block for
                                    the next 3 days, S-scale and R-scale
                                    probabilities
  DAYIND  "Daily Space Weather      sunspot number, 10.7 cm solar flux (SFI),
           Indices"                 90-day flux, GOES X-ray background

The result is one `SpaceWeather` object: 25 bytes on the wire, one line
of text for the DM path. The narrative discussion, the weekly outlook and
the event log are not carried; they are too long for LoRa and their
numbers are already here.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

from meshcore_weather.parser.weather import EMWINProduct, WeatherStore

MSG_SPACE_WEATHER = 0x3E

_XRAY_CLASSES = "ABCMX"


def g_scale_for_kp(kp: float) -> int:
    """NOAA G scale from a Kp value (NOAA rounds: 4.67 -> 5 -> G1)."""
    k = int(round(kp))
    return max(0, min(5, k - 4)) if k >= 5 else 0


@dataclass
class SpaceWeather:
    issued_at: datetime                   # of the 3-Day Forecast
    kp_max_24h: float
    kp_forecast: list[float]              # max Kp per day, 3 days
    g_forecast: list[int]                 # G0-G5 per day
    s1_prob: list[int]                    # % per day
    r12_prob: list[int]                   # % per day
    r3_prob: list[int]                    # % per day
    sfi: int | None = None                # 10.7 cm flux, sfu
    sfi_90d: int | None = None
    ssn: int | None = None
    xray_bkgd: str | None = None          # e.g. "B3.0"
    indices_date: str | None = None
    source_ids: list[str] = field(default_factory=list)

    # -- wire ---------------------------------------------------------------
    #
    #  0      0x3E
    #  1-4    issued, uint32 Unix minute (3-Day Forecast issue time)
    #  5      kp_max_24h * 3           (u8, 0..27)
    #  6-8    kp forecast day 1..3 * 3 (u8 each)
    #  9      G day1 (hi nibble) | G day2 (lo nibble)
    #  10     G day3 (hi nibble) | reserved
    #  11-13  S1+ probability % day 1..3
    #  14-16  R1-R2 probability % day 1..3
    #  17-19  R3+ probability % day 1..3
    #  20-21  SFI, uint16 sfu (0 = unknown)
    #  22-23  sunspot number, uint16 (0xFFFF = unknown)
    #  24     X-ray background: class index (A=0..X=4) * 10 + round(magnitude);
    #         255 = unknown
    SIZE = 25

    def to_bytes(self) -> bytes:
        def kp3(v: float) -> int:
            return max(0, min(27, int(round(v * 3))))
        g = (self.g_forecast + [0, 0, 0])[:3]
        s1 = (self.s1_prob + [0, 0, 0])[:3]
        r12 = (self.r12_prob + [0, 0, 0])[:3]
        r3 = (self.r3_prob + [0, 0, 0])[:3]
        kpf = (self.kp_forecast + [0.0, 0.0, 0.0])[:3]
        xray = 255
        if self.xray_bkgd and self.xray_bkgd[0] in _XRAY_CLASSES:
            try:
                xray = _XRAY_CLASSES.index(self.xray_bkgd[0]) * 10 + min(9, int(round(float(self.xray_bkgd[1:]))))
            except ValueError:
                xray = 255
        return struct.pack(
            ">BIBBBBBBBBBBBBBBBHHB",
            MSG_SPACE_WEATHER,
            int(self.issued_at.timestamp() // 60) & 0xFFFFFFFF,
            kp3(self.kp_max_24h), kp3(kpf[0]), kp3(kpf[1]), kp3(kpf[2]),
            ((g[0] & 0xF) << 4) | (g[1] & 0xF), (g[2] & 0xF) << 4,
            *[min(100, max(0, int(v))) for v in s1],
            *[min(100, max(0, int(v))) for v in r12],
            *[min(100, max(0, int(v))) for v in r3],
            (self.sfi or 0) & 0xFFFF,
            0xFFFF if self.ssn is None else (self.ssn & 0xFFFF),
            xray,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "SpaceWeather":
        if len(data) < cls.SIZE or data[0] != MSG_SPACE_WEATHER:
            raise ValueError("Invalid space weather message")
        (_, issued, kp24, k1, k2, k3, g12, g3, s1a, s1b, s1c,
         ra, rb, rc, r3a, r3b, r3c, sfi, ssn, xray) = struct.unpack_from(">BIBBBBBBBBBBBBBBBHHB", data)
        xray_s = None
        if xray != 255:
            xray_s = f"{_XRAY_CLASSES[min(4, xray // 10)]}{xray % 10}.0"
        return cls(
            issued_at=datetime.fromtimestamp(issued * 60, tz=timezone.utc),
            kp_max_24h=kp24 / 3,
            kp_forecast=[k1 / 3, k2 / 3, k3 / 3],
            g_forecast=[g12 >> 4, g12 & 0xF, g3 >> 4],
            s1_prob=[s1a, s1b, s1c],
            r12_prob=[ra, rb, rc],
            r3_prob=[r3a, r3b, r3c],
            sfi=sfi or None,
            ssn=None if ssn == 0xFFFF else ssn,
            xray_bkgd=xray_s,
        )


# -- Parsers ----------------------------------------------------------------------


_ISSUED_RE = re.compile(r":Issued:\s*(\d{4})\s+(\w{3})\s+(\d{1,2})\s+(\d{4})\s*UTC?", re.I)
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _issued(text: str) -> datetime | None:
    m = _ISSUED_RE.search(text)
    if not m:
        return None
    y, mon, d, hhmm = int(m.group(1)), _MONTHS.get(m.group(2).title()), int(m.group(3)), m.group(4)
    if not mon:
        return None
    return datetime(y, mon, d, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def _pct_row(text: str, label: str) -> list[int]:
    """'R1-R2           10%           10%           10%' -> [10, 10, 10]."""
    m = re.search(rf"^{re.escape(label)}\s+(.*)$", text, re.M)
    if not m:
        return []
    return [int(v) for v in re.findall(r"(\d+)%", m.group(1))][:3]


def parse_3day_forecast(text: str) -> dict:
    """Kp and scale numbers from a DAYTDF product."""
    t = text.replace("\r", "")
    out: dict = {"issued_at": _issued(t)}
    m = re.search(r"greatest observed 3 hr Kp over the past 24 hours was\s+([\d.]+)", t)
    out["kp_max_24h"] = float(m.group(1)) if m else 0.0
    # Kp table: rows "00-03UT  3.33  3.33  3.67" (with optional "(G1)" tags)
    per_day: list[list[float]] = [[], [], []]
    for row in re.findall(r"^\d{2}-\d{2}UT\s+(.*)$", t, re.M):
        vals = re.findall(r"(\d+\.\d+)", row)
        for i, v in enumerate(vals[:3]):
            per_day[i].append(float(v))
    out["kp_forecast"] = [max(d) if d else 0.0 for d in per_day]
    out["g_forecast"] = [g_scale_for_kp(k) for k in out["kp_forecast"]]
    out["s1_prob"] = _pct_row(t, "S1 or greater")
    out["r12_prob"] = _pct_row(t, "R1-R2")
    out["r3_prob"] = _pct_row(t, "R3 or greater")
    return out


def parse_daily_indices(text: str) -> dict:
    """SSN, SFI, 90-day flux and X-ray background from a DAYIND product."""
    t = text.replace("\r", "")
    out: dict = {"indices_date": None, "ssn": None, "sfi": None, "sfi_90d": None, "xray_bkgd": None}
    m = re.search(r":Solar_Indices:\s*(\d{4}\s+\w{3}\s+\d{1,2})", t)
    if m:
        out["indices_date"] = m.group(1)
        # The data row is the first non-comment line after the header block.
        rest = t[m.end():]
        for line in rest.splitlines()[1:]:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) >= 4:
                try:
                    out["ssn"] = int(parts[0])
                    out["sfi"] = int(parts[1])
                    out["sfi_90d"] = int(parts[2])
                    flux = float(parts[3])
                    if flux > 0:
                        # W/m^2 -> class: A <1e-7, B <1e-6, C <1e-5, M <1e-4, X
                        for cls, lo in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)):
                            if flux >= lo:
                                out["xray_bkgd"] = f"{cls}{flux / lo:.1f}"
                                break
                except ValueError:
                    pass
            break
    return out


# -- Service ------------------------------------------------------------------------


def _newest(store: WeatherStore, emwin_id: str) -> EMWINProduct | None:
    best = None
    for p in store._products.values():
        if p.emwin_id == emwin_id and (best is None or p.timestamp > best.timestamp):
            best = p
    return best


def space_weather_for(store: WeatherStore) -> SpaceWeather | None:
    """Latest 3-Day Forecast merged with the latest Daily Indices."""
    tdf = _newest(store, "DAYTDFUS")
    ind = _newest(store, "DAYINDUS")
    if tdf is None and ind is None:
        return None
    # Either product alone is still worth answering with: the forecast
    # without indices, or (a stale feed) the indices without a Kp forecast.
    f = parse_3day_forecast(tdf.raw_text) if tdf else {
        "issued_at": None, "kp_max_24h": 0.0, "kp_forecast": [], "g_forecast": [],
        "s1_prob": [], "r12_prob": [], "r3_prob": [],
    }
    i = parse_daily_indices(ind.raw_text) if ind else {}
    return SpaceWeather(
        issued_at=f["issued_at"] or (tdf.timestamp if tdf else ind.timestamp),
        kp_max_24h=f["kp_max_24h"],
        kp_forecast=f["kp_forecast"],
        g_forecast=f["g_forecast"],
        s1_prob=f["s1_prob"],
        r12_prob=f["r12_prob"],
        r3_prob=f["r3_prob"],
        sfi=i.get("sfi"),
        sfi_90d=i.get("sfi_90d"),
        ssn=i.get("ssn"),
        xray_bkgd=i.get("xray_bkgd"),
        indices_date=i.get("indices_date"),
        source_ids=[x.filename for x in (tdf, ind) if x],
    )


def render(sw: SpaceWeather | None) -> str:
    """One-line DM: 'Kp now 3.0, next 3d 3.7/3.7/4.7 (G1 Tue). SFI 114 SSN 77 xray B3.0. R1-2 10%'."""
    if sw is None:
        return "No space weather products on file"
    from meshcore_weather.core.render_text import _cap, _local
    from datetime import timedelta
    days = [(_local(sw.issued_at) + timedelta(days=n + 1)).strftime("%a") for n in range(3)]
    bits = []
    if sw.kp_forecast:
        kps = "/".join(f"{k:.1f}" for k in sw.kp_forecast)
        gmax = max(sw.g_forecast) if sw.g_forecast else 0
        gtxt = ""
        if gmax:
            when = days[sw.g_forecast.index(gmax)]
            gtxt = f" (G{gmax} {when})"
        bits.append(f"Kp now {sw.kp_max_24h:.1f}, next 3d {kps}{gtxt}")
    else:
        bits.append("Kp forecast not received yet")
    solar = []
    if sw.sfi:
        solar.append(f"SFI {sw.sfi}")
    if sw.ssn is not None:
        solar.append(f"SSN {sw.ssn}")
    if sw.xray_bkgd:
        solar.append(f"xray {sw.xray_bkgd}")
    if solar:
        bits.append(" ".join(solar))
    r12 = max(sw.r12_prob) if sw.r12_prob else 0
    s1 = max(sw.s1_prob) if sw.s1_prob else 0
    risk = []
    if r12 >= 10:
        risk.append(f"R1-2 {r12}%")
    if s1 >= 5:
        risk.append(f"S1 {s1}%")
    if risk:
        bits.append(" ".join(risk))
    return _cap(". ".join(bits))
