import os
from state import SuggestionStore, SUGGESTED, REQUESTED, DISMISSED


def _store(tmp_path):
    return SuggestionStore(os.path.join(tmp_path, "s.db"))


def test_unknown_is_none(tmp_path):
    assert _store(tmp_path).status("movie", 550) is None


def test_mark_and_read(tmp_path):
    s = _store(tmp_path)
    s.mark("movie", 550, SUGGESTED, "2026-07-14T10:00:00-04:00")
    assert s.status("movie", 550) == SUGGESTED


def test_mark_upgrades_status(tmp_path):
    s = _store(tmp_path)
    s.mark("tv", 1399, SUGGESTED, "2026-07-14T10:00:00-04:00")
    s.mark("tv", 1399, REQUESTED, "2026-07-14T11:00:00-04:00")
    assert s.status("tv", 1399) == REQUESTED


def test_types_are_independent(tmp_path):
    s = _store(tmp_path)
    s.mark("movie", 100, DISMISSED, "2026-07-14T10:00:00-04:00")
    assert s.status("tv", 100) is None


def test_last_digest_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.last_digest_at() is None
    s.set_last_digest_at("2026-07-14T18:00:00-04:00")
    assert s.last_digest_at() == "2026-07-14T18:00:00-04:00"


def test_persists_across_instances(tmp_path):
    db = os.path.join(tmp_path, "s.db")
    SuggestionStore(db).mark("movie", 550, SUGGESTED, "2026-07-14T10:00:00-04:00")
    assert SuggestionStore(db).status("movie", 550) == SUGGESTED
