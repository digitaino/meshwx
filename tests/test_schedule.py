"""Tests for the unified broadcast schedule system."""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meshcore_weather.schedule.models import (
    BroadcastConfig,
    BroadcastJob,
    LOCATION_TYPES,
    PRODUCT_TYPES,
)
from meshcore_weather.schedule import store as store_module


# -- Model validation --------------------------------------------------------


class TestBroadcastJobValidation:
    def test_minimal_valid_job(self):
        job = BroadcastJob(
            id="test-1",
            name="Test job",
            product="observations",
            location_type="coverage",
            interval_minutes=15,
        )
        assert job.id == "test-1"
        assert job.enabled is True  # default
        assert job.location_id == ""

    def test_id_gets_lowercased(self):
        job = BroadcastJob(
            id="UPPER-CASE",
            name="x",
            product="observations",
            location_type="coverage",
            interval_minutes=5,
        )
        assert job.id == "upper-case"

    def test_id_rejects_spaces(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="has space",
                name="x",
                product="observations",
                location_type="coverage",
                interval_minutes=5,
            )

    def test_id_rejects_special_chars(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="has/slash",
                name="x",
                product="observations",
                location_type="coverage",
                interval_minutes=5,
            )

    def test_unknown_product_rejected(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="ok",
                name="x",
                product="something_bogus",
                location_type="coverage",
                interval_minutes=5,
            )

    def test_unknown_location_type_rejected(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="ok",
                name="x",
                product="observations",
                location_type="not_a_real_type",
                interval_minutes=5,
            )

    def test_zero_interval_rejected(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="ok",
                name="x",
                product="observations",
                location_type="coverage",
                interval_minutes=0,
            )

    def test_negative_interval_rejected(self):
        with pytest.raises(Exception):
            BroadcastJob(
                id="ok",
                name="x",
                product="observations",
                location_type="coverage",
                interval_minutes=-5,
            )

    def test_all_products_are_accepted(self):
        """Every product in PRODUCT_TYPES constructs a valid job."""
        for product in PRODUCT_TYPES:
            job = BroadcastJob(
                id=f"test-{product}",
                name=product,
                product=product,
                location_type="coverage",
                interval_minutes=60,
            )
            assert job.product == product

    def test_all_location_types_are_accepted(self):
        for lt in LOCATION_TYPES:
            job = BroadcastJob(
                id=f"test-{lt}",
                name=lt,
                product="observations",
                location_type=lt,
                interval_minutes=60,
            )
            assert job.location_type == lt


class TestBroadcastConfigMutations:
    def test_upsert_insert(self):
        cfg = BroadcastConfig()
        job = BroadcastJob(
            id="a",
            name="A",
            product="observations",
            location_type="coverage",
            interval_minutes=5,
        )
        cfg.upsert_job(job)
        assert len(cfg.jobs) == 1
        assert cfg.get_job("a") is not None

    def test_upsert_replaces_existing(self):
        cfg = BroadcastConfig()
        cfg.upsert_job(
            BroadcastJob(
                id="a",
                name="A",
                product="observations",
                location_type="coverage",
                interval_minutes=5,
            )
        )
        cfg.upsert_job(
            BroadcastJob(
                id="a",
                name="A prime",
                product="observations",
                location_type="coverage",
                interval_minutes=15,
            )
        )
        assert len(cfg.jobs) == 1
        assert cfg.get_job("a").name == "A prime"
        assert cfg.get_job("a").interval_minutes == 15

    def test_delete(self):
        cfg = BroadcastConfig()
        cfg.upsert_job(
            BroadcastJob(
                id="a",
                name="A",
                product="observations",
                location_type="coverage",
                interval_minutes=5,
            )
        )
        assert cfg.delete_job("a") is True
        assert cfg.delete_job("a") is False  # second delete is a no-op
        assert len(cfg.jobs) == 0

    def test_json_roundtrip(self):
        cfg = BroadcastConfig(
            version=1,
            jobs=[
                BroadcastJob(
                    id=f"job-{i}",
                    name=f"Job {i}",
                    product="observations",
                    location_type="coverage",
                    interval_minutes=15 + i,
                )
                for i in range(5)
            ],
        )
        js = cfg.model_dump_json()
        restored = BroadcastConfig(**json.loads(js))
        assert len(restored.jobs) == 5
        for i, job in enumerate(restored.jobs):
            assert job.id == f"job-{i}"
            assert job.interval_minutes == 15 + i


