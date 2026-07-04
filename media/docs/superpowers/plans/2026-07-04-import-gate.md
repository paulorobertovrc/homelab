# import-gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a post-import sidecar that verifies each imported movie/episode has a real original-language audio track (Whisper) and is intact (ffprobe), and on failure quarantines the file, blocklists the release, and triggers a re-search — with a loop guard.

**Architecture:** A small Flask HTTP server receives Sonarr/Radarr webhooks on import. It probes the file with ffprobe (integrity + audio-stream enumeration), extracts short audio windows with ffmpeg, and runs faster-whisper (CPU) to detect the spoken language of the track that should be the original. On a confident mismatch or an integrity failure it copies the file to a quarantine folder outside the library, deletes the *arr file record, marks the grabbed-history item failed (native blocklist + re-search), and notifies via ntfy. A SQLite attempt counter caps re-search loops.

**Tech Stack:** Python 3.12, Flask, faster-whisper (CTranslate2, CPU int8), ffmpeg/ffprobe, SQLite (stdlib), Docker.

## Global Constraints

- Runs as a container `import-gate` on `servarr_network`, static IP `172.39.0.17`, HTTP server on container port `8080`, **no host port mapping**.
- Library mounted **read-only** at `/data/media`; quarantine mounted **read-write** at `/data/quarantine` (host `/mnt/d/quarantine/arr_server`); config/state at `/config` (host `${CONFIG_ROOT}/import-gate`).
- Secrets (Sonarr/Radarr API keys) come from env, injected from `media/.env` — never committed.
- Whisper runs on **CPU** (`device="cpu"`, `compute_type="int8"`), model `small`. No GPU / no `nvidia-container-toolkit` dependency.
- Language rule: a file **passes language check** if at least one audio track is confidently the title's `originalLanguage`. Reject only on **confident** mismatch; ties/low-confidence **pass** (never destroy a good file).
- A gate *error* (ffprobe/whisper crash) must **never** quarantine: log, notify "gate unavailable, imported without validation", let the import stand.
- Loop guard: **N = 3** attempts per title, then stop re-searching and notify for manual intervention.
- ntfy topic `arr-media` at `http://ntfy:80` (internal service name on `servarr_network`).
- Follow the repo's existing style: services documented with a banner comment block in `compose.yaml`; env vars named `SET_IP_*` for static IPs.

---

## File Structure

```
media/import-gate/
  Dockerfile
  requirements.txt
  config.py          # env-driven settings (arr endpoints/keys, thresholds, paths)
  languages.py       # language name <-> ISO-639-1 code mapping
  state.py           # SQLite attempt counter (per title key)
  media_probe.py     # ffprobe (integrity + streams) + ffmpeg (extract audio windows)
  validator.py       # integrity decision + whisper language decision -> Verdict
  arr_client.py      # Sonarr/Radarr API wrapper (metadata, delete file, mark failed)
  notify.py          # ntfy push
  app.py             # Flask webhook endpoint, orchestration, idempotency
  tests/
    conftest.py
    test_languages.py
    test_state.py
    test_media_probe.py
    test_validator.py
    test_arr_client.py
    test_app.py
    fixtures/        # short generated clips (created by tests, not committed)
```

`config.py`, `languages.py`, `state.py`, `media_probe.py`, `validator.py`, `arr_client.py`, `notify.py`, `app.py` are each one focused responsibility. Data flows `app -> (arr_client, validator -> media_probe) -> (state, arr_client, notify)`.

---

## Task 1: Scaffold, config, and language map

**Files:**
- Create: `media/import-gate/requirements.txt`
- Create: `media/import-gate/config.py`
- Create: `media/import-gate/languages.py`
- Create: `media/import-gate/tests/conftest.py`
- Create: `media/import-gate/tests/test_languages.py`

**Interfaces:**
- Produces: `config.Settings` dataclass with fields `radarr_url: str`, `radarr_key: str`, `sonarr_url: str`, `sonarr_key: str`, `library_root: str`, `quarantine_root: str`, `state_dir: str`, `ntfy_url: str`, `whisper_model: str`, `lang_prob_threshold: float`, `max_attempts: int`, `sample_windows: int`, `sample_seconds: int`, `skip_intro_fraction: float`; classmethod `Settings.from_env() -> Settings`.
- Produces: `languages.to_code(name: str) -> str | None` (e.g. `"English" -> "en"`, `"Russian" -> "ru"`), `languages.same_language(name: str, code: str) -> bool`.

- [x] **Step 1: Write `requirements.txt`**

```text
flask==3.0.3
faster-whisper==1.0.3
requests==2.32.3
```

- [x] **Step 2: Write the failing test for the language map**

