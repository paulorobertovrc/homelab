import os
from state import AttemptStore


def test_increment_starts_at_one(tmp_path):
    store = AttemptStore(os.path.join(tmp_path, "s.db"))
    assert store.increment("radarr:75") == 1


def test_increment_accumulates(tmp_path):
    store = AttemptStore(os.path.join(tmp_path, "s.db"))
    store.increment("radarr:75")
    assert store.increment("radarr:75") == 2


def test_get_unknown_is_zero(tmp_path):
    store = AttemptStore(os.path.join(tmp_path, "s.db"))
    assert store.get("radarr:99") == 0


def test_reset_clears(tmp_path):
    store = AttemptStore(os.path.join(tmp_path, "s.db"))
    store.increment("radarr:75")
    store.reset("radarr:75")
    assert store.get("radarr:75") == 0


def test_persists_across_instances(tmp_path):
    db = os.path.join(tmp_path, "s.db")
    AttemptStore(db).increment("radarr:75")
    assert AttemptStore(db).get("radarr:75") == 1
