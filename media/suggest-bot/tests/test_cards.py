import pytest
from cards import callback_add, callback_dismiss, caption, parse_callback, poster_url
from pipeline import Suggestion


def _s(**kw):
    base = dict(media_type="movie", tmdb_id=550, title="Fight Club", year="1999",
                overview="Um cara cansado.", poster_path="/p.jpg", source="trending",
                tmdb_score=8.4, in_trakt=True, ratings={"imdb": 8.8, "tomatoes": 79})
    base.update(kw)
    return Suggestion(**base)


def test_poster_url():
    assert poster_url(_s()) == "https://image.tmdb.org/t/p/w500/p.jpg"
    assert poster_url(_s(poster_path=None)) is None


def test_caption_has_title_ratings_and_escapes_html():
    c = caption(_s(title="Tom & Jerry <3"))
    assert "Tom &amp; Jerry &lt;3" in c and "(1999)" in c
    assert "TMDB 8.4" in c and "IMDb 8.8" in c and "🍅 79%" in c and "🔥" in c


def test_caption_watchlist_badge_and_missing_ratings():
    c = caption(_s(source="watchlist", ratings={}, tmdb_score=None))
    assert "👁" in c and "IMDb" not in c


def test_caption_truncates_overview():
    c = caption(_s(overview="x" * 500))
    assert "x" * 400 + "…" in c and "x" * 401 not in c


def test_callback_roundtrip():
    s = _s(media_type="tv", tmdb_id=1399)
    assert parse_callback(callback_add(s)) == ("add", "tv", 1399)
    assert parse_callback(callback_dismiss(s)) == ("dis", "tv", 1399)
    assert len(callback_add(s).encode()) <= 64


@pytest.mark.parametrize("bad", ["", "add:movie", "zap:movie:1", "add:song:1", "add:movie:x"])
def test_parse_callback_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_callback(bad)