Create `media/import-gate/tests/test_languages.py`:

```python
from languages import to_code, same_language


def test_english_maps_to_en():
    assert to_code("English") == "en"


def test_russian_maps_to_ru():
    assert to_code("Russian") == "ru"


def test_portuguese_maps_to_pt():
    assert to_code("Portuguese") == "pt"


def test_unknown_language_returns_none():
    assert to_code("Klingon") is None


def test_same_language_true_case_insensitive():
    assert same_language("English", "en") is True


def test_same_language_false():
    assert same_language("English", "ru") is False


def test_same_language_unknown_name_is_false():
    assert same_language("Klingon", "en") is False
```

- [x] **Step 3: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_languages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'languages'`

- [x] **Step 4: Implement `languages.py`**

```python
"""Map Servarr language names to ISO-639-1 codes (what faster-whisper returns)."""

_NAME_TO_CODE = {
    "english": "en",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "hindi": "hi",
    "arabic": "ar",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "ukrainian": "uk",
}


def to_code(name: str) -> str | None:
    if not name:
        return None
    return _NAME_TO_CODE.get(name.strip().lower())


def same_language(name: str, code: str) -> bool:
    mapped = to_code(name)
    return mapped is not None and mapped == code
```

- [x] **Step 5: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_languages.py -v`
Expected: PASS (7 passed)

- [x] **Step 6: Implement `config.py`**

```python
"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    radarr_url: str
    radarr_key: str
    sonarr_url: str
    sonarr_key: str
    library_root: str
    quarantine_root: str
    state_dir: str
    ntfy_url: str
    whisper_model: str
    lang_prob_threshold: float
    max_attempts: int
    sample_windows: int
    sample_seconds: int
    skip_intro_fraction: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            radarr_url=os.environ.get("RADARR_URL", "http://172.39.0.4:7878"),
            radarr_key=os.environ["RADARR_API_KEY"],
            sonarr_url=os.environ.get("SONARR_URL", "http://172.39.0.3:8989"),
            sonarr_key=os.environ["SONARR_API_KEY"],
            library_root=os.environ.get("LIBRARY_ROOT", "/data/media"),
            quarantine_root=os.environ.get("QUARANTINE_ROOT", "/data/quarantine"),
            state_dir=os.environ.get("STATE_DIR", "/config"),
            ntfy_url=os.environ.get("NTFY_URL", "http://ntfy:80/arr-media"),
            whisper_model=os.environ.get("WHISPER_MODEL", "small"),
            lang_prob_threshold=float(os.environ.get("LANG_PROB_THRESHOLD", "0.7")),
            max_attempts=int(os.environ.get("MAX_ATTEMPTS", "3")),
            sample_windows=int(os.environ.get("SAMPLE_WINDOWS", "3")),
            sample_seconds=int(os.environ.get("SAMPLE_SECONDS", "30")),
            skip_intro_fraction=float(os.environ.get("SKIP_INTRO_FRACTION", "0.1")),
        )
```

- [x] **Step 7: Write `tests/conftest.py` (make modules importable + shared tmp settings)**

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [x] **Step 8: Commit**

```bash
git add media/import-gate/requirements.txt media/import-gate/config.py \
        media/import-gate/languages.py media/import-gate/tests/
git commit -m "feat(import-gate): scaffold config + language map"
```

---

## Task 2: Attempt-counter state (SQLite)

**Files:**
- Create: `media/import-gate/state.py`
- Create: `media/import-gate/tests/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `state.AttemptStore(db_path: str)` with methods `increment(title_key: str) -> int` (returns the new count), `get(title_key: str) -> int`, `reset(title_key: str) -> None`.

- [x] **Step 1: Write the failing test**

Create `media/import-gate/tests/test_state.py`:

```python
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
```

- [x] **Step 2: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`

- [x] **Step 3: Implement `state.py`**

```python
"""Per-title attempt counter, persisted in SQLite so the loop guard survives restarts."""
import sqlite3


class AttemptStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(title_key TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )

    def _conn(self):
        return sqlite3.connect(self._db_path)

    def increment(self, title_key: str) -> int:
        with self._conn() as c:
            c.execute(
                "INSERT INTO attempts(title_key, count) VALUES(?, 1) "
                "ON CONFLICT(title_key) DO UPDATE SET count = count + 1",
                (title_key,),
            )
            row = c.execute(
                "SELECT count FROM attempts WHERE title_key = ?", (title_key,)
            ).fetchone()
            return row[0]

    def get(self, title_key: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT count FROM attempts WHERE title_key = ?", (title_key,)
            ).fetchone()
            return row[0] if row else 0

    def reset(self, title_key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM attempts WHERE title_key = ?", (title_key,))
```

