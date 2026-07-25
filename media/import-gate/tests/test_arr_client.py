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


def test_get_queue_returns_records():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/queue")] = FakeResp(200, {
        "records": [{"id": 1, "downloadId": "ABC"}], "totalRecords": 1
    })
    assert _client(s).get_queue() == [{"id": 1, "downloadId": "ABC"}]
    assert s.calls[0][2]["params"]["pageSize"] == 200


def test_get_queue_empty_when_key_absent():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/queue")] = FakeResp(200, {})
    assert _client(s).get_queue() == []


def test_get_queue_requests_episode_when_asked():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/queue")] = FakeResp(200, {"records": []})
    _client(s).get_queue(include_episode=True)
    assert s.calls[0][2]["params"]["includeEpisode"] == "true"


def test_get_queue_omits_episode_by_default():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/queue")] = FakeResp(200, {"records": []})
    _client(s).get_queue()
    assert "includeEpisode" not in s.calls[0][2]["params"]


class PagingSession(FakeSession):
    """Serves totalRecords across several pages, one page per call."""

    def __init__(self, pages, total):
        super().__init__()
        self.pages = pages
        self.total = total

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        page = kw["params"]["page"]
        return FakeResp(200, {"records": self.pages[page - 1], "totalRecords": self.total})


def test_get_queue_follows_every_page():
    """A season pack split across a page boundary would otherwise be judged partial."""
    s = PagingSession([[{"id": 1}, {"id": 2}], [{"id": 3}]], total=3)
    assert [r["id"] for r in _client(s).get_queue()] == [1, 2, 3]
    assert [c[2]["params"]["page"] for c in s.calls] == [1, 2]


def test_get_queue_stops_on_an_empty_page():
    """Guards against a server that reports a total it never delivers."""
    s = PagingSession([[{"id": 1}], []], total=99)
    assert [r["id"] for r in _client(s).get_queue()] == [1]
    assert len(s.calls) == 2


def test_delete_queue_item_sends_expected_params():
    s = FakeSession()
    _client(s).delete_queue_item(590404725)
    method, url, kw = s.calls[0]
    assert method == "DELETE"
    assert url == "http://radarr:7878/api/v3/queue/590404725"
    assert kw["params"] == {
        "removeFromClient": "true",
        "blocklist": "true",
        "skipRedownload": "false",
        "changeCategory": "false",
    }


def test_delete_queue_item_can_skip_blocklist():
    s = FakeSession()
    _client(s).delete_queue_item(42, blocklist=False)
    assert s.calls[0][2]["params"]["blocklist"] == "false"


def test_delete_queue_item_can_skip_redownload():
    s = FakeSession()
    _client(s).delete_queue_item(42, skip_redownload=True)
    assert s.calls[0][2]["params"]["skipRedownload"] == "true"


def test_get_queue_refuses_to_return_a_partial_view():
    """The last line of defence on the never-judge-a-partial-group invariant. A server
    that keeps serving records past QUEUE_MAX_PAGES must raise, not quietly hand back a
    truncated queue -- gate B's all() over a subset is what pagination exists to prevent.
    Replacing the raise with `return records` used to pass the whole suite."""
    pages = [[{"id": n}] for n in range(1, 60)]
    s = PagingSession(pages, total=10_000)
    with pytest.raises(RuntimeError, match="partial view"):
        _client(s).get_queue()
