import requests
from conftest import FakeResp, FakeSession
from mdblist import MdblistClient


def _client(session):
    c = MdblistClient("MK")
    c._session = session
    return c


def test_ratings_extracts_imdb_and_tomatoes():
    s = FakeSession()
    s.responses[("GET", "https://api.mdblist.com/tmdb/movie/578/")] = FakeResp(200, {
        "title": "Jaws",
        "ratings": [
            {"source": "imdb", "value": 8.1, "score": 81},
            {"source": "tomatoes", "value": 97, "score": 97},
            {"source": "metacritic", "value": 87, "score": 87},
            {"source": "letterboxd", "value": None, "score": None},
        ],
    })
    assert _client(s).ratings("movie", 578) == {"imdb": 8.1, "tomatoes": 97}
    assert s.calls[0][2]["params"] == {"apikey": "MK"}


def test_tv_uses_show_path():
    s = FakeSession()
    s.responses[("GET", "https://api.mdblist.com/tmdb/show/1399/")] = FakeResp(200, {"ratings": []})
    assert _client(s).ratings("tv", 1399) == {}


def test_http_error_returns_empty():
    s = FakeSession()
    s.responses[("GET", "https://api.mdblist.com/tmdb/movie/1/")] = FakeResp(500)
    assert _client(s).ratings("movie", 1) == {}