- [x] **Step 4: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_state.py -v`
Expected: PASS (5 passed)

- [x] **Step 5: Commit**

```bash
git add media/import-gate/state.py media/import-gate/tests/test_state.py
git commit -m "feat(import-gate): persistent per-title attempt counter"
```

---

## Task 3: Media probe (ffprobe integrity + streams, ffmpeg window extraction)

**Files:**
- Create: `media/import-gate/media_probe.py`
- Create: `media/import-gate/tests/test_media_probe.py`

**Interfaces:**
- Consumes: nothing (shells out to `ffprobe`/`ffmpeg`).
- Produces:
  - `media_probe.ProbeResult` dataclass: `has_video: bool`, `has_audio: bool`, `duration_sec: float`, `audio_langs: list[str | None]` (per-stream language tag, `None` if untagged).
  - `media_probe.probe(path: str) -> ProbeResult` (raises `media_probe.ProbeError` if ffprobe fails/file unreadable).
  - `media_probe.extract_windows(path: str, stream_index: int, out_dir: str, windows: int, seconds: int, skip_fraction: float, duration_sec: float) -> list[str]` (returns paths of extracted wav clips).

- [x] **Step 1: Write the failing test (uses a tiny generated fixture)**

Create `media/import-gate/tests/test_media_probe.py`:

```python
import os
import subprocess
import pytest
from media_probe import probe, extract_windows, ProbeError


@pytest.fixture
def sine_video(tmp_path):
    """A 20s clip: 1 video stream + 1 audio stream (440Hz tone)."""
    out = os.path.join(tmp_path, "clip.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=20:size=128x72:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-metadata:s:a:0", "language=eng", "-shortest", out],
        check=True, capture_output=True,
    )
    return out


def test_probe_detects_streams(sine_video):
    r = probe(sine_video)
    assert r.has_video is True
    assert r.has_audio is True
    assert r.duration_sec == pytest.approx(20, abs=1)
    assert r.audio_langs == ["eng"]


def test_probe_unreadable_raises(tmp_path):
    bad = os.path.join(tmp_path, "nope.mkv")
    open(bad, "wb").write(b"not a video")
    with pytest.raises(ProbeError):
        probe(bad)


def test_extract_windows_returns_clips(sine_video):
    out_dir = os.path.dirname(sine_video)
    clips = extract_windows(sine_video, 0, out_dir, windows=2, seconds=3,
                            skip_fraction=0.1, duration_sec=20)
    assert len(clips) == 2
    for c in clips:
        assert os.path.getsize(c) > 0
```

- [x] **Step 2: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_media_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_probe'`

- [x] **Step 3: Implement `media_probe.py`**

```python
"""Thin wrappers over ffprobe/ffmpeg: integrity + stream info, and audio sampling."""
import json
import os
import subprocess
from dataclasses import dataclass


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    has_video: bool
    has_audio: bool
    duration_sec: float
    audio_langs: list  # list[str | None], one per audio stream


def probe(path: str) -> ProbeResult:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        raise ProbeError(f"ffprobe failed for {path}: {e.stderr}") from e
    data = json.loads(out)
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    try:
        duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    audio_langs = [s.get("tags", {}).get("language") for s in audio]
    return ProbeResult(
        has_video=bool(video),
        has_audio=bool(audio),
        duration_sec=duration,
        audio_langs=audio_langs,
    )


def extract_windows(path, stream_index, out_dir, windows, seconds,
                    skip_fraction, duration_sec):
    """Extract `windows` wav clips of `seconds` each, evenly spaced through the
    middle of the file, skipping the first `skip_fraction` of runtime."""
    start = duration_sec * skip_fraction
    usable = max(duration_sec - start - seconds, 0)
    clips = []
    for i in range(windows):
        offset = start + (usable * i / max(windows - 1, 1) if windows > 1 else usable / 2)
        clip = os.path.join(out_dir, f"win_{i}.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(offset), "-t", str(seconds),
             "-i", path, "-map", f"0:a:{stream_index}",
             "-ac", "1", "-ar", "16000", clip],
            check=True, capture_output=True,
        )
        clips.append(clip)
    return clips
```

