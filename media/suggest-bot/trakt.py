"""Cliente Trakt v2 — só trending, auth por client id (sem OAuth)."""
import requests

_BASE = "https://api.trakt.tv"


class TraktClient:
    def __init__(self, client_id: str, timeout: int = 15):
        self._headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
            "User-Agent": "suggest-bot/1.0",
        }
        self._timeout = timeout
        self._session = requests.Session()

    def trending_tmdb_ids(self, limit: int = 40) -> set[tuple[str, int]]:
        """{(media_type, tmdb_id)} dos trendings; itens sem ids.tmdb são ignorados."""
        out: set[tuple[str, int]] = set()
        for kind, key, media_type in (("movies", "movie", "movie"), ("shows", "show", "tv")):
            r = self._session.request(
                "GET", f"{_BASE}/{kind}/trending",
                headers=self._headers, params={"limit": limit}, timeout=self._timeout,
            )
            r.raise_for_status()
            for item in r.json():
                tmdb = ((item.get(key) or {}).get("ids") or {}).get("tmdb")
                if tmdb:
                    out.add((media_type, tmdb))
        return out
