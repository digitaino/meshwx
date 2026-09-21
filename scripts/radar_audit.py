#!/usr/bin/env python3
"""Score the radar tiles the bot sends against the pictures they were cut from.

    scripts/radar_audit.py run                 collect, then evaluate
    scripts/radar_audit.py collect             fetch what was pulled, and the pictures
    scripts/radar_audit.py evaluate            score, draw the sheets, write the report
    scripts/radar_audit.py compare             score other cell rules over the whole corpus

A tile is 32 x 32 cells of four levels, so it is lossy on purpose. What this
measures is whether it is lossy in the way intended, against the same picture
at full resolution:

- precision and recall of the tile against the pixels, at light, moderate and
  heavy.  A cell is the strongest echo in it, so recall should be about 1 and
  precision is the price of that: how much wetter the tile reads than the
  picture.
- things that should not be there: a heavy cell with nothing moderate beside
  it (a core has a collar; a road or a warning polygon does not), wet cells
  strung out one wide (a line drawn on the map, not weather).
- how much of the tile the bot could not see (roads, labels, polygon strokes)
  and had to fill from the cells around it.
- whether the bytes that went out are the bytes this code cuts today.
- the pipeline around it: does the picture still sit on the map it was
  calibrated on, was its time read off the corner, how old was it on arrival,
  and did the dish miss pictures.

Two sources of tiles.  What people actually pulled (the bot's
`data/radar_audit.jsonl`, or its journal on a bot that predates it), because
that is what matters; and the wettest tiles of the newest picture of every
product, because on a quiet mesh nobody pulls anything and the weather is
still teaching something.

Everything lands under `--root` (default `../radar-audit` beside the
repository): `corpus/` grows by about a megabyte a day and is what `compare`
replays; `reports/<stamp>/` holds REPORT.md, report.json and the side-by-side
sheets.  Needs numpy and Pillow and nothing else of the bot's.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tarfile
import io
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from meshcore_weather.protocol import v5  # noqa: E402
from meshcore_weather.radar.picture import BANNER_PX, LEGEND_PX, read_picture, received_time  # noqa: E402
from meshcore_weather.radar.tiles import (  # noqa: E402
    candidates, coarse_bounds, cut_tile, load_frames, strongest, tile_span,
)

HOST = os.environ.get("RADAR_AUDIT_HOST", "digitaino@mesh-wx.digitaino.com")
ROOT = Path(os.environ.get("RADAR_AUDIT_ROOT", REPO.parent / "radar-audit"))
PI_EMWIN = "goes-images/emwin"
PI_AUDIT = "meshcore-weather/data/radar_audit.jsonl"
JOURNAL_RE = re.compile(
    r"^(\S+) .*Radar tile (-?\d+),(-?\d+) z(\d) from (RAD\w+) taken (\d\d):(\d\d)Z: (\d+) wet cells, (\d+) B( \(coarse\))?")
COLOURS = [(245, 245, 245), (90, 190, 110), (245, 200, 40), (225, 40, 40)]
UNKNOWN = (190, 190, 200)


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def ssh(command: str, binary: bool = False, timeout: int = 180):
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", HOST, command],
                         capture_output=True, timeout=timeout)
    if out.returncode not in (0, 1):
        raise SystemExit(f"ssh {HOST}: {out.stderr.decode(errors='replace')[:300]}")
    return out.stdout if binary else out.stdout.decode(errors="replace")


def load_jsonl(path: Path) -> "list[dict]":
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(root: Path, hours: int, spread: int = 4) -> dict:
    corpus = root / "corpus"
    (corpus / "gifs").mkdir(parents=True, exist_ok=True)
    known = load_jsonl(corpus / "pulls.jsonl")
    seen = {(p["ts"], p["south"], p["west"], p["zoom"]) for p in known}
    fresh = []

    for line in ssh(f"cat {PI_AUDIT} {PI_AUDIT}.1 2>/dev/null").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        key = (rec["ts"], rec["south"], rec["west"], rec["zoom"])
        if key not in seen:
            seen.add(key)
            rec["source"] = "audit"
            fresh.append(rec)

    # A bot from before the audit file still says what it sent, in its journal.
    journal = ssh(f"journalctl -u meshcore-weather --since '-{hours}h' --no-pager -o short-iso | grep 'Radar tile'")
    for line in journal.splitlines():
        m = JOURNAL_RE.match(line)
        if not m:
            continue
        when = datetime.fromisoformat(m.group(1)).astimezone(timezone.utc)
        taken = when.replace(hour=int(m.group(6)), minute=int(m.group(7)), second=0, microsecond=0)
        if taken > when + timedelta(minutes=5):
            taken -= timedelta(days=1)
        rec = {
            "ts": int(when.timestamp()), "south": int(m.group(2)), "west": int(m.group(3)),
            "zoom": int(m.group(4)), "product": m.group(5), "taken_min": int(taken.timestamp() // 60),
            "wet": int(m.group(8)), "bytes": int(m.group(9)), "coarse": bool(m.group(10)),
            "source": "journal",
        }
        # The audit file's own line for the same send is a second or two away.
        if any(abs(rec["ts"] - k[0]) <= 5 and k[1:] == (rec["south"], rec["west"], rec["zoom"]) for k in seen):
            continue
        seen.add((rec["ts"], rec["south"], rec["west"], rec["zoom"]))
        fresh.append(rec)

    listing = ssh(f"cd {PI_EMWIN} && ls -d 20*/*-RAD*.GIF 2>/dev/null | tail -6000").split()
    by_product: "dict[str, list[tuple[datetime, str]]]" = defaultdict(list)
    for rel in listing:
        name = rel.rsplit("/", 1)[-1]
        when = received_time(name)
        if when is not None:
            by_product[name.rsplit("-", 1)[-1][:-4].upper()].append((when, rel))
    for files in by_product.values():
        files.sort()

    wanted: "set[str]" = set()
    now = datetime.now(timezone.utc)
    for files in by_product.values():
        wanted.add(files[-1][1])                          # the newest of every product
        # and a few spread through the window, so a storm at 3 a.m. is in the
        # corpus even though nobody was awake to pull it
        window = [rel for when, rel in files if now - when <= timedelta(hours=hours)]
        for i in range(spread):
            if window:
                wanted.add(window[(len(window) - 1) * i // max(1, spread)])
    for rec in fresh:                                     # and what each pull was cut from
        files = by_product.get(rec["product"], [])
        if rec.get("file"):
            wanted.update(rel for _, rel in files if rel.endswith("/" + rec["file"]))
        else:
            taken = datetime.fromtimestamp(rec["taken_min"] * 60, timezone.utc)
            wanted.update(rel for when, rel in files if taken <= when <= taken + timedelta(minutes=45))
    have = {str(p.relative_to(corpus / "gifs")) for p in (corpus / "gifs").rglob("*.GIF")}
    fetch = sorted(wanted - have)
    if fetch:
        blob = ssh(f"cd {PI_EMWIN} && tar cf - {' '.join(fetch)}", binary=True, timeout=600)
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            tar.extractall(corpus / "gifs", filter="data")

    if fresh:
        with (corpus / "pulls.jsonl").open("a") as fh:
            for rec in fresh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    feed = {}
    for product, files in sorted(by_product.items()):
        day = [w for w, _ in files if now - w <= timedelta(hours=24)]
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(day, day[1:])]
        feed[product] = {"pictures_24h": len(day), "longest_gap_min": round(max(gaps, default=0)),
                         "newest_age_min": round((now - files[-1][0]).total_seconds() / 60)}
    state = {"collected_at": now.isoformat(timespec="seconds"), "new_pulls": len(fresh),
             "fetched": len(fetch), "feed": feed}
    (corpus / "last_collect.json").write_text(json.dumps(state, indent=1))
    print(f"collect: {len(fresh)} new pulls, {len(fetch)} pictures fetched, corpus at {corpus}")
    return state


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def cell_boxes(frame, south: int, west: int, zoom: int, n: int = v5.RADAR_GRID):
    """Pixel box of every cell, exactly as `cut_tile` takes it."""
    span = tile_span(zoom)
    cell = span / n
    top, bottom = BANNER_PX, frame.height - LEGEND_PX
    ys = [frame.y(south + span - r * cell) for r in range(n + 1)]
    xs = [frame.x(west + c * cell) for c in range(n + 1)]
    boxes = {}
    for r in range(n):
        ya = max(top, int(math.floor(ys[r])))
        yb = min(bottom, max(int(math.ceil(ys[r + 1])), ya + 1))
        for c in range(n):
            xa = max(0, int(math.floor(xs[c])))
            xb = min(frame.width, max(int(math.ceil(xs[c + 1])), xa + 1))
            if yb > ya and xb > xa:
                boxes[(r, c)] = (ya, yb, xa, xb)
    return boxes


def score_tile(picture, frame, tile) -> dict:
    n = len(tile.rows)
    boxes = cell_boxes(frame, tile.south, tile.west, tile.zoom, n)
    levels = picture.levels
    inside = tile.bounds or (0, n - 1, 0, n - 1)
    tp = Counter(); fp = Counter(); fn = Counter()
    unseen_cells = 0
    unknown_px = total_px = 0
    missed_light_px = missed_strong_px = 0
    for (r, c), (ya, yb, xa, xb) in boxes.items():
        if not (inside[0] <= r <= inside[1] and inside[2] <= c <= inside[3]):
            continue
        box = levels[ya:yb, xa:xb]
        seen = box[box >= 0]
        total_px += box.size
        unknown_px += int((box < 0).sum())
        if seen.size == 0:
            unseen_cells += 1
            continue
        sent = tile.rows[r][c]
        for t in (1, 2, 3):
            wet = int((seen >= t).sum())
            if sent >= t:
                tp[t] += wet
                fp[t] += seen.size - wet
            else:
                fn[t] += wet
        if sent == 0:
            missed_light_px += int((seen == 1).sum())
            missed_strong_px += int((seen >= 2).sum())

    def ratio(a, b):
        return round(a / b, 3) if b else None

    rows = tile.rows
    wet_cells = [(r, c) for r in range(n) for c in range(n) if rows[r][c]]

    def around(r, c):
        return [rows[y][x] for y in range(max(0, r - 1), min(n, r + 2))
                for x in range(max(0, c - 1), min(n, c + 2)) if (y, x) != (r, c)]

    lonely_heavy = sum(1 for r, c in wet_cells if rows[r][c] == 3 and max(around(r, c), default=0) < 2)

    # Wet cells strung out one wide: components of six or more whose cells
    # have barely two wet neighbours each are a drawn line, not weather.
    seen_cells, line_cells = set(), 0
    for start in wet_cells:
        if start in seen_cells:
            continue
        comp, stack = [], [start]
        seen_cells.add(start)
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for y in range(max(0, r - 1), min(n, r + 2)):
                for x in range(max(0, c - 1), min(n, c + 2)):
                    if rows[y][x] and (y, x) not in seen_cells:
                        seen_cells.add((y, x))
                        stack.append((y, x))
        if len(comp) >= 6:
            mean_neighbours = statistics.mean(sum(1 for v in around(r, c) if v) for r, c in comp)
            if mean_neighbours <= 2.3:
                line_cells += len(comp)

    return {
        "wet_cells": len(wet_cells),
        "precision": {t: ratio(tp[t], tp[t] + fp[t]) for t in (1, 2, 3)},
        "recall": {t: ratio(tp[t], tp[t] + fn[t]) for t in (1, 2, 3)},
        "iou": {t: ratio(tp[t], tp[t] + fp[t] + fn[t]) for t in (1, 2, 3)},
        "unknown_px_frac": ratio(unknown_px, total_px),
        "unseen_cells": unseen_cells,
        "lonely_heavy": lonely_heavy,
        "line_cells": line_cells,
        "missed_light_px": missed_light_px,
        "missed_strong_px": missed_strong_px,
    }


def packet_for(tile, picture, frame) -> bytes:
    from meshcore_weather.protocol import v5_builders as b  # heavy import, only here
    return b.radar_message(1, 0x041D, tile, picture, frame)


def packet_size(tile) -> "tuple[int, bool]":
    """(bytes, coarse) without the builders module and its dependencies."""
    common = dict(taken_min=1, south=tile.south, west=tile.west, zoom=tile.zoom, product=0)
    try:
        return len(v5.encode_radar(1, 1, rows=tile.rows, bounds=tile.bounds, **common)), False
    except ValueError:
        bounds = coarse_bounds(tile.bounds)
        rows = v5.radar_coarsen(tile.rows)
        if bounds is not None:
            r0, r1, c0, c1 = bounds
            rows = [[v if r0 <= r <= r1 and c0 <= c <= c1 else 0 for c, v in enumerate(row)]
                    for r, row in enumerate(rows)]
        elif tile.bounds is not None:
            return 0, True
        return len(v5.encode_radar(1, 1, rows=rows, bounds=bounds, **common)), True


def draw_sheet(picture, frame, tile, path: Path, title: str) -> None:
    """Source crop | the tile in the same pixels | the crop in grey with the tile's cells outlined."""
    n = len(tile.rows)
    boxes = cell_boxes(frame, tile.south, tile.west, tile.zoom, n)
    ya = min(b[0] for b in boxes.values()); yb = max(b[1] for b in boxes.values())
    xa = min(b[2] for b in boxes.values()); xb = max(b[3] for b in boxes.values())
    source = Image.open(picture.path).convert("RGB").crop((xa, ya, xb, yb))
    h, w = yb - ya, xb - xa
    scale = max(1, 420 // max(h, w))
    big = (w * scale, h * scale)

    grid = Image.new("RGB", (w, h), COLOURS[0])
    seen = picture.levels[ya:yb, xa:xb]
    for (r, c), (a, b_, x0, x1) in boxes.items():
        unknown = tile.bounds is not None and not (
            tile.bounds[0] <= r <= tile.bounds[1] and tile.bounds[2] <= c <= tile.bounds[3])
        grid.paste(UNKNOWN if unknown else COLOURS[tile.rows[r][c]], (x0 - xa, a - ya, x1 - xa, b_ - ya))

    pixels = Image.new("RGB", (w, h), COLOURS[0])
    px = pixels.load()
    for y in range(h):
        for x in range(w):
            v = int(seen[y, x])
            px[x, y] = UNKNOWN if v < 0 else COLOURS[v]

    sheet = Image.new("RGB", (big[0] * 3 + 24, big[1] + 28), (30, 30, 34))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), title, fill=(235, 235, 235))
    for i, panel in enumerate((source, pixels, grid)):
        sheet.paste(panel.resize(big, Image.NEAREST), (i * (big[0] + 12), 28))
    sheet.save(path)


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def find_picture(corpus: Path, rec: dict, cache: dict):
    """The decoded picture a pull was cut from, by file name or by its printed time."""
    files = sorted((corpus / "gifs").rglob(f"*-{rec['product']}.GIF"))
    if rec.get("file"):
        files = [f for f in files if f.name == rec["file"]]
    else:
        lo = rec["taken_min"] * 60
        files = [f for f in files
                 if (w := received_time(f.name)) is not None and lo <= w.timestamp() <= lo + 45 * 60]
    for f in files:
        if f not in cache:
            cache[f] = read_picture(f, rec["product"])
        picture = cache[f]
        if picture is not None and int(picture.taken.timestamp() // 60) == rec["taken_min"]:
            return picture
    return None


def wettest_tiles(picture, frame, zoom: int, count: int):
    span = tile_span(zoom)
    found = []
    lat = math.ceil(frame.south / span) * span
    while lat + span <= frame.north:
        lon = math.ceil(frame.west / span) * span
        while lon + span <= frame.east:
            tile = cut_tile(picture, frame, lat, lon, zoom)
            if tile is not None and tile.wet:
                found.append(tile)
            lon += span
        lat += span
    found.sort(key=lambda t: -t.wet)
    return found[:count]


def evaluate(root: Path, hours: int, sample: int) -> Path:
    corpus = root / "corpus"
    frames = {f.id: f for f in load_frames()}
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    out = root / "reports" / stamp
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    results = []

    pulls = [p for p in load_jsonl(corpus / "pulls.jsonl") if now.timestamp() - p["ts"] <= hours * 3600]
    for rec in pulls:
        frame = frames.get(rec["product"])
        picture = find_picture(corpus, rec, cache) if frame else None
        entry = {"kind": "pull", "pull": {k: rec[k] for k in rec if k != "hex"}}
        if picture is None:
            entry["problem"] = "source picture not in the corpus"
            results.append(entry)
            continue
        tile = cut_tile(picture, frame, rec["south"], rec["west"], rec["zoom"])
        if tile is None:
            entry["problem"] = "the tile is outside the picture it was logged against"
            results.append(entry)
            continue
        entry.update(score_tile(picture, frame, tile))
        if rec.get("hex"):
            sent = v5.decode(bytes.fromhex(rec["hex"]))
            rows = v5.radar_coarsen(tile.rows) if sent["coarse"] else tile.rows
            entry["reproduced"] = sent["rows"] == ["".join(map(str, r)) for r in rows]
        else:
            entry["reproduced"] = (rec["wet"] == tile.wet) if not rec.get("coarse") else None
        entry["tile"] = [tile.south, tile.west, tile.zoom]
        entry["product"] = rec["product"]
        entry["taken"] = picture.taken.isoformat(timespec="minutes")
        name = f"pull_{rec['ts']}_{tile.south}_{tile.west}_z{tile.zoom}.png"
        draw_sheet(picture, frame, tile, out / "sheets" / name,
                   f"PULLED {rec['product']} tile {tile.south},{tile.west} z{tile.zoom} taken {picture.taken:%H:%M}Z"
                   f"  | source, pixels as classified, tile as sent")
        entry["sheet"] = f"sheets/{name}"
        results.append(entry)

    pictures = []
    newest: "dict[str, Path]" = {}
    for f in (corpus / "gifs").rglob("*.GIF"):
        product = f.name.rsplit("-", 1)[-1][:-4].upper()
        when = received_time(f.name)
        if when is None or now - when > timedelta(hours=hours):
            continue
        if product not in newest or received_time(newest[product].name) < when:
            newest[product] = f
    for product, f in sorted(newest.items()):
        frame = frames.get(product)
        if frame is None or not frame.calibrated:
            continue
        picture = cache.get(f) or read_picture(f, product)
        if picture is None:
            pictures.append({"product": product, "file": f.name, "problem": "unreadable"})
            continue
        pictures.append({
            "product": product, "file": f.name, "line_match": picture.line_match,
            "frame_ok": picture.frame_ok, "taken_is_printed": picture.taken_is_printed,
            "age_at_receipt_min": round((picture.received - picture.taken).total_seconds() / 60),
            "unknown_px_frac": round(float((picture.levels[BANNER_PX:-LEGEND_PX] < 0).mean()), 3),
            "wet_px": int((picture.levels > 0).sum()),
        })
        for zoom, count in ((0, sample), (1, 1), (2, 1)):
            for tile in wettest_tiles(picture, frame, zoom, count):
                entry = {"kind": "sample", "product": product, "tile": [tile.south, tile.west, zoom],
                         "taken": picture.taken.isoformat(timespec="minutes")}
                entry.update(score_tile(picture, frame, tile))
                entry["bytes"], entry["coarse"] = packet_size(tile)
                entry["_draw"] = (picture, frame, tile)
                results.append(entry)

    # Sheets for the samples worth a look: anything that smells of map
    # furniture, then the least faithful, a dozen in all.
    samples = [e for e in results if e["kind"] == "sample"]

    def suspicion(e):
        return (e["lonely_heavy"] * 5 + e["line_cells"] + e["unseen_cells"] / 8
                + (1 - (e["precision"][2] or 1)) * 4 + (e["missed_strong_px"] > 0) * 10)

    for e in sorted(samples, key=suspicion, reverse=True)[:12]:
        picture, frame, tile = e["_draw"]
        name = f"sample_{e['product']}_{tile.south}_{tile.west}_z{tile.zoom}.png"
        draw_sheet(picture, frame, tile, out / "sheets" / name,
                   f"SAMPLE {e['product']} tile {tile.south},{tile.west} z{tile.zoom} taken {picture.taken:%H:%M}Z"
                   f"  | source, pixels as classified, tile as sent")
        e["sheet"] = f"sheets/{name}"
    for e in samples:
        e.pop("_draw", None)

    feed = json.loads((corpus / "last_collect.json").read_text()) if (corpus / "last_collect.json").exists() else {}
    report = {"stamp": stamp, "hours": hours, "results": results, "pictures": pictures, "feed": feed.get("feed", {})}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    (out / "REPORT.md").write_text(render(report))
    print(f"evaluate: {len(pulls)} pulls, {len(samples)} sampled tiles, report at {out / 'REPORT.md'}")
    return out


def mean_of(entries, key, t=None):
    values = [(e[key][t] if t else e[key]) for e in entries if key in e]
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else None


def render(report: dict) -> str:
    res = report["results"]
    pulls = [e for e in res if e["kind"] == "pull"]
    samples = [e for e in res if e["kind"] == "sample"]
    scored = [e for e in res if "precision" in e]
    lines = [f"# Radar audit {report['stamp']}", "",
             f"Window: the last {report['hours']} hours. {len(pulls)} tiles pulled by clients, "
             f"{len(samples)} sampled from the newest picture of each product.", ""]
    lines += ["## Fidelity (tile against the source pixels)", "",
              "| Level | precision | recall | IoU |", "|---|---|---|---|"]
    for t, name in ((1, "light and up"), (2, "moderate and up"), (3, "heavy")):
        lines.append(f"| {name} | {mean_of(scored, 'precision', t)} | {mean_of(scored, 'recall', t)} | {mean_of(scored, 'iou', t)} |")
    lines += ["", "Recall near 1 is by design (a cell is its strongest echo); precision is what that costs.", ""]
    flags = [e for e in scored if e["lonely_heavy"] or e["line_cells"] or e["missed_strong_px"] or e.get("reproduced") is False]
    lines += ["## Flags", ""]
    if not flags:
        lines.append("None.")
    for e in flags:
        what = []
        if e["lonely_heavy"]:
            what.append(f"{e['lonely_heavy']} heavy cell(s) with nothing moderate beside them")
        if e["line_cells"]:
            what.append(f"{e['line_cells']} wet cells in a one-wide line")
        if e["missed_strong_px"]:
            what.append(f"{e['missed_strong_px']} moderate or heavy pixels under dry cells")
        if e.get("reproduced") is False:
            what.append("the bytes sent are not what this code cuts today")
        lines.append(f"- {e['kind']} {e['product']} tile {e['tile']}: {'; '.join(what)}" + (f" ([sheet]({e['sheet']}))" if e.get("sheet") else ""))
    lines += ["", "## Pulled by clients", ""]
    if not pulls:
        lines.append("Nothing was pulled in the window.")
    for e in pulls:
        if "problem" in e:
            lines.append(f"- {e['pull'].get('product')} {e['pull'].get('south')},{e['pull'].get('west')} z{e['pull'].get('zoom')}: {e['problem']}")
            continue
        lines.append(f"- {e['product']} tile {e['tile']} taken {e['taken']}: {e['wet_cells']} wet cells, "
                     f"precision {e['precision'][1]}/{e['precision'][2]}/{e['precision'][3]}, "
                     f"unseen cells {e['unseen_cells']}, reproduced {e.get('reproduced')} ([sheet]({e['sheet']}))")
    lines += ["", "## Pictures", "", "| Product | line match | time printed | age at receipt | masked | pictures 24 h | longest gap |",
              "|---|---|---|---|---|---|---|"]
    for p in report["pictures"]:
        feed = report["feed"].get(p["product"], {})
        if "problem" in p:
            lines.append(f"| {p['product']} | {p['problem']} | | | | {feed.get('pictures_24h', '')} | {feed.get('longest_gap_min', '')} |")
            continue
        lines.append(f"| {p['product']} | {p['line_match']} | {p['taken_is_printed']} | {p['age_at_receipt_min']} min | "
                     f"{p['unknown_px_frac']} | {feed.get('pictures_24h', '')} | {feed.get('longest_gap_min', '')} min |")
    sizes = [e["bytes"] for e in samples if e.get("bytes")]
    if sizes:
        lines += ["", f"Sampled packet sizes: median {statistics.median(sizes)} B, max {max(sizes)} B, "
                      f"coarse {sum(1 for e in samples if e.get('coarse'))} of {len(samples)}."]
    sheets = [e["sheet"] for e in res if e.get("sheet")]
    lines += ["", "## Sheets to look at", ""] + [f"- {s}" for s in sheets]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# compare: what another cell rule would have sent from the same pictures
# ---------------------------------------------------------------------------


def _two_pixels(seen) -> int:
    need = 1 if seen.size < 4 else 2
    return next((t for t in (3, 2, 1) if int((seen >= t).sum()) >= need), 0)


def _share(fraction: float):
    def rule(seen) -> int:
        need = max(1, math.ceil(seen.size * fraction))
        return next((t for t in (3, 2, 1) if int((seen >= t).sum()) >= need), 0)
    return rule


RULES = {
    "strongest (shipped)": strongest,
    "two pixels": _two_pixels,
    "10% of the cell": _share(0.10),
    "25% of the cell": _share(0.25),
}


def compare(root: Path, limit: int) -> None:
    corpus = root / "corpus"
    frames = {f.id: f for f in load_frames()}
    files = sorted((corpus / "gifs").rglob("*.GIF"))[-limit:]
    totals = {name: defaultdict(list) for name in RULES}
    for f in files:
        product = f.name.rsplit("-", 1)[-1][:-4].upper()
        frame = frames.get(product)
        if frame is None or not frame.calibrated:
            continue
        picture = read_picture(f, product)
        if picture is None:
            continue
        for zoom in (0, 1, 2):
            for base in wettest_tiles(picture, frame, zoom, 3 if zoom == 0 else 1):
                for name, rule in RULES.items():
                    tile = cut_tile(picture, frame, base.south, base.west, zoom, rule=rule)
                    s = score_tile(picture, frame, tile)
                    size, coarse = packet_size(tile)
                    t = totals[name]
                    for level in (1, 2, 3):
                        for key in ("precision", "recall"):
                            if s[key][level] is not None:
                                t[f"{key}{level}"].append(s[key][level])
                    t["bytes"].append(size)
                    t["coarse"].append(coarse)
    print(f"{len(files)} pictures\n")
    print(f"{'rule':22s} {'P light':>8s} {'R light':>8s} {'P mod':>8s} {'R mod':>8s} {'P heavy':>8s} {'R heavy':>8s} {'bytes':>6s} {'coarse':>7s}")
    for name, t in totals.items():
        if not t["bytes"]:
            continue
        cols = [statistics.mean(t[k]) if t[k] else float("nan")
                for k in ("precision1", "recall1", "precision2", "recall2", "precision3", "recall3")]
        print(f"{name:22s} " + " ".join(f"{v:8.3f}" for v in cols)
              + f" {statistics.median(t['bytes']):6.0f} {100 * sum(t['coarse']) / len(t['coarse']):6.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("run", "collect", "evaluate", "compare"))
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--hours", type=int, default=26, help="how far back pulls and pictures count")
    ap.add_argument("--sample", type=int, default=3, help="wettest zoom-0 tiles scored per product")
    ap.add_argument("--limit", type=int, default=400, help="compare: newest pictures of the corpus to replay")
    ap.add_argument("--spread", type=int, default=4, help="collect: extra pictures per product, spread over the window")
    args = ap.parse_args()
    if args.command in ("run", "collect"):
        collect(args.root, args.hours, args.spread)
    if args.command in ("run", "evaluate"):
        evaluate(args.root, args.hours, args.sample)
    if args.command == "compare":
        compare(args.root, args.limit)


if __name__ == "__main__":
    main()
