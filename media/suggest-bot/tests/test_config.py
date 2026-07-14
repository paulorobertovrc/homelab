from config import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("JELLYSEERR_API_KEY", "jk")
    monkeypatch.setenv("TRAKT_CLIENT_ID", "tc")
    monkeypatch.setenv("MDBLIST_API_KEY", "mk")


def test_required_and_defaults(monkeypatch):
    _base_env(monkeypatch)
    s = Settings.from_env()
    assert s.telegram_chat_id == 123
    assert s.jellyseerr_url == "http://jellyseerr:5055"
    assert s.digest_size == 5
    assert s.min_imdb == 6.5
    assert s.digest_weekday == 4 and s.digest_hour == 18
    assert s.catchup_grace_days == 3 and s.trending_pages == 2


def test_overrides(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("DIGEST_SIZE", "8")
    monkeypatch.setenv("MIN_IMDB", "7.0")
    s = Settings.from_env()
    assert s.digest_size == 8 and s.min_imdb == 7.0
