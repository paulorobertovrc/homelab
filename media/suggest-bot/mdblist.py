"""Cliente MDBList — notas IMDb/RT por TMDB id. Best-effort: falha vira dict vazio."""
import logging

import requests

log = logging.getLogger(__name__)
_BASE = "https://api.mdblist.com"
_SOURCES = ("imdb", "tomatoes")  # value: imdb 0-10, tomatoes 0-100


class MdblistClient:
    def __init__(self, api_key: str, timeout: int = 15):
        self._key = api_key
        self._timeout = timeout
        self._session = requests.Session()

    def ratings(self, media_type: str, tmdb_id: int) -> dict:
        mtype = "movie" if media_type == "movie" else "show"
        try:
            r = self._session.request(
                "GET", f"{_BASE}/tmdb/{mtype}/{tmdb_id}/",
                params={"apikey": self._key}, timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("mdblist falhou para %s/%s: %s", media_type, tmdb_id, exc)
            return {}
        return {
            rt["source"]: rt["value"]
            for rt in data.get("ratings", [])
            if rt.get("source") in _SOURCES and rt.get("value") is not None
        }
