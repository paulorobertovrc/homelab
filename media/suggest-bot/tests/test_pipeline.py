import sqlite3
from types import SimpleNamespace

from pipeline import build_digest
from state import SuggestionStore, SUGGESTED, DISMISSED

CFG = SimpleNamespace(digest_size=3, min_imdb=6.5, trending_pages=1)
NOW = "2026-07-14T18:00:00-04:00"


def _t(tmdb_id, title, score=7.0, adult=False, taken=False, media_type="movie"):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": "2026",
            "overview": "o", "poster_path": "/p.jpg", "tmdb_score": score,
            "adult": adult, "taken": taken}


class FakeJelly:
    def __init__(self, watchlist=(), trending=(), details=None):
        self._w, self._t, self._d = list(watchlist), list(trending), details or {}

    def watchlist(self):
        return self._w

    def trending(self, pages=1):
        return self._t

    def detail(self, media_type, tmdb_id):
        return self._d[(media_type, tmdb_id)]


class FakeTrakt:
    def __init__(self, ids=frozenset(), boom=False):
        self._ids, self._boom = set(ids), boom

    def trending_tmdb_ids(self, limit=40):
        if self._boom:
            raise RuntimeError("trakt down")
        return self._ids


class FakeMdb:
    def __init__(self, table=None):
        self._table = table or {}
        self.calls = 0

    def ratings(self, media_type, tmdb_id):
        self.calls += 1
        return self._table.get((media_type, tmdb_id), {})


def _store(tmp_path):
    return SuggestionStore(str(tmp_path / "s.db"))


def test_watchlist_first_and_skips_taken(tmp_path):
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"},
                   {"media_type": "movie", "tmdb_id": 604, "title": "Owned"}],
        trending=[_t(550, "Fight Club", score=8.4)],
        details={("movie", 603): _t(603, "Matrix", taken=False),
                 ("movie", 604): _t(604, "Owned", taken=True)},
    )
    got, notes = build_digest(jelly, FakeTrakt(), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert [(s.source, s.tmdb_id) for s in got] == [("watchlist", 603), ("trending", 550)]
    assert notes == []


def test_watchlist_ignores_imdb_floor_but_trending_respects_it(tmp_path):
    mdb = FakeMdb({("movie", 603): {"imdb": 4.0}, ("movie", 550): {"imdb": 5.0},
                   ("movie", 551): {"imdb": 8.0}})
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"}],
        trending=[_t(550, "Low"), _t(551, "High")],
        details={("movie", 603): _t(603, "Matrix")},
    )
    got, _ = build_digest(jelly, FakeTrakt(), mdb, _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [603, 551]


def test_trakt_boost_wins_over_score(tmp_path):
    jelly = FakeJelly(trending=[_t(550, "HighScore", score=9.0), _t(551, "InTrakt", score=7.0)])
    got, _ = build_digest(jelly, FakeTrakt({("movie", 551)}), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert got[0].tmdb_id == 551 and got[0].in_trakt is True


def test_filters_adult_taken_and_already_seen(tmp_path):
    store = _store(tmp_path)
    store.mark("movie", 552, DISMISSED, NOW)
    jelly = FakeJelly(trending=[_t(550, "Ok"), _t(551, "Adult", adult=True),
                                _t(552, "Seen"), _t(553, "Taken", taken=True)])
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert [s.tmdb_id for s in got] == [550]


def test_marks_final_picks_as_suggested(tmp_path):
    store = _store(tmp_path)
    jelly = FakeJelly(trending=[_t(550, "A")])
    build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert store.status("movie", 550) == SUGGESTED
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert got == []


def test_trakt_down_degrades_with_note(tmp_path):
    jelly = FakeJelly(trending=[_t(550, "A")])
    got, notes = build_digest(jelly, FakeTrakt(boom=True), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [550]
    assert len(notes) == 1 and "Trakt" in notes[0]


def test_cut_to_digest_size(tmp_path):
    jelly = FakeJelly(trending=[_t(500 + i, f"T{i}") for i in range(10)])
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert len(got) == CFG.digest_size


def test_watchlist_also_in_trending_dedupes_to_watchlist(tmp_path):
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"}],
        trending=[_t(603, "Matrix", score=9.0)],
        details={("movie", 603): _t(603, "Matrix")},
    )
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert [(s.source, s.tmdb_id) for s in got] == [("watchlist", 603)]


def test_watchlist_respects_store_dedup(tmp_path):
    store = _store(tmp_path)
    store.mark("movie", 603, DISMISSED, NOW)
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"}],
        details={("movie", 603): _t(603, "Matrix")},
    )
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert got == []


def test_ratings_enrichment_populates_suggestions(tmp_path):
    mdb = FakeMdb({("movie", 603): {"imdb": 7.5, "rt": 90}, ("movie", 551): {"imdb": 8.0, "rt": 95}})
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"}],
        trending=[_t(551, "High")],
        details={("movie", 603): _t(603, "Matrix")},
    )
    got, _ = build_digest(jelly, FakeTrakt(), mdb, _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [603, 551]
    assert got[0].ratings == {"imdb": 7.5, "rt": 90}
    assert got[1].ratings == {"imdb": 8.0, "rt": 95}


def test_shortlist_limits_mdblist_calls(tmp_path):
    mdb = FakeMdb()
    jelly = FakeJelly(trending=[_t(500 + i, f"T{i}") for i in range(10)])
    build_digest(jelly, FakeTrakt(), mdb, _store(tmp_path), CFG, NOW)
    assert mdb.calls == CFG.digest_size * 3  # 0 watchlist picks -> slots=3, shortlist=9


def test_final_rank_uses_imdb_between_trakt_ties(tmp_path):
    mdb = FakeMdb({("movie", 550): {"imdb": 9.0}, ("movie", 551): {"imdb": 7.0}})
    jelly = FakeJelly(trending=[_t(550, "LowScoreHighImdb", score=6.0),
                                _t(551, "HighScoreLowImdb", score=9.0)])
    got, _ = build_digest(jelly, FakeTrakt(), mdb, _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [550, 551]


def test_mark_records_now_iso(tmp_path):
    store = _store(tmp_path)
    jelly = FakeJelly(trending=[_t(550, "A")])
    build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    row = sqlite3.connect(str(tmp_path / "s.db")).execute(
        "SELECT status, updated_at FROM suggestions WHERE media_type = ? AND tmdb_id = ?",
        ("movie", 550),
    ).fetchone()
    assert row == (SUGGESTED, NOW)
