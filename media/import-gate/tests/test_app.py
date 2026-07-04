import json
import logging
import shutil
import os
import subprocess
import pytest
from types import SimpleNamespace
from app import create_app
from validator import Verdict, validate


class Recorder:
    def __init__(self):
        self.notifications = []
        self.marked_failed = []
        self.deleted = []
        self.deleted_episode = []


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
        def delete_episodefile(self, fid): rec.deleted_episode.append(fid)
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


def _sonarr_import(path):
    return {
        "eventType": "Download",
        "series": {"id": 42, "title": "The Wire", "originalLanguage": {"name": "English"}},
        "episodeFile": {"id": 91, "path": path},
        "isUpgrade": False,
        "downloadId": "XYZ",
    }


def test_test_event_returns_200_and_does_nothing(ctx):
    r = ctx.app.post("/webhook", json={"eventType": "Test"})
    assert r.status_code == 200
    assert ctx.rec.notifications == []


def test_reject_quarantines_and_selfheals(ctx):
    r = ctx.app.post("/webhook", json=_radarr_import(ctx.file))
    assert r.status_code == 200
    # file copied into quarantine (filename carries the attempt number, see Finding C)
    found = []
    for root, _, files in os.walk(ctx.quar):
        found += files
    assert any(name.endswith("Heat.mkv") for name in found)
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
    for i, did in enumerate(["A", "B", "C"]):
        p = dict(_radarr_import(ctx.file)); p["downloadId"] = did
        # each re-grab imports a NEW file record (new movieFile.id), same as
        # real Radarr; reusing the same id would collide with the Finding-A
        # per-file idempotency key and mask this test's intent.
        p["movieFile"] = {"id": 100 + i, "path": ctx.file}
        # regenerate the source file each time (previous run moved it)
        os.makedirs(os.path.dirname(ctx.file), exist_ok=True)
        open(ctx.file, "wb").write(b"x" * 100)
        ctx.app.post("/webhook", json=p)
    p = dict(_radarr_import(ctx.file)); p["downloadId"] = "D"
    p["movieFile"] = {"id": 200, "path": ctx.file}
    open(ctx.file, "wb").write(b"x" * 100)
    marks_before = len(ctx.rec.marked_failed)
    ctx.app.post("/webhook", json=p)
    # 4th attempt beyond max=3: no additional re-search
    assert len(ctx.rec.marked_failed) == marks_before
    assert any("manual" in m.lower() or "gave up" in m.lower()
               for _, m in ctx.rec.notifications)


def test_season_pack_different_episode_files_both_validated(ctx):
    # Finding A: a season-pack torrent has ONE downloadId but Sonarr fires
    # ONE Download event PER imported episode file. Idempotency must be
    # keyed by the per-file id (episodeFile.id), not by downloadId, or every
    # episode after the first in the pack would be silently treated as a
    # duplicate and skip validation entirely.
    p1 = _sonarr_import(ctx.file)
    p1["downloadId"] = "SEASONPACK"
    p1["episodeFile"] = {"id": 201, "path": ctx.file}
    p1["episodes"] = [{"id": 501}]

    p2 = _sonarr_import(ctx.file)
    p2["downloadId"] = "SEASONPACK"  # same torrent/downloadId as p1
    p2["episodeFile"] = {"id": 202, "path": ctx.file}  # different file
    p2["episodes"] = [{"id": 502}]

    ctx.app.post("/webhook", json=p1)
    ctx.app.post("/webhook", json=p2)

    # Both episode files must have been independently validated and acted on.
    assert ctx.rec.deleted_episode == [201, 202]
    assert ctx.rec.marked_failed == [6, 6]


def test_per_episode_loop_guard_independent_budgets(ctx):
    # Finding B: the attempt-counter budget must be per-episode, not shared
    # across the whole series. Exhausting episode 501's budget must NOT
    # affect a different episode (502) of the SAME series.
    def _episode_payload(download_id, episode_file_id, episode_id):
        p = _sonarr_import(ctx.file)
        p["downloadId"] = download_id
        p["episodeFile"] = {"id": episode_file_id, "path": ctx.file}
        p["episodes"] = [{"id": episode_id}]
        return p

    # Exhaust episode 501's budget (max_attempts=3).
    for i, did in enumerate(["A", "B", "C"]):
        open(ctx.file, "wb").write(b"x" * 100)  # regenerate (previous run moved it)
        ctx.app.post("/webhook", json=_episode_payload(did, 300 + i, 501))

    open(ctx.file, "wb").write(b"x" * 100)
    marks_before = len(ctx.rec.marked_failed)
    ctx.app.post("/webhook", json=_episode_payload("D", 310, 501))
    assert len(ctx.rec.marked_failed) == marks_before  # episode 501: gave up
    assert any("manual" in m.lower() or "gave up" in m.lower()
               for _, m in ctx.rec.notifications)

    # A DIFFERENT episode of the same series must still be handled normally.
    open(ctx.file, "wb").write(b"x" * 100)
    marks_before = len(ctx.rec.marked_failed)
    ctx.app.post("/webhook", json=_episode_payload("E", 320, 502))
    assert len(ctx.rec.marked_failed) == marks_before + 1
    assert ctx.rec.deleted_episode[-1] == 320


