import pytest
from jellyseerr import JellyseerrClient, AlreadyRequested
from conftest import FakeResp, FakeSession


def _client(session):
    c = JellyseerrClient("http://jellyseerr:5055", "KEY")
    c._session = session
    return c


TRENDING = {
    "page": 1, "totalPages": 1,
    "results": [
        {"id": 550, "mediaType": "movie", "title": "Fight Club", "overview": "o",
         "posterPath": "/p.jpg", "voteAverage": 8.4, "releaseDate": "1999-10-15", "adult": False},
        {"id": 1399, "mediaType": "tv", "name": "GoT", "overview": "x",
         "posterPath": "/g.jpg", "voteAverage": 8.3, "firstAirDate": "2011-04-17",
         "mediaInfo": {"status": 5}},
        {"id": 7, "mediaType": "person", "name": "Someone"},
    ],
}


def test_trending_normalizes_and_drops_people():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/discover/trending")] = FakeResp(200, TRENDING)
    items = _client(s).trending()
    assert [i["tmdb_id"] for i in items] == [550, 1399]
    movie, tv = items
    assert movie["title"] == "Fight Club" and movie["year"] == "1999" and movie["taken"] is False
    assert tv["title"] == "GoT" and tv["media_type"] == "tv" and tv["taken"] is True
    assert s.calls[0][2]["headers"]["X-Api-Key"] == "KEY"


def test_trending_sends_only_supported_params():
    # Regressão: Jellyseerr 2.7.3 valida a query estritamente e responde
    # 400 "Unknown query parameter 'timeWindow'" — o endpoint só aceita
    # page/language (fatos de API verificados no plano). Visto ao vivo 2026-07-23.
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/discover/trending")] = FakeResp(200, TRENDING)
    _client(s).trending()
    assert s.calls[0][2]["params"] == {"page": 1, "language": "pt-BR"}


def test_watchlist_paginates():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/discover/watchlist")] = FakeResp(200, {
        "page": 1, "totalPages": 1, "totalResults": 1,
        "results": [{"tmdbId": 603, "mediaType": "movie", "title": "The Matrix"}],
    })
    items = _client(s).watchlist()
    assert items == [{"media_type": "movie", "tmdb_id": 603, "title": "The Matrix"}]


def test_detail_movie_taken_flag():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/movie/603")] = FakeResp(200, {
        "title": "The Matrix", "releaseDate": "1999-03-31", "overview": "neo",
        "posterPath": "/m.jpg", "voteAverage": 8.2, "mediaInfo": {"status": 3},
    })
    d = _client(s).detail("movie", 603)
    assert d["taken"] is True and d["year"] == "1999" and d["media_type"] == "movie"


def test_request_movie_body():
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(201, {"id": 1})
    _client(s).request("movie", 550)
    assert s.calls[0][2]["json"] == {"mediaType": "movie", "mediaId": 550}


def test_request_tv_asks_all_seasons():
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(201, {"id": 2})
    _client(s).request("tv", 1399)
    assert s.calls[0][2]["json"] == {"mediaType": "tv", "mediaId": 1399, "seasons": "all"}


@pytest.mark.parametrize("code", [409, 202])
def test_request_duplicate_and_noseasons_raise_already(code):
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(code, {"message": "dup"})
    with pytest.raises(AlreadyRequested):
        _client(s).request("movie", 550)


class _NonJsonResp(FakeResp):
    def json(self):
        raise ValueError("corpo não é JSON")


def test_request_duplicate_with_nonjson_body_still_raises_already():
    # Regressão: um 409/202 com corpo não-parseável não pode mascarar o
    # AlreadyRequested atrás de um ValueError não tratado.
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = _NonJsonResp(409)
    with pytest.raises(AlreadyRequested, match="409"):
        _client(s).request("movie", 550)
