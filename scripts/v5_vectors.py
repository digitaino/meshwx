#!/usr/bin/env python3
"""Generate the MeshWX v5 client bundle index and the wire test vectors.

    python3 scripts/v5_vectors.py            # writes both files
    python3 scripts/v5_vectors.py --index    # only client_data/index.json
    python3 scripts/v5_vectors.py --vectors  # only docs/meshwx_v5_vectors.json

``index.json`` is the append-only table the wire indexes into: office byte,
station u16 and state byte (spec 9).  It is derived, never hand-edited.

The vectors are the conformance fixtures for ``docs/MeshWX_v5_Spec.md``: a
client is correct when it decodes every ``hex`` to the ``decoded`` JSON and
re-encodes that JSON back to the same ``hex``.

Values are Austin-area and realistic: office EWX, stations KAUS/KGTU/KHYI,
PFM point 102 (Austin Bergstrom), and ``NOW_MIN`` = 2026-09-15 01:00 UTC.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meshcore_weather.protocol import v5  # noqa: E402

CLIENT_DATA = ROOT / "meshcore_weather" / "client_data"
INDEX_PATH = CLIENT_DATA / "index.json"
VECTORS_PATH = ROOT / "docs" / "meshwx_v5_vectors.json"

#: 2026-09-15 01:00 UTC, in Unix minutes.
NOW_MIN = 29823900

BOT = 0x4C7A  # first two bytes of the WX-AUS public key, little-endian u16


def _load(name: str):
    with open(CLIENT_DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# index.json
# --------------------------------------------------------------------------


def build_index() -> dict:
    """Offices, stations and states, in the exact order the wire indexes."""
    return {
        "version": 1,
        "offices": sorted(_load("wfos.json").keys()),
        "stations": sorted(_load("stations.json").keys()),
        # state_index.json is append-only and its order IS the wire order.
        "states": list(_load("state_index.json")["states"]),
    }


def write_index() -> dict:
    index = build_index()
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, separators=(",", ":"))
        fh.write("\n")
    print(
        f"wrote {INDEX_PATH.relative_to(ROOT)}: "
        f"{len(index['offices'])} offices, {len(index['stations'])} stations, "
        f"{len(index['states'])} states"
    )
    return index


# --------------------------------------------------------------------------
# Vectors
# --------------------------------------------------------------------------

# Event codes come from protocol.json "events".
EV_SV_W = 3   # severe thunderstorm warning
EV_WS_W = 24  # winter storm warning


def build_vectors(index: dict) -> list:
    offices = index["offices"]
    stations = index["stations"]
    states = index["states"]

    ewx = offices.index("EWX")
    kaus = stations.index("KAUS")
    kgtu = stations.index("KGTU")
    khyi = stations.index("KHYI")

    vectors = []

    def add(name, data, note=None):
        entry = {"name": name, "hex": data.hex(), "decoded": v5.decode(data)}
        if note:
            entry["note"] = note
        vectors.append(entry)

    # 1. Severe thunderstorm warning: polygon (6 vertices) + 2 county runs,
    #    hail 1.00 in, wind 60 mph, tornado possible (radar indicated).
    add(
        "severe_thunderstorm_warning_polygon",
        v5.encode_warning(
            17,
            BOT,
            event=EV_SV_W,
            office=ewx,
            etn=42,
            expires_min=NOW_MIN + 45,
            tornado=v5.TAG_TORNADO_RADAR_INDICATED,
            hail_qin=4,
            wind_mph=60,
            polygon=[
                (30.52, -97.98),
                (30.61, -97.62),
                (30.38, -97.41),
                (30.15, -97.50),
                (30.09, -97.85),
                (30.28, -98.04),
            ],
            areas=v5.areas_from_ugcs(["TXC453", "TXC209"], states),
        ),
        "SV.W.EWX.42 over Travis and Hays counties; expires 01:45 UTC",
    )

    # 2. Winter storm warning: zone list only (TXZ191-194 and TXZ200).
    add(
        "winter_storm_warning_zones",
        v5.encode_warning(
            18,
            BOT,
            event=EV_WS_W,
            office=ewx,
            etn=7,
            expires_min=NOW_MIN + 1080,  # 18 hours
            areas=v5.areas_from_ugcs(
                ["TXZ191", "TXZ192", "TXZ193", "TXZ194", "TXZ200"], states
            ),
        ),
        "WS.W.EWX.7, two runs: zones 191-194 and zone 200",
    )

    # 3. Cancel: the severe thunderstorm warning expired early.
    add(
        "cancel_expired_early",
        v5.encode_cancel(
            19,
            BOT,
            event=EV_SV_W,
            office=ewx,
            etn=42,
            reason=v5.CANCEL_EXPIRED,
        ),
    )

    # 4. Digest: 3 active identities, feed last heard 28 minutes ago (7 x 4).
    add(
        "digest_three_entries",
        v5.encode_digest(
            20,
            BOT,
            now_min=NOW_MIN,
            feed_health=7,
            entries=[
                (EV_SV_W, ewx, 42, NOW_MIN + 45),
                (EV_WS_W, ewx, 7, NOW_MIN + 1080),
                (EV_SV_W, ewx, 43, NOW_MIN + 20),
            ],
        ),
    )

    # 5. Observations: KAUS complete, KGTU calm with gaps, KHYI mostly unknown.
    add(
        "observations_three_stations",
        v5.encode_obs(
            21,
            BOT,
            ts_min=NOW_MIN - 7,
            stations=[
                {
                    "station": kaus,
                    "temp_f": 88,
                    "dewpoint_f": 72,
                    "wind_dir_deg": 160,
                    "sky": 3,
                    "wind_mph": 12,
                    "gust_mph": 21,
                    "visibility_mi": 10,
                    "pressure_inhg": 29.92,
                    "humidity_pct": 59,
                    "feels_delta_f": 7,
                },
                {
                    "station": kgtu,
                    "temp_f": 84,
                    "dewpoint_f": 70,
                    "wind_dir_deg": None,  # calm
                    "sky": 1,
                    "wind_mph": 0,
                    "gust_mph": 0,
                    "visibility_mi": 10,
                    "pressure_inhg": 29.95,
                    "humidity_pct": None,
                    "feels_delta_f": 0,
                },
                {
                    "station": khyi,
                    "temp_f": None,
                    "dewpoint_f": None,
                    "wind_dir_deg": 290,
                    "sky": 10,
                    "wind_mph": None,
                    "gust_mph": 0,
                    "visibility_mi": None,
                    "pressure_inhg": None,
                    "humidity_pct": None,
                    "feels_delta_f": 0,
                },
            ],
        ),
        "KGTU is calm, KHYI has only wind direction and sky",
    )

    # 6. Forecast: point 102 (Austin Bergstrom), 7 periods from tonight.
    #    Night periods carry high = None (127), day periods low = None (127).
    add(
        "forecast_seven_periods",
        v5.encode_forecast(
            22,
            BOT,
            point=102,
            issued_min=NOW_MIN - 120,
            first_period=1,  # tonight
            periods=[
                {   # 1 tonight
                    "high_f": None, "low_f": 73, "pop_pct": 20, "sky": 2,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": True, "wind_dir_deg": 157.5, "wind_mph": 5,
                },
                {   # 2 tomorrow
                    "high_f": 93, "low_f": None, "pop_pct": 40, "sky": 3,
                    "thunder": True, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 10,
                },
                {   # 3 tomorrow night
                    "high_f": None, "low_f": 72, "pop_pct": 30, "sky": 3,
                    "thunder": True, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 5,
                },
                {   # 4
                    "high_f": 90, "low_f": None, "pop_pct": 60, "sky": 8,
                    "thunder": True, "wintry": False, "windy": True,
                    "fog": False, "wind_dir_deg": 202.5, "wind_mph": 20,
                },
                {   # 5
                    "high_f": None, "low_f": 69, "pop_pct": 20, "sky": 2,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 315, "wind_mph": 10,
                },
                {   # 6
                    "high_f": 86, "low_f": None, "pop_pct": 10, "sky": 1,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 0, "wind_mph": 10,
                },
                {   # 7
                    "high_f": None, "low_f": 65, "pop_pct": None, "sky": 0,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": None, "wind_mph": None,
                },
            ],
        ),
        "point 102 = Austin Bergstrom-Travis TX; the reserved half-day form, "
        "odd first_period with alternating temperatures",
    )

    # 6b. Forecast as this bot actually sends it: whole days, even first_period,
    #     every entry carrying BOTH a high and a low. Temperatures follow a real
    #     WX-AUS week for point 103 (spec 7).
    add(
        "forecast_seven_days",
        v5.encode_forecast(
            26,
            BOT,
            point=103,
            issued_min=NOW_MIN - 60,
            first_period=0,  # day 0; always even for daily periods
            periods=[
                {   # day 0
                    "high_f": 102, "low_f": 77, "pop_pct": 0, "sky": 0,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 10,
                },
                {   # day 1
                    "high_f": 100, "low_f": 78, "pop_pct": 0, "sky": 1,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 10,
                },
                {   # day 2
                    "high_f": 99, "low_f": 76, "pop_pct": 30, "sky": 3,
                    "thunder": True, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 10,
                },
                {   # day 3
                    "high_f": 98, "low_f": 74, "pop_pct": 10, "sky": 2,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 157.5, "wind_mph": 5,
                },
                {   # day 4
                    "high_f": 98, "low_f": 75, "pop_pct": 0, "sky": 1,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 135, "wind_mph": 5,
                },
                {   # day 5
                    "high_f": 99, "low_f": 75, "pop_pct": 10, "sky": 2,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 180, "wind_mph": 10,
                },
                {   # day 6
                    "high_f": 95, "low_f": 74, "pop_pct": 20, "sky": 3,
                    "thunder": False, "wintry": False, "windy": False,
                    "fog": False, "wind_dir_deg": 157.5, "wind_mph": 10,
                },
            ],
        ),
        "point 103 = Austin Camp Mabry-Travis TX; the daily form this bot sends",
    )

    # 7. Text: a warning narrative that needs two chunks.
    narrative = (
        "SEVERE THUNDERSTORM WARNING FOR NORTHEASTERN HAYS AND SOUTHWESTERN "
        "TRAVIS COUNTIES UNTIL 145 AM CDT. At 1257 AM a severe thunderstorm "
        "was near Dripping Springs, moving east at 40 mph. HAZARD: 60 mph "
        "gusts and quarter size hail. SOURCE: Radar indicated."
    )
    for data in v5.text_chunks(
        23, BOT, subject=v5.SUBJECT_WARNING, text=narrative
    ):
        info = v5.decode(data)
        add(
            f"text_warning_narrative_chunk{info['idx']}",
            data,
            "group 23, reassemble by (bot, group) in idx order",
        )

    # 8. Not available: a forecast for a place the bot could not resolve.
    add(
        "not_available_unknown_location",
        v5.encode_not_available(
            25, BOT, request="f round rock zz", reason=v5.REASON_UNKNOWN_LOCATION
        ),
    )

    return vectors


def write_vectors(index: dict) -> list:
    vectors = build_vectors(index)
    with open(VECTORS_PATH, "w", encoding="utf-8") as fh:
        json.dump(vectors, fh, indent=2)
        fh.write("\n")
    print(f"wrote {VECTORS_PATH.relative_to(ROOT)}: {len(vectors)} vectors")
    for entry in vectors:
        print(f"  {len(entry['hex']) // 2:3d} bytes  {entry['name']}")
    return vectors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", action="store_true", help="only write index.json")
    ap.add_argument(
        "--vectors", action="store_true", help="only write the test vectors"
    )
    args = ap.parse_args()
    both = not (args.index or args.vectors)

    index = write_index() if (both or args.index) else build_index()
    if both or args.vectors:
        write_vectors(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