# -- Persistence -------------------------------------------------------------


class TestStorePersistence:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        # Redirect CONFIG_PATH to a temp location
        tmp_cfg = tmp_path / "broadcast_config.json"
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        cfg = BroadcastConfig(
            version=1,
            jobs=[
                BroadcastJob(
                    id="round-trip",
                    name="Round trip",
                    product="forecast",
                    location_type="city",
                    location_id="Austin TX",
                    interval_minutes=60,
                )
            ],
        )
        store_module.save_config(cfg)
        assert tmp_cfg.exists()
        loaded = store_module.load_config()
        assert len(loaded.jobs) == 4          # the saved job plus the three missing core jobs
        assert loaded.jobs[0].id == "round-trip"
        assert loaded.jobs[0].location_id == "Austin TX"

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path, monkeypatch):
        tmp_cfg = tmp_path / "broadcast_config.json"
        tmp_cfg.write_text("{this is not valid json")
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        cfg = store_module.load_config()
        # Corrupt file → falls back to default_config_for_bootstrap, which
        # always emits at least warnings-delta + warnings-full.
        ids = {j.id for j in cfg.jobs}
        assert {"warnings", "digest", "observations", "forecast"} <= ids

    def test_missing_file_bootstraps_and_saves(self, tmp_path, monkeypatch):
        tmp_cfg = tmp_path / "broadcast_config.json"
        assert not tmp_cfg.exists()
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        cfg = store_module.load_config()
        # Default bootstrap ran and the result was persisted
        assert tmp_cfg.exists()
        assert len(cfg.jobs) >= 2  # at minimum warnings delta + full

    def test_atomic_write_does_not_leave_tmp(self, tmp_path, monkeypatch):
        """save_config should use temp-then-rename so no .tmp files survive."""
        tmp_cfg = tmp_path / "broadcast_config.json"
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)
        cfg = BroadcastConfig(version=1, jobs=[])
        store_module.save_config(cfg)
        # The temp file should not exist after a successful write
        tmp_file = tmp_cfg.with_suffix(".json.tmp")
        assert not tmp_file.exists()


# -- Default bootstrap -------------------------------------------------------