- [x] **Step 4: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_media_probe.py -v`
Expected: PASS (3 passed). (Requires `ffmpeg`/`ffprobe` on PATH — present in the container and on this host.)

- [x] **Step 5: Commit**

```bash
git add media/import-gate/media_probe.py media/import-gate/tests/test_media_probe.py
git commit -m "feat(import-gate): ffprobe integrity/streams + ffmpeg window extraction"
```

---

## Task 4: Validator (integrity + whisper language decision)

**Files:**
- Create: `media/import-gate/validator.py`
- Create: `media/import-gate/tests/test_validator.py`

**Interfaces:**
- Consumes: `media_probe.ProbeResult`, `media_probe.probe`, `media_probe.extract_windows`; `languages.same_language`.
- Produces:
  - `validator.Verdict` dataclass: `ok: bool`, `reason: str` (machine slug: `"ok"`, `"corrupt"`, `"no-audio"`, `"wrong-language"`), `detail: str` (human string for ntfy, e.g. `"orig=en, detected=ru"`), `errored: bool` (True only if the gate itself failed).
  - `validator.validate(path: str, original_language_name: str, expected_runtime_min: int | None, settings, transcribe_fn) -> Verdict`.
  - `transcribe_fn(clip_path: str) -> tuple[str, float]` is injected (returns `(lang_code, probability)`) so tests don't need the real model. Production passes a closure over a loaded `WhisperModel`.

- [x] **Step 1: Write the failing test**

Create `media/import-gate/tests/test_validator.py`:

```python
import os
import subprocess
import pytest
from types import SimpleNamespace
from validator import validate


def _settings(**over):
    base = dict(lang_prob_threshold=0.7, sample_windows=2, sample_seconds=3,
                skip_intro_fraction=0.1)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def eng_clip(tmp_path):
    out = os.path.join(tmp_path, "clip.mkv")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=20:size=128x72:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-metadata:s:a:0", "language=eng", "-shortest", out],
        check=True, capture_output=True,
    )
    return out


def test_pass_when_detected_matches_original(eng_clip):
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("en", 0.95))
    assert v.ok is True and v.reason == "ok"


def test_reject_confident_mismatch(eng_clip):
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("ru", 0.95))
    assert v.ok is False and v.reason == "wrong-language"
    assert "ru" in v.detail


def test_low_confidence_passes(eng_clip):
    # detected differs but below threshold -> do not destroy a good file
    v = validate(eng_clip, "English", 1, _settings(), lambda p: ("ru", 0.4))
    assert v.ok is True


def test_corrupt_file_rejected_without_whisper(tmp_path):
    bad = os.path.join(tmp_path, "bad.mkv")
    open(bad, "wb").write(b"not a video")

    def boom(_):
        raise AssertionError("whisper must not run on a corrupt file")

    v = validate(bad, "English", 1, _settings(), boom)
    assert v.ok is False and v.reason == "corrupt"


def test_transcribe_error_sets_errored_not_reject(eng_clip):
    def boom(_):
        raise RuntimeError("model exploded")

    v = validate(eng_clip, "English", 1, _settings(), boom)
    assert v.errored is True and v.ok is True  # errored gate does not quarantine
```

- [x] **Step 2: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'validator'`

- [x] **Step 3: Implement `validator.py`**

```python
"""Decide pass/reject for one imported file. Integrity first (cheap), then whisper."""
import tempfile
from collections import Counter
from dataclasses import dataclass

from languages import same_language, to_code
import media_probe


@dataclass
class Verdict:
    ok: bool
    reason: str      # "ok" | "corrupt" | "no-audio" | "wrong-language"
    detail: str
    errored: bool = False


def validate(path, original_language_name, expected_runtime_min, settings, transcribe_fn):
    # 1. Integrity (cheap, no whisper).
    try:
        result = media_probe.probe(path)
    except media_probe.ProbeError:
        return Verdict(False, "corrupt", "ffprobe could not read the file")

    if not result.has_video or result.duration_sec <= 0:
        return Verdict(False, "corrupt", "missing video stream or zero duration")
    if not result.has_audio:
        return Verdict(False, "no-audio", "no audio stream")
    if expected_runtime_min:
        floor = expected_runtime_min * 60 * 0.5  # >50% of expected runtime
        if result.duration_sec < floor:
            return Verdict(False, "corrupt",
                           f"duration {int(result.duration_sec)}s < floor {int(floor)}s")

    orig_code = to_code(original_language_name)
    if orig_code is None:
        # Unknown original language -> can't judge; pass (don't destroy).
        return Verdict(True, "ok", f"original language '{original_language_name}' not mappable")

    # 2. Whisper on each audio stream; pass as soon as one stream is confidently original.
    try:
        for stream_index, _ in enumerate(result.audio_langs):
            with tempfile.TemporaryDirectory() as tmp:
                clips = media_probe.extract_windows(
                    path, stream_index, tmp, settings.sample_windows,
                    settings.sample_seconds, settings.skip_intro_fraction,
                    result.duration_sec,
                )
                votes = []
                for clip in clips:
                    code, prob = transcribe_fn(clip)
                    if prob >= settings.lang_prob_threshold:
                        votes.append(code)
                if votes:
                    winner, _ = Counter(votes).most_common(1)[0]
                    if winner == orig_code:
                        return Verdict(True, "ok", f"orig={orig_code}, stream {stream_index} matches")
    except Exception as e:  # gate failure -> never quarantine
        return Verdict(True, "ok", f"gate error: {e}", errored=True)

    # No stream confidently matched the original language.
    return Verdict(False, "wrong-language",
                   f"orig={orig_code}, no audio stream confidently matched")
```

