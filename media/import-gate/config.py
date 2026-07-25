"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


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
            queue_watch_interval_min=int(os.environ.get("QUEUE_WATCH_INTERVAL_MIN", "10")),
            queue_watch_min_age_min=int(os.environ.get("QUEUE_WATCH_MIN_AGE_MIN", "15")),
            queue_watch_max_per_cycle=int(os.environ.get("QUEUE_WATCH_MAX_PER_CYCLE", "3")),
            queue_watch_preair_enabled=_env_bool("QUEUE_WATCH_PREAIR_ENABLED", "true"),
            queue_watch_preair_margin_h=preair_margin,
            # Ships simulating. Arming is a deliberate act, not a side effect of deploying.
            queue_watch_dry_run=_env_bool("QUEUE_WATCH_DRY_RUN", "true"),
        )