class TestBootstrap:
    def test_bootstrap_has_the_four_v5_jobs(self, monkeypatch):
        from meshcore_weather.config import settings
        monkeypatch.setattr(settings, "home_cities", "Austin TX,Dallas TX")
        cfg = store_module.default_config_for_bootstrap()
        by_id = {j.id: j for j in cfg.jobs}
        assert set(by_id) == {"warnings", "digest", "observations", "forecast"}
        assert by_id["warnings"].interval_minutes == 2 and by_id["digest"].interval_minutes == 180
        assert by_id["observations"].location_type == "coverage"
        assert by_id["forecast"].location_type == "city" and by_id["forecast"].location_id == "Austin TX"

    def test_bootstrap_with_no_home_cities_forecasts_the_coverage_centre(self, monkeypatch):
        from meshcore_weather.config import settings
        monkeypatch.setattr(settings, "home_cities", "")
        cfg = store_module.default_config_for_bootstrap()
        assert len(cfg.jobs) == 4 and cfg.get_job("forecast").location_type == "coverage"

    def test_v4_config_migrates_to_v5_jobs(self, tmp_path, monkeypatch):
        """The Pi's old file: delta -> warnings, full -> digest, per-city obs -> one batch."""
        import json
        from meshcore_weather.config import settings
        monkeypatch.setattr(settings, "home_cities", "Austin TX")
        old = {"version": 1, "jobs": [
            {"id": "warnings-delta", "name": "d", "product": "warnings_delta", "location_type": "coverage",
             "location_id": "", "interval_minutes": 2, "enabled": False},
            {"id": "warnings-full", "name": "f", "product": "warnings", "location_type": "coverage",
             "location_id": "", "interval_minutes": 120, "enabled": True},
            {"id": "obs-austin-tx", "name": "o", "product": "observation", "location_type": "city",
             "location_id": "Austin TX", "interval_minutes": 60, "enabled": True},
            {"id": "obs-dallas-tx", "name": "o2", "product": "observation", "location_type": "city",
             "location_id": "Dallas TX", "interval_minutes": 60, "enabled": True},
            {"id": "forecast-austin-tx", "name": "fc", "product": "forecast", "location_type": "city",
             "location_id": "Austin TX", "interval_minutes": 60, "enabled": True},
            {"id": "afd", "name": "afd", "product": "afd", "location_type": "wfo",
             "location_id": "EWX", "interval_minutes": 720, "enabled": True},
        ]}
        cfg_path = tmp_path / "broadcast_config.json"
        cfg_path.write_text(json.dumps(old))
        monkeypatch.setattr(store_module, "CONFIG_PATH", cfg_path)
        cfg = store_module.load_config()
        by_id = {j.id: j for j in cfg.jobs}
        assert set(by_id) == {"warnings", "digest", "observations", "forecast-austin-tx"}
        assert by_id["warnings"].product == "warnings" and by_id["warnings"].enabled is False
        assert by_id["digest"].product == "digest" and by_id["digest"].interval_minutes == 180
        assert by_id["observations"].location_type == "coverage"
        assert by_id["forecast-austin-tx"].interval_minutes == 180


# -- Scheduler semantics -----------------------------------------------------