- [x] **Step 4: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_validator.py -v`
Expected: PASS (5 passed)

- [x] **Step 5: Commit**

```bash
git add media/import-gate/validator.py media/import-gate/tests/test_validator.py
git commit -m "feat(import-gate): integrity + whisper language verdict"
```

---

## Task 5: Servarr API client

**Files:**
- Create: `media/import-gate/arr_client.py`
- Create: `media/import-gate/tests/test_arr_client.py`

**Interfaces:**
- Consumes: `config.Settings`.
- Produces: `arr_client.ArrClient(base_url: str, api_key: str, kind: str)` where `kind in {"radarr","sonarr"}`, with methods:
  - `get_movie(movie_id: int) -> dict` / `get_series(series_id: int) -> dict`
  - `delete_moviefile(file_id: int) -> None` (`DELETE /api/v3/moviefile/{id}`) / `delete_episodefile(file_id: int) -> None`
  - `find_grab_history_id(download_id: str) -> int | None` (`GET /api/v3/history?downloadId=...`, find `eventType == "grabbed"`)
  - `mark_failed(history_id: int) -> None` (`POST /api/v3/history/failed/{id}`)

- [x] **Step 1: Write the failing test (HTTP mocked via a fake session)**

Create `media/import-gate/tests/test_arr_client.py`:

```python
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
```

- [x] **Step 2: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_arr_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'arr_client'`

- [x] **Step 3: Implement `arr_client.py`**

```python
"""Minimal Sonarr/Radarr v3 API wrapper for the self-heal steps."""
import requests


class ArrClient:
    def __init__(self, base_url: str, api_key: str, kind: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.kind = kind
        self._session = requests.Session()

    def _req(self, method, path, **kw):
        headers = kw.pop("headers", {})
        headers["X-Api-Key"] = self.api_key
        resp = self._session.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=30, **kw
        )
        resp.raise_for_status()
        return resp

    def get_movie(self, movie_id: int) -> dict:
        return self._req("GET", f"/api/v3/movie/{movie_id}").json()

    def get_series(self, series_id: int) -> dict:
        return self._req("GET", f"/api/v3/series/{series_id}").json()

    def delete_moviefile(self, file_id: int) -> None:
        self._req("DELETE", f"/api/v3/moviefile/{file_id}")

    def delete_episodefile(self, file_id: int) -> None:
        self._req("DELETE", f"/api/v3/episodefile/{file_id}")

    def find_grab_history_id(self, download_id: str) -> int | None:
        records = self._req(
            "GET", "/api/v3/history",
            params={"downloadId": download_id, "pageSize": 50},
        ).json().get("records", [])
        for r in records:
            if r.get("eventType") == "grabbed" and r.get("downloadId") == download_id:
                return r.get("id")
        return None

    def mark_failed(self, history_id: int) -> None:
        self._req("POST", f"/api/v3/history/failed/{history_id}")
```

- [x] **Step 4: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_arr_client.py -v`
Expected: PASS (5 passed)

- [x] **Step 5: Commit**

```bash
git add media/import-gate/arr_client.py media/import-gate/tests/test_arr_client.py
git commit -m "feat(import-gate): Sonarr/Radarr API client for self-heal"
```

---

## Task 6: ntfy notify + app orchestration (webhook)

**Files:**
- Create: `media/import-gate/notify.py`
- Create: `media/import-gate/app.py`
- Create: `media/import-gate/tests/test_app.py`

**Interfaces:**
- Consumes: `config.Settings`, `arr_client.ArrClient`, `validator.validate`, `state.AttemptStore`, `notify.push`, `languages`.
- Produces:
  - `notify.push(ntfy_url: str, title: str, tags: str, priority: int, message: str) -> None`.
  - `app.create_app(settings, radarr, sonarr, store, validate_fn, notify_fn) -> Flask` with `POST /webhook` and `GET /health`. Dependencies are injected so tests never touch real *arr/whisper.
  - Quarantine + self-heal orchestration in `app.handle_import(payload, ...)`.

**Webhook payload shape (verified against the live API):** import events arrive as `eventType: "Download"` (Radarr) / `"Download"` (Sonarr). Radarr payload has `movie: {id, title, originalLanguage: {name}, ...}`, `movieFile: {id, path, relativePath}`, `isUpgrade`, `downloadId`. Sonarr has `series: {id, title, originalLanguage: {name}}`, `episodeFile: {id, path}`, `episodes: [...]`, `downloadId`. A `eventType: "Test"` ping must return 200 without doing anything.

- [x] **Step 1: Write the failing test**

Create `media/import-gate/tests/test_app.py`:

```python
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
```

- [x] **Step 2: Run it, verify it fails**

Run: `cd media/import-gate && python -m pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [x] **Step 3: Implement `notify.py`**

