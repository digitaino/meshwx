"""Persistence for the broadcast schedule config.

Stores `BroadcastConfig` as JSON at `data/broadcast_config.json`.
Writes are atomic (temp file + rename) so a crash mid-write can't
corrupt the file. Reads are tolerant of missing/corrupt files and
fall back to a sane default synthesized from the operator's
environment-variable coverage settings.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from meshcore_weather.config import settings
from meshcore_weather.schedule.models import PRODUCT_TYPES, BroadcastConfig, BroadcastJob

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(settings.data_dir) / "broadcast_config.json"


def load_config() -> BroadcastConfig:
    """Load broadcast config from disk.

    Returns a fresh default (synthesized from env vars) if the file
    doesn't exist OR if it exists but fails to parse — we'd rather
    keep broadcasting with sensible defaults than silently stop
    broadcasting because a config file got corrupted.
    """
    if not CONFIG_PATH.exists():
        logger.info(
            "No broadcast_config.json at %s — synthesizing default jobs from env",
            CONFIG_PATH,
        )
        cfg = default_config_for_bootstrap()
        # Persist the default so the operator can see/edit it
        try:
            save_config(cfg)
        except Exception as exc:
            logger.warning("Could not persist default broadcast config: %s", exc)
        return cfg

    try:
        raw = CONFIG_PATH.read_text()
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            data["jobs"] = _migrate_jobs(data["jobs"])
        data.pop("radar_grid_size", None)
        cfg = BroadcastConfig(**data)
        if _ensure_core_jobs(cfg):
            try:
                save_config(cfg)
            except Exception as exc:
                logger.warning("Could not persist migrated broadcast config: %s", exc)
        return cfg
    except Exception as exc:
        logger.warning(
            "broadcast_config.json at %s is invalid (%s) — falling back to defaults",
            CONFIG_PATH, exc,
        )
        return default_config_for_bootstrap()


def save_config(cfg: BroadcastConfig) -> None:
    """Write the config to disk atomically.

    Writes to a temp file in the same directory, then renames. This
    guarantees the destination is either the old valid file or the
    new valid file — never a half-written mess.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    json_text = cfg.model_dump_json(indent=2)
    tmp_path.write_text(json_text + "\n")
    os.replace(tmp_path, CONFIG_PATH)
    logger.debug("Wrote broadcast config to %s (%d jobs)", CONFIG_PATH, len(cfg.jobs))


# -- Default bootstrap --


def default_config_for_bootstrap() -> BroadcastConfig:
    """The v5 schedule a fresh deployment gets: warnings on change (checked
    every 2 min), the digest every 3 h, one observations packet an hour,
    and the home forecast every 6 h. Everything else is request-only."""
    cfg = BroadcastConfig(version=2, jobs=[])
    _ensure_core_jobs(cfg)
    logger.info("Bootstrap schedule: %d default jobs", len(cfg.jobs))
    return cfg


_V4_TO_V5 = {"warnings_delta": "warnings", "observation": "observations"}
_RETIRED = {"outlook", "storm_reports", "rain_obs", "metar", "taf", "warnings_near", "afd",
            "space_weather", "fire_weather", "daily_climate", "nowcast", "radar"}


def _migrate_jobs(jobs: list[dict]) -> list[dict]:
    """v4 job files keep working: delta -> warnings, the full re-broadcast
    -> digest, per-city observations -> one coverage batch, forecast kept
    at 6 h. Retired products are dropped."""
    out: list[dict] = []
    seen_obs = False
    for j in jobs:
        prod = j.get("product")
        if prod == "warnings" and j.get("id") in ("warnings-full", "warnings-coverage"):
            j = {**j, "id": "digest", "name": "Active warnings digest", "product": "digest",
                 "location_type": "coverage", "location_id": "", "interval_minutes": 180}
        elif prod in _V4_TO_V5:
            j = {**j, "product": _V4_TO_V5[prod]}
            if j["product"] == "warnings":
                j.update(id="warnings", name="Warnings on change", interval_minutes=2)
            elif j["product"] == "observations":
                if seen_obs or j.get("location_type") != "station":
                    if seen_obs:
                        continue
                    j.update(id="observations", name="Observations, coverage stations",
                             location_type="coverage", location_id="", interval_minutes=60)
                seen_obs = True
        elif prod == "forecast":
            if j.get("location_type") not in ("city", "pfm_point", "coverage"):
                continue
            j = {**j, "interval_minutes": max(int(j.get("interval_minutes") or 360), 180)}
        elif prod in _RETIRED or prod not in PRODUCT_TYPES:
            logger.warning("Dropping broadcast job %r: product %r is request-only in v5", j.get("id"), prod)
            continue
        if any(o.get("id") == j.get("id") for o in out):
            continue
        out.append(j)
    return out


def _ensure_core_jobs(cfg: BroadcastConfig) -> bool:
    """Add any of the four v5 jobs that are missing. Returns True if it did."""
    have = {j.product for j in cfg.jobs}
    added = False
    home_cities = _split_csv(settings.home_cities)
    defaults = [
        BroadcastJob(id="warnings", name="Warnings on change", product="warnings",
                     location_type="coverage", interval_minutes=2),
        BroadcastJob(id="digest", name="Active warnings digest", product="digest",
                     location_type="coverage", interval_minutes=180),
        BroadcastJob(id="observations", name="Observations, coverage stations", product="observations",
                     location_type="coverage", interval_minutes=60),
        BroadcastJob(id="forecast", name=f"Forecast: {home_cities[0]}" if home_cities else "Forecast: home",
                     product="forecast", location_type="city" if home_cities else "coverage",
                     location_id=home_cities[0] if home_cities else "", interval_minutes=360),
    ]
    for job in defaults:
        if job.product not in have and cfg.get_job(job.id) is None:
            cfg.jobs.append(job)
            added = True
    return added


# -- Helpers --


def _split_csv(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    """Convert a display string like 'Austin TX' → 'austin-tx'."""
    s = s.strip().lower()
    s = _SLUG_CHARS.sub("-", s)
    s = s.strip("-")
    return s or "item"
