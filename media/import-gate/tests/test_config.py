import pytest
from config import Settings

BASE_ENV = {"RADARR_API_KEY": "R", "SONARR_API_KEY": "S"}

# Cleared before every case. Without this the defaults test would read whatever the
# developer happens to have exported and pass or fail by accident.
QUEUE_KEYS = (
    "QUEUE_WATCH_ENABLED",
    "QUEUE_WATCH_INTERVAL_MIN",
    "QUEUE_WATCH_MIN_AGE_MIN",
    "QUEUE_WATCH_MAX_PER_CYCLE",
    "QUEUE_WATCH_PREAIR_ENABLED",
    "QUEUE_WATCH_PREAIR_MARGIN_H",
    "QUEUE_WATCH_DRY_RUN",
)


def _env(monkeypatch, **overrides):
    for k in QUEUE_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k, v in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_queue_watch_defaults(monkeypatch):
    _env(monkeypatch)
    s = Settings.from_env()
    assert s.queue_watch_enabled is True
    assert s.queue_watch_interval_min == 10
    assert s.queue_watch_min_age_min == 15
    assert s.queue_watch_max_per_cycle == 3
    assert s.queue_watch_preair_enabled is True
    assert s.queue_watch_preair_margin_h == 24
    assert s.queue_watch_dry_run is True     # ships simulating, armed by hand


def test_queue_watch_reads_overrides(monkeypatch):
    _env(monkeypatch, QUEUE_WATCH_ENABLED="false", QUEUE_WATCH_INTERVAL_MIN="5",
         QUEUE_WATCH_PREAIR_ENABLED="false", QUEUE_WATCH_PREAIR_MARGIN_H="48",
         QUEUE_WATCH_DRY_RUN="false")
    s = Settings.from_env()
    assert s.queue_watch_enabled is False
    assert s.queue_watch_interval_min == 5
    assert s.queue_watch_preair_enabled is False
    assert s.queue_watch_preair_margin_h == 48
    assert s.queue_watch_dry_run is False


@pytest.mark.parametrize("value", ["0", "-1"])
def test_preair_margin_below_one_is_rejected(monkeypatch, value):
    """A zero margin is the aggressive mode that would blocklist legitimate
    releases appearing ~2h before airDateUtc. Fail loudly at startup instead."""
    _env(monkeypatch, QUEUE_WATCH_PREAIR_MARGIN_H=value)
    with pytest.raises(ValueError, match="QUEUE_WATCH_PREAIR_ENABLED"):
        Settings.from_env()