def _sonarr_runtime_ctx(tmp_path, fake_arr):
    """Build an app whose validate_fn records the expected_runtime_min it
    receives, so we can assert what floor app.py computed for a Sonarr event."""
    captured = {}
    lib = tmp_path / "media"; lib.mkdir()
    quar = tmp_path / "quar"; quar.mkdir()
    src = lib / "The Wire"; src.mkdir()
    f = src / "ep.mkv"; f.write_bytes(b"x" * 100)

    settings = SimpleNamespace(
        library_root=str(lib), quarantine_root=str(quar),
        ntfy_url="http://ntfy/arr-media", max_attempts=3,
    )

    from state import AttemptStore
    store = AttemptStore(str(tmp_path / "s.db"))

    def validate_fn(path, original_language_name, expected_runtime_min):
        captured["runtime"] = expected_runtime_min
        return Verdict(True, "ok", "")

    app = create_app(settings, fake_arr, fake_arr, store,
                     validate_fn=validate_fn, notify_fn=lambda *a: None)
    return SimpleNamespace(app=app.test_client(), file=str(f), captured=captured)


def test_sonarr_runtime_floor_is_series_runtime_times_episode_count(tmp_path):
    class FakeArr:
        def get_series(self, sid): return {"id": sid, "runtime": 55}
        def delete_episodefile(self, fid): pass
        def find_grab_history_id(self, did): return None
        def mark_failed(self, hid): pass

    c = _sonarr_runtime_ctx(tmp_path, FakeArr())
    p = _sonarr_import(c.file)
    p["episodes"] = [{"id": 501}, {"id": 502}]  # a 2-episode file
    c.app.post("/webhook", json=p)
    assert c.captured["runtime"] == 110  # 55 min/ep * 2 episodes


def test_sonarr_runtime_floor_none_when_series_lookup_fails(tmp_path):
    class FakeArr:
        def get_series(self, sid): raise RuntimeError("sonarr unreachable")
        def delete_episodefile(self, fid): pass
        def find_grab_history_id(self, did): return None
        def mark_failed(self, hid): pass

    c = _sonarr_runtime_ctx(tmp_path, FakeArr())
    p = _sonarr_import(c.file)
    p["episodes"] = [{"id": 501}]
    r = c.app.post("/webhook", json=p)
    assert r.status_code == 200
    assert c.captured["runtime"] is None  # floor skipped, import not blocked


def test_sonarr_runtime_floor_none_when_series_runtime_falsy(tmp_path):
    class FakeArr:
        def get_series(self, sid): return {"id": sid, "runtime": 0}  # e.g. specials
        def delete_episodefile(self, fid): pass
        def find_grab_history_id(self, did): return None
        def mark_failed(self, hid): pass

    c = _sonarr_runtime_ctx(tmp_path, FakeArr())
    p = _sonarr_import(c.file)
    p["episodes"] = [{"id": 501}]
    c.app.post("/webhook", json=p)
    assert c.captured["runtime"] is None


def test_pass_path_logs_info_so_healthy_imports_are_visible(tmp_path, caplog):
    # Observability: waitress does not log per-request access lines, and the
    # PASS path is otherwise silent -> a healthy gate would be invisible in
    # `docker logs`. A confident PASS must emit an INFO line naming the title.
    lib = tmp_path / "media"; lib.mkdir()
    quar = tmp_path / "quar"; quar.mkdir()
    src = lib / "Heat (1995)"; src.mkdir()
    f = src / "Heat.mkv"; f.write_bytes(b"x" * 100)

    settings = SimpleNamespace(
        library_root=str(lib), quarantine_root=str(quar),
        ntfy_url="http://ntfy/arr-media", max_attempts=3,
    )
    from state import AttemptStore
    store = AttemptStore(str(tmp_path / "s.db"))

    class FakeArr:
        def delete_moviefile(self, fid): pass
        def delete_episodefile(self, fid): pass
        def find_grab_history_id(self, did): return None
        def mark_failed(self, hid): pass

    app = create_app(settings, FakeArr(), FakeArr(), store,
                     validate_fn=lambda **kw: Verdict(True, "ok", "orig=en, stream 0 matches"),
                     notify_fn=lambda *a: None)
    client = app.test_client()

    with caplog.at_level(logging.INFO, logger="app"):
        r = client.post("/webhook", json=_radarr_import(str(f)))
    assert r.status_code == 200
    assert any(rec.levelno == logging.INFO and "Heat" in rec.message
               for rec in caplog.records)


