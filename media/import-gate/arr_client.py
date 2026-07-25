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

    QUEUE_PAGE_SIZE = 200
    QUEUE_MAX_PAGES = 50

    def get_queue(self, include_episode: bool = False) -> list:
        """Every queue record, following pagination to the end.

        Partial views are unsafe here: a season pack is N records sharing a downloadId, so
        a page boundary inside a pack would leave the pre-air gate judging a subset of the
        group. Erroring out instead of paging would silently disable the watcher whenever a
        big season is queued, so it pages.
        """
        records = []
        for page in range(1, self.QUEUE_MAX_PAGES + 1):
            params = {"page": page, "pageSize": self.QUEUE_PAGE_SIZE}
            if include_episode:
                # Sonarr only: brings EpisodeResource (with airDateUtc) inline,
                # which the pre-air gate needs.
                params["includeEpisode"] = "true"
            payload = self._req("GET", "/api/v3/queue", params=params).json()
            batch = payload.get("records", [])
            records += batch
            # `not batch` also guards against a server reporting a total it never serves.
            if not batch or len(records) >= payload.get("totalRecords", len(records)):
                return records
        raise RuntimeError(
            f"queue exceeded {self.QUEUE_MAX_PAGES} pages; refusing to act on a partial view"
        )

    def delete_queue_item(self, queue_id: int, blocklist: bool = True,
                          skip_redownload: bool = False) -> None:
        # blocklist=true is what stops the *arr from re-grabbing the same release
        # on the next RSS pass; without it this would loop.
        self._req("DELETE", f"/api/v3/queue/{queue_id}", params={
            "removeFromClient": "true",
            "blocklist": "true" if blocklist else "false",
            "skipRedownload": "true" if skip_redownload else "false",
            "changeCategory": "false",
        })