```python
"""ntfy push (best-effort; never raises into the caller)."""
import requests


def push(ntfy_url: str, title: str, tags: str, priority: int, message: str) -> None:
    try:
        requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Priority": str(priority)},
            timeout=10,
        )
    except Exception:
        pass
```

- [x] **Step 4: Implement `app.py`**

```python
"""Flask webhook receiver + self-heal orchestration."""
import os
import shutil
from flask import Flask, request, jsonify


def _title_key(kind, media_id):
    return f"{kind}:{media_id}"


def create_app(settings, radarr, sonarr, store, validate_fn, notify_fn):
    app = Flask(__name__)
    seen_download_ids = set()  # idempotency within process lifetime

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/webhook")
    def webhook():
        payload = request.get_json(force=True, silent=True) or {}
        event = payload.get("eventType")
        if event == "Test":
            return jsonify(status="test-ok")
        if event != "Download":
            return jsonify(status="ignored", event=event)

        is_radarr = "movie" in payload
        arr = radarr if is_radarr else sonarr
        kind = "radarr" if is_radarr else "sonarr"

        if is_radarr:
            media = payload["movie"]
            media_file = payload["movieFile"]
            media_id = media["id"]
            title = media.get("title", "?")
            orig_lang = media.get("originalLanguage", {}).get("name", "")
            runtime = media.get("runtime")
            file_id = media_file["id"]
            delete_file = arr.delete_moviefile
        else:
            media = payload["series"]
            media_file = payload["episodeFile"]
            media_id = media["id"]
            title = media.get("title", "?")
            orig_lang = media.get("originalLanguage", {}).get("name", "")
            runtime = None
            file_id = media_file["id"]
            delete_file = arr.delete_episodefile

        download_id = payload.get("downloadId")
        if download_id and download_id in seen_download_ids:
            return jsonify(status="duplicate")
        if download_id:
            seen_download_ids.add(download_id)

        path = media_file["path"]
        verdict = validate_fn(path=path, original_language_name=orig_lang,
                              expected_runtime_min=runtime)

        if verdict.errored:
            notify_fn(settings.ntfy_url, "Import-gate indisponível", "warning", 3,
                      f"{title}: importado sem validação ({verdict.detail})")
            return jsonify(status="errored-passed")

        if verdict.ok:
            return jsonify(status="passed")

        # --- reject: loop guard, quarantine, self-heal ---
        key = _title_key(kind, media_id)
        attempts = store.get(key)
        if attempts >= settings.max_attempts:
            notify_fn(settings.ntfy_url, "⚠️ Import-gate desistiu", "no_entry", 4,
                      f"{title}: {settings.max_attempts} tentativas sem faixa original. "
                      f"Intervenção manual necessária.")
            return jsonify(status="gave-up")

        _quarantine(path, settings, title, verdict.reason)
        try:
            delete_file(file_id)
            if download_id:
                hid = arr.find_grab_history_id(download_id)
                if hid is not None:
                    arr.mark_failed(hid)
        except Exception as e:
            notify_fn(settings.ntfy_url, "Import-gate erro no self-heal", "warning", 4,
                      f"{title}: quarentenado, mas self-heal falhou: {e}")
            return jsonify(status="quarantined-selfheal-failed")

        n = store.increment(key)
        notify_fn(settings.ntfy_url, "🔒 Quarentena", "lock", 3,
                  f"{title}: {verdict.detail}. Tentativa {n}. Re-busca disparada.")
        return jsonify(status="quarantined", attempt=n)

    def _quarantine(path, settings, title, reason):
        dest_dir = os.path.join(settings.quarantine_root, f"{title} ({reason})")
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(dest_dir, os.path.basename(path)))

    return app


if __name__ == "__main__":  # production entrypoint
    from config import Settings
    from arr_client import ArrClient
    from state import AttemptStore
    from validator import validate as _validate
    from notify import push as _push
    from faster_whisper import WhisperModel

    s = Settings.from_env()
    model = WhisperModel(s.whisper_model, device="cpu", compute_type="int8")

    def transcribe_fn(clip_path):
        _segs, info = model.transcribe(clip_path)
        return info.language, info.language_probability

    def validate_fn(path, original_language_name, expected_runtime_min):
        return _validate(path, original_language_name, expected_runtime_min, s, transcribe_fn)

    application = create_app(
        s,
        ArrClient(s.radarr_url, s.radarr_key, "radarr"),
        ArrClient(s.sonarr_url, s.sonarr_key, "sonarr"),
        AttemptStore(os.path.join(s.state_dir, "attempts.db")),
        validate_fn, _push,
    )
    application.run(host="0.0.0.0", port=8080)
```

