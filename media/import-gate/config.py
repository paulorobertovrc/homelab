"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _env_bool(name: str, default: str) -> bool:
    """Strict boolean: an unrecognised token is an error, never a silent guess.

    The permissive version treated anything outside the true-list as False. That is
    harmless for an on/off flag, but QUEUE_WATCH_DRY_RUN inverts the stakes -- there,
    False means ARMED, so `ture`, `sim`, `y` or a stray quote silently armed a
    watcher that deletes torrents and their data. Refusing to guess costs a restart
    and a one-character fix; guessing wrong costs downloads.

    Blank reads as unset, matching compose's `${FOO:-default}`.
    """
    raw = os.environ.get(name, default).strip()
    if not raw:
        raw = default.strip()
    token = raw.lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    raise ValueError(
        f"{name} must be one of {_TRUE + _FALSE} (got {raw!r}). "
        "Refusing to guess: for QUEUE_WATCH_DRY_RUN a wrong guess is the "
        "difference between simulating and deleting."
    )


def _env_int(name: str, default: str, minimum: int) -> int:
    """Integer knob with a floor, so a nonsensical value fails at startup.

    Only the pre-air margin used to be validated. The rest could each produce a
    failure that looks healthy from outside: INTERVAL_MIN=0 is a hot loop hammering
    both *arr APIs, a negative interval makes sleep() raise and kills the daemon
    thread while the container stays healthy, and MAX_PER_CYCLE<1 leaves the watcher
    permanently inert.
    """
    value = int(os.environ.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value}).")
    return value


@dataclass(frozen=True)
class Settings:
    radarr_url: str
    radarr_key: str
    sonarr_url: str
    sonarr_key: str
    library_root: str
    quarantine_root: str
    state_dir: str
    ntfy_url: str
    whisper_model: str
    lang_prob_threshold: float
    max_attempts: int
    sample_windows: int
    sample_seconds: int
    skip_intro_fraction: float
    queue_watch_enabled: bool
    queue_watch_interval_min: int
    queue_watch_min_age_min: int
    queue_watch_max_per_cycle: int
    queue_watch_preair_enabled: bool
    queue_watch_preair_margin_h: int
    queue_watch_dry_run: bool

    @classmethod
    def from_env(cls) -> "Settings":
        preair_margin = int(os.environ.get("QUEUE_WATCH_PREAIR_MARGIN_H", "24"))
        if preair_margin < 1:
            raise ValueError(
                f"QUEUE_WATCH_PREAIR_MARGIN_H must be >= 1 (got {preair_margin}). "
                "To turn the pre-air gate off use QUEUE_WATCH_PREAIR_ENABLED=false; "
                "a zero margin would blocklist legitimate releases, which routinely "
                "appear a couple of hours before airDateUtc."
            )
        # Floors, not style: see _env_int. min age may be 0 ("act on first sighting"),
        # which is coherent and is already how gate B behaves.
        interval_min = _env_int("QUEUE_WATCH_INTERVAL_MIN", "10", minimum=1)
        min_age_min = _env_int("QUEUE_WATCH_MIN_AGE_MIN", "15", minimum=0)
        max_per_cycle = _env_int("QUEUE_WATCH_MAX_PER_CYCLE", "3", minimum=1)
        return cls(
            radarr_url=os.environ.get("RADARR_URL", "http://172.39.0.4:7878"),
            radarr_key=os.environ["RADARR_API_KEY"],
            sonarr_url=os.environ.get("SONARR_URL", "http://172.39.0.3:8989"),
            sonarr_key=os.environ["SONARR_API_KEY"],
            library_root=os.environ.get("LIBRARY_ROOT", "/data/media"),
            quarantine_root=os.environ.get("QUARANTINE_ROOT", "/data/quarantine"),
            state_dir=os.environ.get("STATE_DIR", "/config"),
            ntfy_url=os.environ.get("NTFY_URL", "http://ntfy:80/arr-media"),
            whisper_model=os.environ.get("WHISPER_MODEL", "small"),
            lang_prob_threshold=float(os.environ.get("LANG_PROB_THRESHOLD", "0.7")),
            max_attempts=int(os.environ.get("MAX_ATTEMPTS", "3")),
            sample_windows=int(os.environ.get("SAMPLE_WINDOWS", "3")),
            sample_seconds=int(os.environ.get("SAMPLE_SECONDS", "30")),
            skip_intro_fraction=float(os.environ.get("SKIP_INTRO_FRACTION", "0.1")),
            queue_watch_enabled=_env_bool("QUEUE_WATCH_ENABLED", "true"),
            queue_watch_interval_min=interval_min,
            queue_watch_min_age_min=min_age_min,
            queue_watch_max_per_cycle=max_per_cycle,
            queue_watch_preair_enabled=_env_bool("QUEUE_WATCH_PREAIR_ENABLED", "true"),
            queue_watch_preair_margin_h=preair_margin,
            # Ships simulating. Arming is a deliberate act, not a side effect of deploying.
            queue_watch_dry_run=_env_bool("QUEUE_WATCH_DRY_RUN", "true"),
        )
