"""State and national overviews for the text path, from the same pyIEM
warning extraction the broadcasts use (replaces the old regex scans in
parser/weather.py)."""

from __future__ import annotations

from collections import Counter

from meshcore_weather.core import render_text, services
from meshcore_weather.core.vtec_names import short_name
from meshcore_weather.parser.weather import WeatherStore
from meshcore_weather.protocol.warnings import extract_active_warnings


# UGC prefixes that are marine/lake areas, not states.
_MARINE = {"AM", "AN", "GM", "PZ", "PK", "PH", "PS", "PM", "LM", "LS", "LH", "LE", "LO", "LC", "SL"}


def _by_state(warnings: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for w in warnings:
        states = {u[:2] for u in (w.get("ugcs") or w.get("zones") or []) if len(u) >= 2}
        for st in states - _MARINE:
            out.setdefault(st, []).append(w)
    return out


def national(store: WeatherStore) -> str:
    ws = extract_active_warnings(store, coverage=None)
    by_state = _by_state(ws)
    kinds = Counter(short_name(w.get("vtec_phenomenon"), w.get("vtec_significance")) for w in ws)
    top = ", ".join(f"{k} {n}" for k, n in kinds.most_common(4))
    if not ws:
        return render_text._cap("US: no active warnings. Send wx <city ST> or <ST>")
    states = " ".join(sorted(by_state))
    return render_text._cap(f"US: {len(ws)} active ({top}). States: {states}")


def state(store: WeatherStore, st: str) -> str:
    st = st.upper()
    ws = _by_state(extract_active_warnings(store, coverage=None)).get(st, [])
    sr = services.storm_reports_for(store, state=st, limit=16)
    ro = services.rain_for(store, state=st)
    bits = []
    if ws:
        kinds = Counter(short_name(w.get("vtec_phenomenon"), w.get("vtec_significance")) for w in ws)
        bits.append(f"{len(ws)} warn: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common(4)))
    else:
        bits.append("no warnings")
    if sr:
        bits.append(f"{len(sr.entries)} storm rpts")
    if ro:
        bits.append(f"rain in {len(ro.cities)}")
    return render_text._cap(f"{st}: " + " | ".join(bits) + ". warn/storm/rain <ST> for lists")


def warnings_in_state(store: WeatherStore, st: str, limit: int = 8) -> str:
    st = st.upper()
    ws = _by_state(extract_active_warnings(store, coverage=None)).get(st, [])
    if not ws:
        return render_text._cap(f"No active warnings in {st}")
    sev = {"W": 0, "A": 1, "Y": 2, "S": 3}
    ws.sort(key=lambda w: (sev.get(w.get("vtec_significance") or "S", 3), w["expires_at"]))
    # Collapse identical (event, expiry) lines: "HEAT ADV til 7:00PM x3".
    groups: dict[str, int] = {}
    for w in ws:
        name = short_name(w.get("vtec_phenomenon"), w.get("vtec_significance"))
        key = f"{name} til {render_text._when(w['expires_at'])}"
        groups[key] = groups.get(key, 0) + 1
    items = [f"{k} x{n}" if n > 1 else k for k, n in list(groups.items())[:limit]]
    more = f" +{len(groups) - limit}" if len(groups) > limit else ""
    return render_text._cap(f"{st} {len(ws)} active: " + "; ".join(items) + more)


def warnings_summary(store: WeatherStore) -> str:
    ws = extract_active_warnings(store, coverage=None)
    if not ws:
        return "No active warnings."
    counts = Counter()
    for st, lst in _by_state(ws).items():
        counts[st] = len(lst)
    body = " ".join(f"{st}({n})" if n > 1 else st for st, n in sorted(counts.items()))
    return render_text._cap(f"{len(ws)} warnings in {len(counts)} states: {body}. warn <ST> for list")
