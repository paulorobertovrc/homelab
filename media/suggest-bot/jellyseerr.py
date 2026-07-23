"""Cliente da API do Jellyseerr (X-Api-Key). Único caminho de escrita do bot (/request)."""
import requests

# MediaInfo.status (seerr-api.yml): 1=UNKNOWN 2=PENDING 3=PROCESSING
# 4=PARTIALLY_AVAILABLE 5=AVAILABLE 6=DELETED. mediaInfo ausente = fora do Jellyseerr.
_TAKEN = {2, 3, 4, 5}


class JellyseerrError(Exception):
    pass


class AlreadyRequested(JellyseerrError):
    """409 (request duplicado) ou 202 (todas as temporadas já pedidas/disponíveis)."""


def _normalize(media_type: str, tmdb_id: int, raw: dict) -> dict:
    return {
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "title": raw.get("title") or raw.get("name") or "?",
        "year": (raw.get("releaseDate") or raw.get("firstAirDate") or "")[:4],
        "overview": raw.get("overview") or "",
        "poster_path": raw.get("posterPath"),
        "tmdb_score": raw.get("voteAverage"),
        "adult": bool(raw.get("adult", False)),
        "taken": (raw.get("mediaInfo") or {}).get("status") in _TAKEN,
    }


class JellyseerrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._timeout = timeout
        self._session = requests.Session()

    def _call(self, method: str, path: str, **kw):
        return self._session.request(
            method, f"{self._base}/api/v1{path}",
            headers=self._headers, timeout=self._timeout, **kw,
        )

    def _get_json(self, path: str, **params) -> dict:
        r = self._call("GET", path, params=params or None)
        r.raise_for_status()
        return r.json()

    def trending(self, pages: int = 1) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            # Só page/language: Jellyseerr 2.7.3 rejeita params desconhecidos com 400
            # ("Unknown query parameter 'timeWindow'"). A janela semanal do spec é
            # inatingível aqui — o proxy TMDB do Jellyseerr usa a janela própria dele.
            data = self._get_json("/discover/trending", page=page, language="pt-BR")
            for item in data.get("results", []):
                if item.get("mediaType") not in ("movie", "tv"):
                    continue
                out.append(_normalize(item["mediaType"], item["id"], item))
        return out

    def watchlist(self) -> list[dict]:
        out, page, total = [], 1, 1
        while page <= total:
            data = self._get_json("/discover/watchlist", page=page)
            total = data.get("totalPages", 1)
            for item in data.get("results", []):
                out.append({
                    "media_type": item["mediaType"],
                    "tmdb_id": item["tmdbId"],
                    "title": item.get("title", "?"),
                })
            page += 1
        return out

    def detail(self, media_type: str, tmdb_id: int) -> dict:
        path = f"/movie/{tmdb_id}" if media_type == "movie" else f"/tv/{tmdb_id}"
        return _normalize(media_type, tmdb_id, self._get_json(path))

    def request(self, media_type: str, tmdb_id: int) -> None:
        body = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            body["seasons"] = "all"  # string literal — pede todas menos specials
        r = self._call("POST", "/request", json=body)
        if r.status_code in (409, 202):  # 202 = NoSeasonsAvailableError (não é erro HTTP)
            try:
                message = (r.json() or {}).get("message")
            except ValueError:  # corpo não-JSON não pode mascarar o AlreadyRequested
                message = None
            raise AlreadyRequested(message or f"HTTP {r.status_code}")
        r.raise_for_status()