class TestSchedulerTick:
    @pytest.mark.asyncio
    async def test_job_does_not_run_before_interval_elapsed(self, tmp_path, monkeypatch):
        """A job with interval=60min that ran 1s ago should NOT run on the next tick."""
        from meshcore_weather.parser.weather import WeatherStore
        from meshcore_weather.schedule.scheduler import Scheduler

        tmp_cfg = tmp_path / "broadcast_config.json"
        tmp_cfg.write_text(
            BroadcastConfig(
                version=1,
                jobs=[
                    BroadcastJob(
                        id="far-future",
                        name="shouldn't run",
                        product="warnings",
                        location_type="coverage",
                        interval_minutes=60,
                    )
                ],
            ).model_dump_json()
        )
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        store = WeatherStore()
        radio = MagicMock()
        radio.send_lock = asyncio.Lock()
        radio.send_channel_data = AsyncMock(return_value=True)
        sched = Scheduler(store, radio)

        # Skip start() to avoid opening an HTTP client; just initialize state manually
        sched._coverage = sched._coverage  # no-op; Coverage.empty() by default
        await sched._reload_config()
        # Fake "already ran this tick" (for the core jobs load_config adds too)
        for j in sched._config.jobs:
            sched._last_run[j.id] = time.time()
        sched._http_client = None

        sent_count = await sched.tick()
        assert sent_count == 0
        # send_binary_channel should not have been called
        radio.send_channel_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_job_is_skipped(self, tmp_path, monkeypatch):
        from meshcore_weather.parser.weather import WeatherStore
        from meshcore_weather.schedule.scheduler import Scheduler

        tmp_cfg = tmp_path / "broadcast_config.json"
        tmp_cfg.write_text(
            BroadcastConfig(
                version=1,
                jobs=[
                    BroadcastJob(
                        id="disabled-job",
                        name="off",
                        product="warnings",
                        location_type="coverage",
                        interval_minutes=1,
                        enabled=False,
                    )
                ],
            ).model_dump_json()
        )
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        store = WeatherStore()
        radio = MagicMock()
        radio.send_lock = asyncio.Lock()
        radio.send_channel_data = AsyncMock(return_value=True)
        sched = Scheduler(store, radio)

        await sched._reload_config()
        for j in sched._config.jobs:                    # only the disabled job is due
            if j.id != "disabled-job":
                sched._last_run[j.id] = time.time()

        sent_count = await sched.tick()
        assert sent_count == 0
        radio.send_channel_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_bad_builder_does_not_break_other_jobs(self, tmp_path, monkeypatch):
        """A builder that raises should log but not stop the tick from
        processing other jobs."""
        from meshcore_weather.parser.weather import WeatherStore
        from meshcore_weather.schedule import executor as executor_module
        from meshcore_weather.schedule.scheduler import Scheduler

        tmp_cfg = tmp_path / "broadcast_config.json"
        tmp_cfg.write_text(
            BroadcastConfig(
                version=1,
                jobs=[
                    BroadcastJob(
                        id="bad-one",
                        name="raises",
                        product="warnings",
                        location_type="coverage",
                        interval_minutes=1,
                    ),
                    BroadcastJob(
                        id="good-one",
                        name="works",
                        product="warnings",
                        location_type="coverage",
                        interval_minutes=1,
                    ),
                ],
            ).model_dump_json()
        )
        monkeypatch.setattr(store_module, "CONFIG_PATH", tmp_cfg)

        # Patch the warnings builder so the FIRST call raises, the SECOND returns []
        call_count = {"n": 0}
        original_builder = executor_module.PRODUCT_BUILDERS["warnings"]

        def flaky_warnings(job, ctx):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated builder failure")
            return []

        monkeypatch.setitem(executor_module.PRODUCT_BUILDERS, "warnings", flaky_warnings)

        store = WeatherStore()
        radio = MagicMock()
        radio.send_lock = asyncio.Lock()
        radio.send_channel_data = AsyncMock(return_value=True)
        sched = Scheduler(store, radio)
        await sched._reload_config()
        sched._http_client = None

        # Both jobs should execute — the exception is caught per-job
        await sched.tick()
        assert call_count["n"] == 2


# -- App requests (v5) ----------------------------------------------------------


class TestAppResponder:
    def _responder(self):
        from meshcore_weather.parser.weather import WeatherStore
        from meshcore_weather.protocol.broadcaster import AppResponder
        import meshcore_weather.schedule.scheduler as sched_mod
        sched_mod.TX_SPACING = 0
        radio = MagicMock()
        radio._mc = MagicMock()
        radio._mc.self_info = {"public_key": "1d04" + "00" * 30}
        sent = []

        async def cap(data, data_type=0xFF10, ev=None):
            sent.append(data)
            return True
        radio.send_channel_data = cap
        return AppResponder(WeatherStore(), radio, render_text=lambda c, a: None), sent

    @pytest.mark.asyncio
    async def test_unknown_station_and_unsupported_command(self):
        from meshcore_weather.protocol import v5
        r, sent = self._responder()
        await r.handle_request(">o ZZZZ", "a")
        d = v5.decode(sent[-1])
        assert d["name"] == "not_available" and d["reason"] == v5.REASON_UNKNOWN_LOCATION and d["bot"] == 0x041D
        await r.handle_request(">nope", "b")
        assert v5.decode(sent[-1])["reason"] == v5.REASON_UNSUPPORTED

    @pytest.mark.asyncio
    async def test_empty_store_answers_no_data_and_rate_limits(self):
        from meshcore_weather.protocol import v5
        r, sent = self._responder()
        await r.handle_request(">o KAUS", "a")
        assert v5.decode(sent[-1])["reason"] == v5.REASON_NO_DATA
        n = len(sent)
        assert await r.handle_request(">o KAUS", "a") == "rate limited" and len(sent) == n
        await r.handle_request(">d", "b")
        d = v5.decode(sent[-1])
        assert d["name"] == "digest" and d["entries"] == [] and d["feed_health"] == 255
