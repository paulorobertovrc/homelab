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


@pytest.mark.parametrize("token", ["true", "TRUE", " True ", "1", "yes", "on"])
def test_recognised_true_tokens(monkeypatch, token):
    _env(monkeypatch, QUEUE_WATCH_ENABLED=token)
    assert Settings.from_env().queue_watch_enabled is True


@pytest.mark.parametrize("token", ["false", "FALSE", " off ", "0", "no"])
def test_recognised_false_tokens(monkeypatch, token):
    _env(monkeypatch, QUEUE_WATCH_ENABLED=token)
    assert Settings.from_env().queue_watch_enabled is False


# The exact typos a human makes reaching for "false" -- plus 'sim', which is the
# obvious wrong guess in a Portuguese-language project.
@pytest.mark.parametrize("typo", ["ture", "flase", "fasle", "sim", "nao", "y", "n", "maybe"])
def test_unrecognised_token_is_rejected_rather_than_guessed(monkeypatch, typo):
    """The old allowlist made every unrecognised token mean False. For DRY_RUN,
    False means ARMED -- so a single typo silently armed a deleter that removes
    torrents and their data. Refuse to guess; name the variable and stop."""
    _env(monkeypatch, QUEUE_WATCH_DRY_RUN=typo)
    with pytest.raises(ValueError, match="QUEUE_WATCH_DRY_RUN"):
        Settings.from_env()


def test_a_typo_never_arms_the_watcher(monkeypatch):
    """The property that actually matters, stated directly: no input short of an
    explicit recognised false value may produce an armed watcher."""
    for typo in ("ture", "flase", "sim", "y", "", "   ", "'false'", '"false"'):
        _env(monkeypatch, QUEUE_WATCH_DRY_RUN=typo)
        try:
            assert Settings.from_env().queue_watch_dry_run is True, \
                f"{typo!r} produced an ARMED watcher"
        except ValueError:
            pass    # refusing to start is the other acceptable answer


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_falls_back_to_the_default(monkeypatch, blank):
    """`FOO=` in .env reads as 'unset', matching compose's `${FOO:-default}`."""
    _env(monkeypatch, QUEUE_WATCH_DRY_RUN=blank)
    assert Settings.from_env().queue_watch_dry_run is True


@pytest.mark.parametrize("var,value", [
    ("QUEUE_WATCH_INTERVAL_MIN", "0"),    # sleep(0) -> hot loop hammering both *arr
    ("QUEUE_WATCH_INTERVAL_MIN", "-5"),   # negative -> sleep() raises -> thread dies
    ("QUEUE_WATCH_MAX_PER_CYCLE", "0"),   # cap 0 -> watcher permanently inert
    ("QUEUE_WATCH_MAX_PER_CYCLE", "-1"),
    ("QUEUE_WATCH_MIN_AGE_MIN", "-1"),
])
def test_nonsensical_numeric_knobs_are_rejected(monkeypatch, var, value):
    """Only the margin was validated. The rest could silently produce a dead thread,
    an inert watcher, or a busy loop -- all of which look healthy from outside."""
    _env(monkeypatch, **{var: value})
    with pytest.raises(ValueError, match=var):
        Settings.from_env()


def test_min_age_zero_is_allowed(monkeypatch):
    """Distinct from the rejections above: 0 means 'act on first sighting', which is
    aggressive but coherent, and is how gate B already behaves."""
    _env(monkeypatch, QUEUE_WATCH_MIN_AGE_MIN="0")
    assert Settings.from_env().queue_watch_min_age_min == 0