def test_reject_quarantines_and_selfheals_sonarr(ctx):
    r = ctx.app.post("/webhook", json=_sonarr_import(ctx.file))
    assert r.status_code == 200
    found = []
    for root, _, files in os.walk(ctx.quar):
        found += files
    assert any(name.endswith("Heat.mkv") for name in found)
    assert ctx.rec.deleted_episode == [91]
    assert ctx.rec.deleted == []  # movie-file deletion never invoked for a Sonarr event
    assert ctx.rec.marked_failed == [6]
    assert len(ctx.rec.notifications) == 1


def test_errored_verdict_never_quarantines(tmp_path):
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
        def delete_episodefile(self, fid): rec.deleted_episode.append(fid)
        def find_grab_history_id(self, did): return 6
        def mark_failed(self, hid): rec.marked_failed.append(hid)

    from state import AttemptStore
    store = AttemptStore(str(tmp_path / "s.db"))

    def notify_fn(url, title, tags, prio, msg): rec.notifications.append((title, msg))

    app = create_app(
        settings, FakeArr(), FakeArr(), store,
        validate_fn=lambda **kw: Verdict(True, "ok", "gate error: whisper crashed", errored=True),
        notify_fn=notify_fn,
    )
    client = app.test_client()

    r = client.post("/webhook", json=_radarr_import(str(f)))
    assert r.status_code == 200
    assert r.get_json()["status"] == "errored-passed"

    # gate error must never quarantine or self-heal
    assert rec.deleted == []
    assert rec.marked_failed == []
    found = []
    for root, _, files in os.walk(str(quar)):
        found += files
    assert found == []

    # but it must still notify (the "gate unavailable" message, not the quarantine one)
    assert len(rec.notifications) == 1
    title, _ = rec.notifications[0]
    assert "indispon" in title.lower()


def test_real_validator_wired_end_to_end_rejects_wrong_language(tmp_path):
    """Finding D: every other test injects a FAKE validate_fn, so the real
    keyword-argument seam between app.py and validator.validate() has never
    been exercised. Wire the REAL validator.validate through app.py's webhook
    handler, exactly like the production `if __name__ == "__main__"` block
    does, stubbing only the whisper transcribe call. A confident non-English
    detection must make the REAL validator compute a genuine wrong-language
    reject, proving app.py -> validator.validate() -> media_probe are wired
    correctly end-to-end with real ffmpeg/ffprobe."""
    rec = Recorder()
    lib = tmp_path / "media"; lib.mkdir()
    quar = tmp_path / "quar"; quar.mkdir()
    src = lib / "Heat (1995)"; src.mkdir()
    clip = str(src / "Heat.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=35:size=128x72:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=35",
         "-metadata:s:a:0", "language=eng", "-shortest", clip],
        check=True, capture_output=True,
    )

    settings = SimpleNamespace(
        library_root=str(lib), quarantine_root=str(quar),
        ntfy_url="http://ntfy/arr-media", max_attempts=3,
        # validator.validate()'s own knobs (see tests/test_validator.py's _settings):
        lang_prob_threshold=0.7, sample_windows=2, sample_seconds=3,
        skip_intro_fraction=0.1,
    )

    class FakeArr:
        def delete_moviefile(self, fid): rec.deleted.append(fid)
        def delete_episodefile(self, fid): rec.deleted_episode.append(fid)
        def find_grab_history_id(self, did): return 6
        def mark_failed(self, hid): rec.marked_failed.append(hid)

    from state import AttemptStore
    store = AttemptStore(str(tmp_path / "s.db"))

    def notify_fn(url, title, tags, prio, msg): rec.notifications.append((title, msg))

    def fake_transcribe_fn(clip_path):
        return ("ru", 0.95)  # confidently NOT English -> real validator rejects

    def validate_fn(path, original_language_name, expected_runtime_min):
        return validate(path, original_language_name, expected_runtime_min,
                        settings, fake_transcribe_fn)

    app = create_app(settings, FakeArr(), FakeArr(), store,
                     validate_fn=validate_fn, notify_fn=notify_fn)
    client = app.test_client()

    payload = _radarr_import(clip)
    # keep expected runtime short so the 35s fixture clip clears the
    # duration-floor check (same pattern as tests/test_validator.py's eng_clip
    # tests, which pass expected_runtime_min=1).
    payload["movie"]["runtime"] = 1

    r = client.post("/webhook", json=payload)
    assert r.status_code == 200
    assert r.get_json()["status"] == "quarantined"

    found = []
    for root, _, files in os.walk(str(quar)):
        found += files
    assert any(name.endswith("Heat.mkv") for name in found)
    assert rec.deleted == [79]
    assert rec.marked_failed == [6]