- [x] **Step 5: Run it, verify it passes**

Run: `cd media/import-gate && python -m pytest tests/test_app.py -v`
Expected: PASS (4 passed)

- [x] **Step 6: Run the whole suite**

Run: `cd media/import-gate && python -m pytest -v`
Expected: PASS (all tasks' tests green)

- [x] **Step 7: Commit**

```bash
git add media/import-gate/notify.py media/import-gate/app.py media/import-gate/tests/test_app.py
git commit -m "feat(import-gate): webhook orchestration, quarantine, self-heal, loop guard"
```

---

## Task 7: Dockerfile, compose wiring, *arr config, and real-fixture end-to-end

**Files:**
- Create: `media/import-gate/Dockerfile`
- Create: `media/import-gate/.dockerignore`
- Modify: `media/compose.yaml` (add `import-gate` service)
- Modify: `media/.env` (add `SET_IP_IMPORT_GATE`, `RADARR_API_KEY`, `SONARR_API_KEY`) — not committed
- Modify: `media/.env.example` (add the same keys, blank)

**Interfaces:**
- Consumes: everything above.
- Produces: a running container reachable at `http://172.39.0.17:8080/webhook`.

- [x] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

EXPOSE 8080
CMD ["python", "app.py"]
```

- [x] **Step 2: Write `.dockerignore`**

```text
tests/
__pycache__/
*.pyc
```

- [x] **Step 3: Add the compose service** (`media/compose.yaml`, after the `ntfy` block, matching the repo's banner style)

```yaml
  ###############################################
  # IMPORT-GATE — post-import audio-language + integrity gate
  # Webhook from Sonarr/Radarr on import -> ffprobe integrity + faster-whisper
  # (CPU) language check. On failure: quarantine (outside library) + blocklist
  # + re-search, with a 3-attempt loop guard. Library mounted read-only.
  ###############################################
  import-gate:
    build: ./import-gate
    container_name: import-gate
    restart: unless-stopped
    networks:
      servarr_network:
        ipv4_address: ${SET_IP_IMPORT_GATE:-172.31.0.17}
    environment:
      - TZ=${TZ:-America/Cuiaba}
      - RADARR_URL=http://${SET_IP_RADARR:-172.31.0.11}:7878
      - SONARR_URL=http://${SET_IP_SONARR:-172.31.0.12}:8989
      - RADARR_API_KEY=${RADARR_API_KEY:?Set RADARR_API_KEY in media/.env}
      - SONARR_API_KEY=${SONARR_API_KEY:?Set SONARR_API_KEY in media/.env}
      - NTFY_URL=http://${SET_IP_NTFY:-172.31.0.10}:80/arr-media
    volumes:
      - ${LIBRARY:-/mnt/f/Media}:/data/media:ro
      - /mnt/d/quarantine/arr_server:/data/quarantine
      - ${CONFIG_ROOT:-/docker/appdata}/import-gate:/config
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/health')\" || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
```

- [x] **Step 4: Add env vars**

`media/.env` (real values; NOT committed):

```bash
SET_IP_IMPORT_GATE="172.39.0.17"
RADARR_API_KEY="<paste from /docker/appdata/radarr/config.xml>"
SONARR_API_KEY="<paste from /docker/appdata/sonarr/config.xml>"
```

`media/.env.example` (committed, blank):

```bash
SET_IP_IMPORT_GATE=172.31.0.17
RADARR_API_KEY=
SONARR_API_KEY=
```

- [x] **Step 5: Build and start**

Run:
```bash
cd media && mkdir -p /mnt/d/quarantine/arr_server /docker/appdata/import-gate
docker compose up -d --build import-gate
docker compose logs import-gate | tail -20
```
Expected: container `Up`, log shows Flask listening on `0.0.0.0:8080`. First run downloads the whisper `small` model (one-time).

- [x] **Step 6: Health + Test-event smoke check**

Run:
```bash
docker exec import-gate python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read())"
curl -s -X POST http://172.39.0.17:8080/webhook -H 'Content-Type: application/json' -d '{"eventType":"Test"}'
```
Expected: `{"status": "ok"}` then `{"status":"test-ok"}`.

- [x] **Step 7: Wire the *arr Webhook connections** (both Sonarr and Radarr, via API)

Run (Radarr shown; repeat for Sonarr at `172.39.0.3:8989`):
```bash
RADARR_KEY=$(sudo grep -oP '(?<=<ApiKey>)[^<]+' /docker/appdata/radarr/config.xml)
curl -s -X POST -H "X-Api-Key: $RADARR_KEY" -H "Content-Type: application/json" \
  http://172.39.0.4:7878/api/v3/notification -d '{
    "name":"import-gate","implementation":"Webhook","configContract":"WebhookSettings",
    "onDownload":true,"onUpgrade":true,
    "fields":[{"name":"url","value":"http://172.39.0.17:8080/webhook"},{"name":"method","value":1}]
  }'
