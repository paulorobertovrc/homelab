"""Minimal Sonarr/Radarr v3 API wrapper for the self-heal steps."""
import requests


class ArrClient:
    def __init__(self, base_url: str, api_key: str, kind: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.kind = kind
        self._session = requests.Session()

    def _req(self, method, path, **kw):
        headers = kw.pop("headers", {})
        headers["X-Api-Key"] = self.api_key
        resp = self._session.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=30, **kw
        )
        resp.raise_for_status()
        return resp

    def get_movie(self, movie_id: int) -> dict:
        return self._req("GET", f"/api/v3/movie/{movie_id}").json()

    def get_series(self, series_id: int) -> dict:
        return self._req("GET", f"/api/v3/series/{series_id}").json()

    def delete_moviefile(self, file_id: int) -> None:
        self._req("DELETE", f"/api/v3/moviefile/{file_id}")

    def delete_episodefile(self, file_id: int) -> None:
        self._req("DELETE", f"/api/v3/episodefile/{file_id}")

    def find_grab_history_id(self, download_id: str) -> int | None:
        records = self._req(
            "GET", "/api/v3/history",
            params={"downloadId": download_id, "pageSize": 50},
        ).json().get("records", [])
        for r in records:
            if r.get("eventType") == "grabbed" and r.get("downloadId") == download_id:
                return r.get("id")
        return None

    def mark_failed(self, history_id: int) -> None:
        self._req("POST", f"/api/v3/history/failed/{history_id}")
