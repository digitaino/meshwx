#!/usr/bin/env python3
"""Build geodata/places.json from GeoNames + the existing Census list.

Output rows: [NAME, STATE, lat, lon, population]

  - GeoNames cities500 supplies every populated place with population, for
    the 50 states, DC, and the territories (PR, GU, VI, AS, MP). GeoNames is
    CC-BY 4.0 (https://www.geonames.org); attribution lives in NOTICE.
  - Census places not present in GeoNames (tiny CDPs under 500 people) are
    kept with population 0 so nothing that resolved before stops resolving.

Population lets the resolver prefer Springfield MO over Springfield AR when
neither is near the bot. Sorted by state then name for stable indices.

Usage:
  python scripts/build_places.py path/to/cities500.txt
  (download: https://download.geonames.org/export/dump/cities500.zip)
Then run scripts/build_client_data.py to refresh client_data/places.json.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

GEODATA = Path(__file__).resolve().parent.parent / "meshcore_weather" / "geodata"
TERRITORIES = {"PR", "GU", "VI", "AS", "MP"}
FEATURE_CODES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLS", "PPLX", "PPLL", "PPLF"}


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c)).upper().strip()


def main(cities_path: str) -> None:
    rows: dict[tuple[str, str], list] = {}
    with open(cities_path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 15:
                continue
            country, admin1, feature = c[8], c[10], c[7]
            if feature not in FEATURE_CODES:
                continue
            if country == "US":
                state = admin1
                if len(state) != 2 or not state.isalpha():
                    continue
            elif country in TERRITORIES:
                state = country
            else:
                continue
            name = _norm(c[1])
            try:
                lat, lon, pop = float(c[4]), float(c[5]), int(c[14] or 0)
            except ValueError:
                continue
            key = (name, state)
            # Same name twice in a state: keep both (resolver picks by
            # proximity, then population), unless they are the same spot.
            if key in rows and abs(rows[key][2] - lat) < 0.02 and abs(rows[key][3] - lon) < 0.02:
                if pop > rows[key][4]:
                    rows[key] = [name, state, round(lat, 4), round(lon, 4), pop]
                continue
            rows.setdefault(key, [name, state, round(lat, 4), round(lon, 4), pop])

    out = list(rows.values())
    # Keep Census places GeoNames lacks (pop 0) so nothing regresses.
    census_path = GEODATA / "places.json"
    added = 0
    if census_path.exists():
        seen = {(r[0], r[1]) for r in out}
        for p in json.loads(census_path.read_text()):
            k = (_norm(p[0]), p[1])
            if k not in seen:
                out.append([k[0], p[1], round(p[2], 4), round(p[3], 4), int(p[4]) if len(p) > 4 else 0])
                seen.add(k)
                added += 1
    out.sort(key=lambda r: (r[1], r[0]))
    census_path.write_text(json.dumps(out, separators=(",", ":")))
    by_state = {}
    for r in out:
        by_state[r[1]] = by_state.get(r[1], 0) + 1
    print(f"places.json: {len(out)} rows ({added} kept from Census); "
          f"PR={by_state.get('PR',0)} GU={by_state.get('GU',0)} VI={by_state.get('VI',0)} "
          f"AS={by_state.get('AS',0)} MP={by_state.get('MP',0)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
