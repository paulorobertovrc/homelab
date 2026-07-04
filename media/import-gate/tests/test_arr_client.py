import pytest
from arr_client import ArrClient


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.get((method, url), FakeResp())


def _client(session):
    c = ArrClient("http://radarr:7878", "KEY", "radarr")
    c._session = session
    return c


def test_get_movie_hits_correct_url():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/movie/75")] = FakeResp(200, {"id": 75})
    assert _client(s).get_movie(75) == {"id": 75}
    assert s.calls[0][2]["headers"]["X-Api-Key"] == "KEY"


def test_delete_moviefile():
    s = FakeSession()
    _client(s).delete_moviefile(79)
    assert s.calls[0][0] == "DELETE"
    assert s.calls[0][1] == "http://radarr:7878/api/v3/moviefile/79"


def test_find_grab_history_id_filters_grabbed():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/history")] = FakeResp(200, {
        "records": [
            {"id": 9, "eventType": "downloadFolderImported", "downloadId": "ABC"},
            {"id": 6, "eventType": "grabbed", "downloadId": "ABC"},
        ]
    })
    assert _client(s).find_grab_history_id("ABC") == 6


def test_find_grab_history_id_none_when_absent():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/history")] = FakeResp(200, {"records": []})
    assert _client(s).find_grab_history_id("ZZZ") is None


def test_mark_failed():
    s = FakeSession()
    _client(s).mark_failed(6)
    assert s.calls[0][0] == "POST"
    assert s.calls[0][1] == "http://radarr:7878/api/v3/history/failed/6"
