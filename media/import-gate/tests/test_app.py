import json
import shutil
import os
import pytest
from types import SimpleNamespace
from app import create_app
from validator import Verdict


class Recorder:
    def __init__(self):
        self.notifications = []
        self.marked_failed = []
        self.deleted = []


@pytest.fixture
def ctx(tmp_path):
    rec = Recorder()
    lib = tmp_path / "media"; lib.mkdir()
    quar = tmp_path / "quar"; quar.mkdir()
    src = lib / "Heat (1995)"; src.mkdir()
    f = src / "Heat.mkv"; f.write_bytes(b"x" * 100)

    settings = SimpleNamespace(
        library_root=str(lib), quarantine_root=str(quar),
        ntfy_url="http://ntfy/arr-media", max_attempts=3,
    )

    class FakeArr:
        def delete_moviefile(self, fid): rec.deleted.append(fid)
        def find_grab_history_id(self, did): return 6
        def mark_failed(self, hid): rec.marked_failed.append(hid)

    from state import AttemptStore
    store = AttemptStore(str(tmp_path / "s.db"))

    def notify_fn(url, title, tags, prio, msg): rec.notifications.append((title, msg))

    app = create_app(settings, FakeArr(), FakeArr(), store,
                     validate_fn=lambda **kw: Verdict(False, "wrong-language", "orig=en, detected=ru"),
                     notify_fn=notify_fn)
    return SimpleNamespace(app=app.test_client(), rec=rec, file=str(f), quar=str(quar))


def _radarr_import(path):
    return {
        "eventType": "Download",
        "movie": {"id": 75, "title": "Heat", "originalLanguage": {"name": "English"}, "runtime": 170},
        "movieFile": {"id": 79, "path": path},
        "isUpgrade": False,
        "downloadId": "ABC",
    }


def test_test_event_returns_200_and_does_nothing(ctx):
    r = ctx.app.post("/webhook", json={"eventType": "Test"})
    assert r.status_code == 200
    assert ctx.rec.notifications == []


def test_reject_quarantines_and_selfheals(ctx):
    r = ctx.app.post("/webhook", json=_radarr_import(ctx.file))
    assert r.status_code == 200
    # file copied into quarantine
    found = []
    for root, _, files in os.walk(ctx.quar):
        found += files
    assert "Heat.mkv" in found
    assert ctx.rec.deleted == [79]
    assert ctx.rec.marked_failed == [6]
    assert len(ctx.rec.notifications) == 1


def test_idempotent_same_download_id(ctx):
    ctx.app.post("/webhook", json=_radarr_import(ctx.file))
    before = len(ctx.rec.marked_failed)
    ctx.app.post("/webhook", json=_radarr_import(ctx.file))
    assert len(ctx.rec.marked_failed) == before  # not acted on twice


def test_loop_guard_stops_after_max(ctx):
    # push attempts to the cap, then one more must NOT mark failed again
    from state import AttemptStore
    for did in ["A", "B", "C"]:
        p = dict(_radarr_import(ctx.file)); p["downloadId"] = did
        # regenerate the source file each time (previous run moved it)
        os.makedirs(os.path.dirname(ctx.file), exist_ok=True)
        open(ctx.file, "wb").write(b"x" * 100)
        ctx.app.post("/webhook", json=p)
    p = dict(_radarr_import(ctx.file)); p["downloadId"] = "D"
    open(ctx.file, "wb").write(b"x" * 100)
    marks_before = len(ctx.rec.marked_failed)
    ctx.app.post("/webhook", json=p)
    # 4th attempt beyond max=3: no additional re-search
    assert len(ctx.rec.marked_failed) == marks_before
    assert any("manual" in m.lower() or "gave up" in m.lower()
               for _, m in ctx.rec.notifications)
