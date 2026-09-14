"""Shared test guards.

The traffic log is a module singleton that persists lifetime counters to
`data/traffic_stats.json` under the working directory. Tests that exercise
the bot's message handlers would otherwise write phantom requests into the
real data directory when the suite runs on the Pi, so persistence is off for
every test; the persistence test builds its own TrafficLog with a tmp path.
"""

import pytest

from meshcore_weather.traffic import traffic_log


@pytest.fixture(autouse=True)
def _no_traffic_persistence():
    traffic_log._path = None
    yield
