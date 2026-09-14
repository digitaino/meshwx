"""Space weather from SWPC products received over EMWIN.

Two daily products carry everything a mesh user needs:

  DAYTDF  "3-Day Forecast"          max Kp last 24 h, Kp per 3-h block for
                                    the next 3 days, S-scale and R-scale
                                    probabilities
  DAYIND  "Daily Space Weather      sunspot number, 10.7 cm solar flux (SFI),
           Indices"                 90-day flux, GOES X-ray background

SWPC's real-time alert envelope also arrives (confirmed on GOES-19 with
ALTEF3US, 2026-09-14). Every alert, warning, watch and summary shares one
layout ("Space Weather Message Code: ALTK05", "Issue Time: ...", a headline
line, then "Threshold Reached" / "Valid To" / "End Time" / "NOAA Scale"
fields), so one parser covers them:

  ALTKnn  ALERT: Geomagnetic K-index of n          observed, 3-h synoptic period
  WARKnn  WARNING: Geomagnetic K-index of n expected   Valid From/To
  WATAxx  WATCH: Geomagnetic Storm Category Gn Predicted  per-day list
  ALTXMF / SUMXM5 / SUMX01   X-ray flux M5+ (R2) or X1+ (R3)
  WARPX1 / ALTPXn / SUMPXn   proton events (S scale)

The result is one `SpaceWeather` object: 27 bytes on the wire, one line
of text for the DM path. The narrative discussion, the weekly outlook and
the event log are not carried; they are too long for LoRa and their
numbers are already here.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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
    first_day: object = None              # date of the first forecast column
    # Alert state "now", from the SWPC alert envelope (0 = nothing active)
    kp_now: int = 0          # observed K-index from a live ALTKnn (4..9)
    kp_expected: int = 0     # WARKnn in its valid window, or a watch (G+4)
    xray_alert: int = 0      # 1 = M5+ flare (R2), 2 = X1+ flare (R3), last 3 h
    proton_alert: int = 0    # 1 = S1 expected, 2 = S1 observed, 3 = S2+ observed
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
    #  25     observed K alert (hi nibble, 0 or 4..9) | expected K (lo nibble)
    #  26     X-ray alert (hi nibble: 0 none, 1 R2, 2 R3) | proton (lo nibble:
    #         0 none, 1 S1 expected, 2 S1 observed, 3 S2+ observed)
    SIZE = 27

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
            ">BIBBBBBBBBBBBBBBBHHBBB",
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
            ((self.kp_now & 0xF) << 4) | (self.kp_expected & 0xF),
            ((self.xray_alert & 0xF) << 4) | (self.proton_alert & 0xF),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "SpaceWeather":
        if len(data) < cls.SIZE or data[0] != MSG_SPACE_WEATHER:
            raise ValueError("Invalid space weather message")
        (_, issued, kp24, k1, k2, k3, g12, g3, s1a, s1b, s1c,
         ra, rb, rc, r3a, r3b, r3c, sfi, ssn, xray, kpa, alerts) = struct.unpack_from(">BIBBBBBBBBBBBBBBBHHBBB", data)
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
            kp_now=kpa >> 4,
            kp_expected=kpa & 0xF,
            xray_alert=alerts >> 4,
            proton_alert=alerts & 0xF,
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
    # "NOAA Kp index breakdown Sep 14-Sep 16 2026": the three columns start on
    # the first date, which is the issue date (not the day after).
    m = re.search(r"Kp index breakdown\s+(\w{3})\s+(\d{1,2})-\w{3}\s+\d{1,2}\s+(\d{4})", t)
    out["first_day"] = None
    if m and _MONTHS.get(m.group(1).title()):
        out["first_day"] = datetime(int(m.group(3)), _MONTHS[m.group(1).title()], int(m.group(2)), tzinfo=timezone.utc).date()
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


# -- SWPC alert envelope ------------------------------------------------------------


_SWPC_TIME_RE = r"(\d{4})\s+(\w{3})\s+(\d{1,2})\s+(\d{4})\s*UTC"
_HEADLINE_RE = re.compile(
    r"^(?:(CONTINUED|EXTENDED|CANCEL)\s+)?(ALERT|WARNING|WATCH|SUMMARY):\s*(.+)$", re.M)


def _swpc_time(text: str, label: str) -> datetime | None:
    m = re.search(rf"^{re.escape(label)}:\s*{_SWPC_TIME_RE}", text, re.M)
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).title())
    if not mon:
        return None
    hhmm = m.group(4)
    return datetime(int(m.group(1)), mon, int(m.group(3)), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def parse_swpc_alert(text: str) -> dict | None:
    """One SWPC alert/warning/watch/summary message -> plain dict.

    Keys: code ("ALTK05"), kind ("ALERT"...), cancel (bool), issued_at,
    headline, k (int|None, from "K-index of 5"), scale ((letter, level)|None,
    from "NOAA Scale: G1 - Minor"), threshold_at, valid_to, begin_at, end_at,
    watch_days ({date: G-level} for WATA messages).
    """
    t = text.replace("\r", "")
    m = re.search(r"^Space Weather Message Code:\s*([A-Z0-9]{6})", t, re.M)
    if not m:
        return None
    out: dict = {"code": m.group(1), "issued_at": _swpc_time(t, "Issue Time")}
    h = _HEADLINE_RE.search(t)
    if not h:
        return None
    out["kind"] = h.group(2)
    out["cancel"] = h.group(1) == "CANCEL"
    out["headline"] = h.group(3).strip()
    k = re.search(r"K-index of (\d)", out["headline"])
    out["k"] = int(k.group(1)) if k else None
    sc = re.search(r"^NOAA Scale:\s*([GSR])(\d)", t, re.M)
    out["scale"] = (sc.group(1), int(sc.group(2))) if sc else None
    for key, label in (("threshold_at", "Threshold Reached"), ("valid_to", "Valid To"),
                       ("begin_at", "Begin Time"), ("end_at", "End Time")):
        out[key] = _swpc_time(t, label)
    # Watches list the predicted level per day: "Sep 16:  G2 (Moderate)"
    days: dict = {}
    year = out["issued_at"].year if out["issued_at"] else datetime.now(timezone.utc).year
    for mon, day, g in re.findall(r"(\w{3})\s+(\d{1,2}):\s+(?:G(\d)|None)", t):
        mi = _MONTHS.get(mon.title())
        if mi:
            days[datetime(year, mi, int(day), tzinfo=timezone.utc).date()] = int(g) if g else 0
    out["watch_days"] = days
    return out


_ALERT_TYPES = {"ALT", "WAR", "WAT", "SUM"}
_K_ALERT_HOURS = 3       # a K alert covers one 3-h synoptic period
_XRAY_HOURS = 3          # radio blackout lasts about as long as the flare
_PROTON_HOURS = 24       # proton events run for a day or more; SUMPX ends them


def alert_state(store: WeatherStore, now: datetime | None = None) -> dict:
    """Current alert numbers from every SWPC alert message in the store."""
    now = now or datetime.now(timezone.utc)
    msgs = []
    for p in store._products.values():
        if p.product_type in _ALERT_TYPES and "KWNP" in p.filename:
            a = parse_swpc_alert(p.raw_text)
            if a and a["issued_at"]:
                a["id"] = p.filename
                msgs.append(a)
    msgs.sort(key=lambda a: a["issued_at"])
    state = {"kp_now": 0, "kp_expected": 0, "xray_alert": 0, "proton_alert": 0, "ids": []}
    latest_by_code: dict[str, dict] = {a["code"]: a for a in msgs}   # last message per code wins

    def _fresh(t: datetime | None, hours: int) -> bool:
        return t is not None and t <= now < t + timedelta(hours=hours)

    for code, a in latest_by_code.items():
        used = False
        if code.startswith("ALTK") and a["k"] and _fresh(a["threshold_at"] or a["issued_at"], _K_ALERT_HOURS):
            state["kp_now"] = max(state["kp_now"], a["k"]); used = True
        elif code.startswith("WARK") and a["k"] and not a["cancel"] and a["valid_to"] and a["issued_at"] <= now < a["valid_to"]:
            state["kp_expected"] = max(state["kp_expected"], a["k"]); used = True
        elif code.startswith("WATA") and not a["cancel"]:
            today = now.date()
            future = [g for d, g in a["watch_days"].items() if d >= today]
            g = max(future) if future else 0
            if g:
                state["kp_expected"] = max(state["kp_expected"], g + 4); used = True
        elif code in ("ALTXMF", "SUMXM5", "SUMX01") or (code.startswith("SUMX") and a["scale"]):
            when = a["end_at"] or a["threshold_at"] or a["issued_at"]
            if _fresh(when, _XRAY_HOURS):
                level = 2 if (a["scale"] and a["scale"][1] >= 3) or "X1" in a["headline"] else 1
                state["xray_alert"] = max(state["xray_alert"], level); used = True
        elif code.startswith("WARPX") and not a["cancel"] and a["valid_to"] and a["issued_at"] <= now < a["valid_to"]:
            state["proton_alert"] = max(state["proton_alert"], 1); used = True
        elif code.startswith("ALTPX"):
            ended = latest_by_code.get("SUMPX" + code[-1])
            over = ended is not None and ended["end_at"] is not None and ended["end_at"] > (a["threshold_at"] or a["issued_at"])
            if not over and _fresh(a["threshold_at"] or a["begin_at"] or a["issued_at"], _PROTON_HOURS):
                s_level = a["scale"][1] if a["scale"] else int(code[-1])
                state["proton_alert"] = max(state["proton_alert"], 2 if s_level <= 1 else 3); used = True
        if used:
            state["ids"].append(a["id"])
    return state


# -- Service ------------------------------------------------------------------------


def _newest(store: WeatherStore, emwin_id: str) -> EMWINProduct | None:
    best = None
    for p in store._products.values():
        if p.emwin_id == emwin_id and (best is None or p.timestamp > best.timestamp):
            best = p
    return best


def space_weather_for(store: WeatherStore, now: datetime | None = None) -> SpaceWeather | None:
    """Latest 3-Day Forecast merged with the latest Daily Indices and live alerts."""
    tdf = _newest(store, "DAYTDFUS")
    ind = _newest(store, "DAYINDUS")
    if tdf is None and ind is None:
        al = alert_state(store, now)
        if not al["ids"]:
            return None
        return SpaceWeather(issued_at=datetime.now(timezone.utc), kp_max_24h=0.0, kp_forecast=[],
                            g_forecast=[], s1_prob=[], r12_prob=[], r3_prob=[],
                            kp_now=al["kp_now"], kp_expected=al["kp_expected"],
                            xray_alert=al["xray_alert"], proton_alert=al["proton_alert"],
                            source_ids=al["ids"])
    # Either product alone is still worth answering with: the forecast
    # without indices, or (a stale feed) the indices without a Kp forecast.
    f = parse_3day_forecast(tdf.raw_text) if tdf else {
        "issued_at": None, "kp_max_24h": 0.0, "kp_forecast": [], "g_forecast": [],
        "s1_prob": [], "r12_prob": [], "r3_prob": [],
    }
    i = parse_daily_indices(ind.raw_text) if ind else {}
    al = alert_state(store, now)
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
        first_day=f.get("first_day"),
        kp_now=al["kp_now"],
        kp_expected=al["kp_expected"],
        xray_alert=al["xray_alert"],
        proton_alert=al["proton_alert"],
        source_ids=[x.filename for x in (tdf, ind) if x] + al["ids"],
    )


def alert_text(sw: SpaceWeather) -> str:
    """'!G2 storm now (K6) !R3 blackout' or '' when nothing is active."""
    parts = []
    if sw.kp_now:
        g = g_scale_for_kp(sw.kp_now)
        parts.append(f"!G{g} storm now (K{sw.kp_now})" if g else f"!K{sw.kp_now} active")
    elif sw.kp_expected:
        g = g_scale_for_kp(sw.kp_expected)
        parts.append(f"!G{g} expected" if g else f"!K{sw.kp_expected} expected")
    if sw.xray_alert:
        parts.append(f"!R{sw.xray_alert + 1} radio blackout")
    if sw.proton_alert:
        parts.append({1: "!S1 protons expected", 2: "!S1 proton event", 3: "!S2+ proton event"}[min(3, sw.proton_alert)])
    return " ".join(parts)


def render(sw: SpaceWeather | None) -> str:
    """One-line DM: 'Kp now 3.0, next 3d 3.7/3.7/4.7 (G1 Tue). SFI 114 SSN 77 xray B3.0. R1-2 10%'."""
    if sw is None:
        return "No space weather products on file"
    from meshcore_weather.core.render_text import _cap, _local
    if sw.first_day is not None:
        days = [(datetime.combine(sw.first_day, datetime.min.time()) + timedelta(days=n)).strftime("%a") for n in range(3)]
    else:
        days = [(_local(sw.issued_at) + timedelta(days=n)).strftime("%a") for n in range(3)]
    bits = []
    live = alert_text(sw)
    if live:
        bits.append(live)
    if sw.kp_forecast:
        kps = "/".join(f"{k:.1f}" for k in sw.kp_forecast)
        gmax = max(sw.g_forecast) if sw.g_forecast else 0
        gtxt = ""
        if gmax:
            when = days[sw.g_forecast.index(gmax)]
            gtxt = f" (G{gmax} {when})"
        bits.append(f"Kp 24h max {sw.kp_max_24h:.0f}, next 3d {kps}{gtxt}")
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
