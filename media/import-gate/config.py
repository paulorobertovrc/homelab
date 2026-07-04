"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


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

    @classmethod
    def from_env(cls) -> "Settings":
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
        )