```
Expected: HTTP 201 with the created notification JSON.

- [x] **Step 8: Layer-0 config (cheap grab-time filter)** — document, don't over-build

In Sonarr/Radarr UI: set each library's **Language Profile** (Sonarr) / and confirm Radarr's language handling so that releases *tagged* with a non-original language are deprioritized/rejected at grab time. This is configuration; record what was set in `media/README.md`. (No code — reduces how often the gate must run.)

- [x] **Step 9: Real-fixture end-to-end** (the Russian-audio case)

Heat (1995) is already in the library as a RuTracker remux (`originalLanguage=English`, likely Russian primary audio) — a live positive case. Trigger a manual re-check by re-sending its import webhook payload, or run the validator directly inside the container against the mounted path:
```bash
docker exec import-gate python -c "
from config import Settings; from validator import validate
from faster_whisper import WhisperModel
s=Settings.from_env()
m=WhisperModel(s.whisper_model, device='cpu', compute_type='int8')
def tr(c):
    _seg,info=m.transcribe(c); return info.language, info.language_probability
path='/data/media/Movies/Heat (1995)/Схватка - Heat (1995) UHD BDRemux US 2160p HDR Ultimate Collector\'s Edition от RuTracker.mkv'
print(validate(path,'English',170,s,tr))
"
```
Expected: a `Verdict` — if the remux's original English track is present it should be `ok`; if it is Russian-only it should be `wrong-language`. Either way this proves the whisper path runs end-to-end on a real file. **Do not** wire auto-quarantine against the live library until this dry-run result is reviewed.

- [x] **Step 10: Migrate the existing manual quarantine out of the library**

Move `/mnt/f/Media/_quarantena` (58 GB: "O Negocio audio russo", "AHS corrupt") to `/mnt/d/quarantine/arr_server/` so nothing sits inside `${LIBRARY}`. Extract ~2-3 min sample clips first if keeping fixtures is desired; the full files can then be deleted at the user's discretion. Nothing is auto-deleted.

- [x] **Step 11: Commit**

```bash
git add media/import-gate/Dockerfile media/import-gate/.dockerignore \
        media/compose.yaml media/.env.example media/README.md
git commit -m "feat(import-gate): containerize, wire *arr webhooks, real-fixture e2e"
```

---

## Self-Review

**Spec coverage:**
- Integrity (ffprobe) → Task 3/4. Whisper language check → Task 3/4. Original-language rule → validator (Task 4). Webhook→sidecar topology → Task 6/7. CPU whisper → config + Dockerfile (no GPU). Quarantine+blocklist+re-search → Task 6. Loop guard N=3 → Task 6. Error-never-quarantine → validator `errored` + app (Task 4/6). Idempotency → app (Task 6). Layer-0 grab filter → Task 7 step 8. Real fixtures + migrate `_quarantena` → Task 7 steps 9-10. ntfy reason encoding → app + `_quarantine` folder name (Task 6). All covered.

**Placeholder scan:** No "TBD"/"handle edge cases"/"add validation" — each step has concrete code or an exact command. The two `<paste from ...config.xml>` markers are deliberate secret-injection points for the human, not code placeholders.

**Type consistency:** `Verdict(ok, reason, detail, errored)` used identically in Task 4 and Task 6. `validate_fn(path=, original_language_name=, expected_runtime_min=)` keyword signature matches between `app.py`'s production closure and `test_app.py`'s fake. `ArrClient` method names (`delete_moviefile`, `find_grab_history_id`, `mark_failed`) consistent across Task 5 and Task 6. `AttemptStore.get/increment` consistent across Task 2 and Task 6.

**Known verify-at-implementation point (from spec):** the exact Radarr/Sonarr semantics of `mark_failed` vs `delete_moviefile` ordering — Task 5/6 implement both; confirm on the live API during Task 7 step 9 that together they net out to (original removed, release blocklisted, search running).
