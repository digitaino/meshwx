"""Shared test guards.

The traffic log is a module singleton that persists lifetime counters to
`data/traffic_stats.json` under the working directory. Tests that exercise
the bot's message handlers would otherwise write phantom requests into the
real data directory when the suite runs on the Pi, so persistence is off for
every test; the persistence test builds its own TrafficLog with a tmp path.
"""

import pytest

from meshcore_weather.config import settings
from meshcore_weather.meshcore.delivery import delivery_tracker
from meshcore_weather.traffic import traffic_log


@pytest.fixture(autouse=True)
def _no_dm_reply_pause(monkeypatch):
    """Handler tests run on the real clock, where the 2 s pause before a DM
    reply would really wait. The tests of the pause set it themselves."""
    monkeypatch.setattr(settings, "dm_reply_delay_s", 0.0)


@pytest.fixture(autouse=True)
def _no_traffic_persistence():
    traffic_log._path = None
    yield


@pytest.fixture(autouse=True)
def _no_delivery_persistence():
    """DM replies in handler tests reach the delivery tracker singleton, which
    saves outcome rows to `data/delivery_outcomes.json`: not from a test."""
    delivery_tracker._persist_path = None
    yield


@pytest.fixture(autouse=True)
def _warning_state_in_tmp(tmp_path, monkeypatch):
    """The scheduler persists warning state; never into the real data dir from a test."""
    try:
        import meshcore_weather.schedule.scheduler as sched_mod
    except ImportError:          # a dev venv without pyIEM: those tests skip themselves
        return
    monkeypatch.setattr(sched_mod, "_STATE_PATH", tmp_path / "warning_state.json")
