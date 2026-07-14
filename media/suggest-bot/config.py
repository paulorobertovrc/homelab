"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: int
    jellyseerr_url: str
    jellyseerr_key: str
    trakt_client_id: str
    mdblist_key: str
    state_dir: str
    ntfy_url: str
    digest_size: int
    min_imdb: float
    digest_weekday: int  # 0=segunda … 6=domingo (convenção datetime.weekday())
    digest_hour: int
    catchup_grace_days: int
    trending_pages: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=int(os.environ["TELEGRAM_CHAT_ID"]),
            jellyseerr_url=os.environ.get("JELLYSEERR_URL", "http://jellyseerr:5055"),
            jellyseerr_key=os.environ["JELLYSEERR_API_KEY"],
            trakt_client_id=os.environ["TRAKT_CLIENT_ID"],
            mdblist_key=os.environ["MDBLIST_API_KEY"],
            state_dir=os.environ.get("STATE_DIR", "/config"),
            ntfy_url=os.environ.get("NTFY_URL", "http://ntfy:80/arr-media"),
            digest_size=int(os.environ.get("DIGEST_SIZE", "5")),
            min_imdb=float(os.environ.get("MIN_IMDB", "6.5")),
            digest_weekday=int(os.environ.get("DIGEST_WEEKDAY", "4")),  # sexta
            digest_hour=int(os.environ.get("DIGEST_HOUR", "18")),
            catchup_grace_days=int(os.environ.get("CATCHUP_GRACE_DAYS", "3")),
            trending_pages=int(os.environ.get("TRENDING_PAGES", "2")),
        )
