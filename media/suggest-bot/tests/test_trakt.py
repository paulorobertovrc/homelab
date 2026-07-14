from conftest import FakeResp, FakeSession
from trakt import TraktClient


def test_trending_merges_movies_and_shows_and_skips_null_tmdb():
    s = FakeSession()
    s.responses[("GET", "https://api.trakt.tv/movies/trending")] = FakeResp(200, [
        {"watchers": 100, "movie": {"title": "A", "year": 2026, "ids": {"trakt": 1, "tmdb": 550}}},
    ])
    s.responses[("GET", "https://api.trakt.tv/shows/trending")] = FakeResp(200, [
        {"watchers": 90, "show": {"title": "B", "year": 2026, "ids": {"trakt": 2, "tmdb": 1399}}},
        {"watchers": 80, "show": {"title": "C", "year": 2026, "ids": {"trakt": 3, "tmdb": None}}},
    ])
    c = TraktClient("CID")
    c._session = s
    assert c.trending_tmdb_ids() == {("movie", 550), ("tv", 1399)}
    headers = s.calls[0][2]["headers"]
    assert headers["trakt-api-key"] == "CID" and headers["trakt-api-version"] == "2"
    assert s.calls[0][2]["params"] == {"limit": 40}
