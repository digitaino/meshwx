#!/usr/bin/env python3
"""Compare the running bot's answers with sources that are not EMWIN.

    python scripts/audit.py [--bot http://localhost:8081] [--states TX,NY] [--json out.json]

Checks, all against the bot's structured /api/audit/* endpoints:
  warnings   every state: the set of VTEC events (office, phen, sig, etn) vs
             api.weather.gov active alerts, and each event's zone list
  storms     every state: bot reports in the last 6 h vs IEM local storm
             reports (the bot may have fewer — the satellite drops some
             products — but never one IEM does not have)
  obs        a nationwide station list: the bot's METAR vs the latest
             aviationweather.gov METAR for that station (identical text when
             the observation time matches; otherwise the bot's must be recent)
  forecast   a nationwide place list: highs/lows vs the api.weather.gov
             gridded forecast for the same point, within a tolerance
  space      SWPC planetary Kp and the 3-day forecast

The bot itself never uses the internet; this script does, and only here.
Exit status is non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter

import httpx

UA = {"User-Agent": "meshcore-weather audit (https://github.com/digitaino/meshwx)"}
STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
          "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR").split()
STATIONS = ["KAUS", "KEDC", "KDFW", "KIAH", "KJFK", "KORD", "KLAX", "KSEA", "KDEN", "KMIA", "KATL", "KBOS",
            "KPHX", "KMSP", "KSTL", "KBNA", "KSLC", "KPDX", "KDTW", "KCLT", "KMCI", "KOKC", "KABQ", "KBIL",
            "PANC", "PHNL", "TJSJ", "KBUF", "KSJT", "KBRO"]
PLACES = ["Austin TX", "Round Rock TX", "Dallas TX", "Houston TX", "New York NY", "Chicago IL", "Los Angeles CA",
          "Seattle WA", "Denver CO", "Miami FL", "Atlanta GA", "Boston MA", "Phoenix AZ", "Minneapolis MN",
          "Anchorage AK", "Honolulu HI", "San Juan PR", "Kansas City MO", "Billings MT", "Portland OR"]
FORECAST_TOL_F = 3
OBS_MAX_AGE_MIN = 90


class Audit:
    def __init__(self, bot: str):
        self.bot = bot.rstrip("/")
        self.c = httpx.Client(timeout=60, headers=UA, follow_redirects=True)
        self.results: list[dict] = []

    def rec(self, check: str, subject: str, ok: bool, detail: str = "") -> None:
        self.results.append({"check": check, "subject": subject, "ok": ok, "detail": detail})
        mark = "PASS" if ok else "FAIL"
        print(f"{mark} {check:8} {subject:18} {detail}", flush=True)

    def bot_get(self, path: str, **params) -> dict:
        r = self.c.get(self.bot + path, params=params)
        r.raise_for_status()
        return r.json()

    # -- warnings ---------------------------------------------------------------
    def warnings(self, states: list[str]) -> None:
        for st in states:
            try:
                api = self.c.get(f"https://api.weather.gov/alerts/active?area={st}").json()["features"]
            except Exception as e:
                self.rec("warnings", st, True, f"skipped: api.weather.gov {e}")
                continue
            api_events: dict[str, set[str]] = {}
            api_sent: dict[str, dt.datetime] = {}
            api_no_vtec = Counter()
            for f in api:
                p = f["properties"]
                ugcs = {u for u in p.get("geocode", {}).get("UGC", []) if u[:2] == st}
                vt = p.get("parameters", {}).get("VTEC") or []
                if not vt:
                    api_no_vtec[p["event"]] += 1
                try:
                    sent = dt.datetime.fromisoformat(p["sent"])
                except Exception:
                    sent = None
                for v in vt:
                    m = re.search(r"/O\.(\w+)\.[KPT](\w{3})\.(\w{2})\.(\w)\.(\d{4})\.", v)   # K=CONUS, P=Pacific/AK, T=PR
                    if not m or m.group(1) in ("CAN", "EXP"):
                        continue
                    key = f"{m.group(2)} {m.group(3)}.{m.group(4)}.{int(m.group(5))}"
                    api_events.setdefault(key, set()).update(ugcs)
                    if sent and (key not in api_sent or sent > api_sent[key]):
                        api_sent[key] = sent
            bot = self.bot_get("/api/audit/warnings", state=st)["events"]
            bot_events = {f"{e['office']} {e['phenomenon']}.{e['significance']}.{e['etn']}": set(e["ugcs"])
                          for e in bot if e.get("etn") is not None}
            # A short-fuse warning in its last minutes: NWS may already have
            # dropped it (an SVS with EXP is on its way over the satellite).
            soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)
            ending = {f"{e['office']} {e['phenomenon']}.{e['significance']}.{e['etn']}" for e in bot
                      if e.get("etn") is not None and e.get("expires") and dt.datetime.fromisoformat(e["expires"]) <= soon}
            # Issued in the last few minutes: the product is on its way over the
            # satellite (30 s directory scan, ~1 min end to end). Reported, not failed.
            recent_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
            just_issued = {k for k, t in api_sent.items() if t >= recent_cut}
            missing = sorted(set(api_events) - set(bot_events) - just_issued)
            missing_new = sorted((set(api_events) - set(bot_events)) & just_issued)
            extra = sorted(set(bot_events) - set(api_events) - ending)
            ending_extra = sorted((set(bot_events) - set(api_events)) & ending)
            zone_gaps = []
            for k in set(api_events) & set(bot_events):
                lost = api_events[k] - bot_events[k]
                if lost:
                    zone_gaps.append(f"{k} missing {len(lost)}/{len(api_events[k])} zones")
            ok = not missing and not extra and not zone_gaps
            detail = f"{len(api_events)} events"
            if missing:
                detail += f" | MISSING from bot: {', '.join(missing)}"
            if extra:
                detail += f" | bot has, NWS does not: {', '.join(extra)}"
            if zone_gaps:
                detail += " | " + "; ".join(zone_gaps)
            if ending_extra:
                detail += f" | expiring within 15 min, not counted: {', '.join(ending_extra)}"
            if missing_new:
                detail += f" | issued in the last 5 min, not yet on the satellite: {', '.join(missing_new)}"
            self.rec("warnings", st, ok, detail)

    # -- storms -----------------------------------------------------------------
    def storms(self, states: list[str], hours: int = 6) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        sts = (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%MZ")
        ets = now.strftime("%Y-%m-%dT%H:%MZ")
        try:
            feats = self.c.get(f"https://mesonet.agron.iastate.edu/geojson/lsr.geojson?sts={sts}&ets={ets}").json()["features"]
        except Exception as e:
            self.rec("storms", "ALL", True, f"skipped: IEM {e}")
            return
        iem: dict[str, list] = {}
        for f in feats:
            p = f["properties"]
            iem.setdefault(p["st"], []).append((p["valid"][:16], p["typetext"].upper(), p["city"].upper()))
        for st in states:
            bot = self.bot_get("/api/audit/storms", state=st, hours=hours)["reports"]
            iem_n = len(iem.get(st, []))
            iem_cities = {c for _, _, c in iem.get(st, [])}
            unmatched = [b for b in bot if re.sub(r"^\d+ [NSEW]{1,3} ", "", b["location"]).upper() not in
                         {re.sub(r"^\d+ [NSEW]{1,3} ", "", c) for c in iem_cities}]
            ok = not unmatched
            detail = f"bot {len(bot)} / IEM {iem_n} in last {hours}h"
            if unmatched:
                detail += " | bot reports IEM lacks: " + "; ".join(f"{b['event']} {b['location']}" for b in unmatched[:5])
            if len(bot) < iem_n:
                detail += f" | {iem_n - len(bot)} not (yet) on the satellite"
            self.rec("storms", st, ok, detail)

    # -- observations -------------------------------------------------------------
    def obs(self, stations: list[str]) -> None:
        try:
            raw = self.c.get("https://aviationweather.gov/api/data/metar?ids=" + ",".join(stations) + "&format=raw").text
        except Exception as e:
            self.rec("obs", "ALL", True, f"skipped: aviationweather {e}")
            return
        latest = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[0] in ("METAR", "SPECI"):
                parts = parts[1:]
            if parts:
                latest.setdefault(parts[0], line.strip())
        now = dt.datetime.now(dt.timezone.utc)
        for stn in stations:
            b = self.bot_get("/api/audit/obs", station=stn)
            ref = latest.get(stn)
            if not b.get("metar"):
                self.rec("obs", stn, ref is None, "bot has no METAR" + ("" if ref is None else f"; aviationweather has {ref[:40]}"))
                continue
            if b.get("metar_station") != stn:
                self.rec("obs", stn, False, f"bot answered with {b.get('metar_station')} instead of {stn}")
                continue
            bm = b["metar"].replace("METAR ", "").strip()
            m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", bm)
            age = None
            if m:
                d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    t = now.replace(day=d, hour=h, minute=mi, second=0, microsecond=0)
                    if t > now + dt.timedelta(hours=1):
                        t -= dt.timedelta(days=31)
                    age = int((now - t).total_seconds() / 60)
                except ValueError:
                    pass
            if ref:
                rm = ref.replace("METAR ", "").replace("SPECI ", "").strip()
                same_time = bm.split()[1] == rm.split()[1] if len(bm.split()) > 1 and len(rm.split()) > 1 else False
                if same_time:
                    core_b = " ".join(bm.split()[:8]); core_r = " ".join(rm.split()[:8])
                    self.rec("obs", stn, core_b == core_r, "same obs, " + ("identical" if core_b == core_r else f"DIFFERS: bot '{core_b}' vs '{core_r}'"))
                    continue
            ok = age is not None and age <= OBS_MAX_AGE_MIN
            self.rec("obs", stn, ok, f"bot obs {age} min old" + ("" if ok else f" (> {OBS_MAX_AGE_MIN})") + (f"; newest online {ref.split()[2] if ref and len(ref.split())>2 else '?'}" if ref else ""))

    # -- forecast -----------------------------------------------------------------
    def _online_pfm(self, wfo: str):
        """The same WFO's PFM from forecast.weather.gov, parsed with the bot's parser."""
        if wfo in getattr(self, "_pfm_cache", {}):
            return self._pfm_cache[wfo]
        self._pfm_cache = getattr(self, "_pfm_cache", {})
        try:
            from meshcore_weather.parser.pfm import parse_pfm
            txt = self.c.get(f"https://forecast.weather.gov/product.php?site={wfo}&product=PFM&issuedby={wfo}&format=txt&version=1&glossary=0").text
            i = txt.find("FOUS")
            pts = parse_pfm(txt[i:] if i >= 0 else txt)
        except Exception:
            pts = None
        self._pfm_cache[wfo] = pts
        return pts

    def forecast(self, places: list[str]) -> None:
        from meshcore_weather.parser.pfm import downsample_to_daily
        for place in places:
            try:
                b = self.bot_get("/api/audit/forecast", place=place)
            except httpx.HTTPStatusError as e:
                self.rec("forecast", place, False, f"bot: {e.response.text[:80]}")
                continue
            if not b.get("periods"):
                self.rec("forecast", place, False, "bot has no forecast" + (f" (nearest point {b.get('point_km')} km)" if b.get("point_km") else ""))
                continue
            # Primary: the same PFM point as published on forecast.weather.gov.
            pts = self._online_pfm(b.get("wfo") or "")
            ref = None
            if pts:
                ref = next((p for p in pts if p.name == b["point"]), None)
            if ref is not None:
                ref_days = {d.local_date.isoformat(): (d.high_f, d.low_f) for d in downsample_to_daily(ref)}
                start = dt.date.fromisoformat(b["start_date"])
                diffs, checked = [], 0
                for per in b["periods"][:5]:
                    day = (start + dt.timedelta(days=per["day"])).isoformat()
                    if day not in ref_days:
                        continue
                    checked += 1
                    rh, rl = ref_days[day]
                    if rh != per["high_f"] or rl != per["low_f"]:
                        diffs.append(f"{day} bot {per['high_f']}/{per['low_f']} vs PFM {rh}/{rl}")
                same_issue = ref.issue_time and b.get("issued") and ref.issue_time.isoformat()[:16] == b["issued"][:16]
                ok = checked > 0 and (not diffs or not same_issue)
                note = f"PFM {b['point']} " + (f"{checked} days identical" if not diffs else "; ".join(diffs))
                if diffs and not same_issue:
                    note += f" (different issuances: bot {b.get('issued','')[:16]} vs online {ref.issue_time.isoformat()[:16] if ref.issue_time else '?'}; not counted as a failure)"
                self.rec("forecast", place, ok, note)
                continue
            # Fallback: NWS gridded forecast for the point, loose tolerance
            # (a PFM point and a grid cell are different products).
            try:
                pt = self.c.get(f"https://api.weather.gov/points/{b['lat']:.4f},{b['lon']:.4f}").json()["properties"]
                periods = self.c.get(pt["forecast"]).json()["properties"]["periods"]
            except Exception as e:
                self.rec("forecast", place, True, f"skipped: no online PFM point and api.weather.gov {e}")
                continue
            api: dict[str, dict] = {}
            for p in periods:
                day = p["startTime"][:10]
                api.setdefault(day, {})["high" if p["isDaytime"] else "low"] = p["temperature"]
            start = dt.date.fromisoformat(b["start_date"]) if b.get("start_date") else None
            diffs, checked = [], 0
            for per in b["periods"][:5]:
                if start is None:
                    break
                day = (start + dt.timedelta(days=per["day"])).isoformat()
                r = api.get(day)
                if not r:
                    continue
                for k, v in (("high", per["high_f"]), ("low", per["low_f"])):
                    if k in r:
                        checked += 1
                        if abs(r[k] - v) > 6:
                            diffs.append(f"{day} {k} bot {v} vs grid {r[k]}")
            self.rec("forecast", place, checked > 0 and not diffs,
                     f"grid check ({b['point']} not in online PFM): " + ("; ".join(diffs) or f"{checked} values within 6F"))

    # -- space --------------------------------------------------------------------
    def space(self) -> None:
        b = self.bot_get("/api/audit/space")
        if not b.get("available"):
            self.rec("space", "swpc", False, "bot has no space weather")
            return
        try:
            t3 = self.c.get("https://services.swpc.noaa.gov/text/3-day-forecast.txt").text
            kp = self.c.get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json").json()
        except Exception as e:
            self.rec("space", "swpc", True, f"skipped: SWPC {e}")
            return
        m = re.search(r"past 24 hours was\s+([\d.]+)", t3)
        m2 = re.search(r"Kp index breakdown\s+(\w{3})\s+(\d{1,2})-", t3)
        issues = []
        if m and abs(float(m.group(1)) - b["kp_max_24h"]) > 0.4:
            issues.append(f"kp_max_24h bot {b['kp_max_24h']} vs SWPC {m.group(1)} (SWPC text may be a newer issuance)")
        if m2 and b.get("first_day"):
            months = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
            if months.get(m2.group(1).title()) != int(b["first_day"][5:7]) or int(m2.group(2)) != int(b["first_day"][8:10]):
                issues.append(f"first forecast day bot {b['first_day']} vs SWPC {m2.group(1)} {m2.group(2)}")
        live_kp = None
        if kp:
            last = kp[-1]
            try:
                live_kp = float(last["Kp"]) if isinstance(last, dict) else float(last[1])
            except (KeyError, ValueError, TypeError, IndexError):
                live_kp = None
        if live_kp is not None and live_kp >= 4 and b.get("kp_now", 0) < 4 and b.get("kp_expected", 0) < 4:
            issues.append(f"SWPC Kp {live_kp} now but bot shows no K alert (alert may not have arrived)")
        self.rec("space", "swpc", not issues, "; ".join(issues) or f"Kp24h max {b['kp_max_24h']}, live Kp {live_kp}, columns start {b.get('first_day')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="http://localhost:8081")
    ap.add_argument("--states", default=",".join(STATES))
    ap.add_argument("--stations", default=",".join(STATIONS))
    ap.add_argument("--places", default="|".join(PLACES))
    ap.add_argument("--json", default="")
    ap.add_argument("--only", default="", help="comma list of checks: warnings,storms,obs,forecast,space")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set()
    au = Audit(a.bot)
    states = [s.strip().upper() for s in a.states.split(",") if s.strip()]
    if not only or "warnings" in only:
        au.warnings(states)
    if not only or "storms" in only:
        au.storms(states)
    if not only or "obs" in only:
        au.obs([s.strip().upper() for s in a.stations.split(",") if s.strip()])
    if not only or "forecast" in only:
        au.forecast([p.strip() for p in a.places.split("|") if p.strip()])
    if not only or "space" in only:
        au.space()
    fails = [r for r in au.results if not r["ok"]]
    print(f"\n{len(au.results) - len(fails)} passed, {len(fails)} failed")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": au.results}, f, indent=1)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
