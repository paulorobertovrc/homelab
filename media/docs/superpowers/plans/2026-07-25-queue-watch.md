# queue-watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a polling thread to `import-gate` that clears queue items the qBit
extension guard leaves stranded, and kills grabs of episodes that have not aired yet —
both with blocklist + ntfy.

**Architecture:** Pure decision functions (`group_by_download_id`, `find_stuck`,
`find_preair`) with no I/O and no implicit clock, wrapped by a thin `QueueWatcher.run_once()`
that does the HTTP, and a `run_forever()` loop started as a daemon thread from `app.py`.
Flask `/health` keeps measuring only the web app, so a poller failure never marks the
container unhealthy nor touches import validation.

**Tech Stack:** Python 3.12, `requests`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-25-queue-watch-design.md`

## Global Constraints

- Work inside `import-gate/`. Run tests from that directory: `cd import-gate && python -m pytest tests/ -q`.
- `tests/conftest.py` already puts `import-gate/` on `sys.path` — import modules bare (`from queue_watch import ...`), never as a package.
- Reuse the existing `FakeResp` / `FakeSession` doubles style from `tests/test_arr_client.py`. No `requests-mock`, no network in tests.
- No `sleep()` in tests. Time enters every pure function as a `now` parameter.
- All datetimes are timezone-aware UTC. `airDateUtc` arrives as ISO-8601 ending in `Z`.
- Log via `logger = logging.getLogger(__name__)`, matching `app.py`.
- ntfy calls go through `notify.push(ntfy_url, title, tags, priority, message)` — signature is fixed, never raises.
- Portuguese user-facing ntfy text, English code comments and log lines — matches the existing codebase.
- Commit after each task with Conventional Commits + the co-author line.

## File Structure

| File | Responsibility |
|---|---|
| `import-gate/queue_watch.py` (new) | Pure gate logic + `QueueWatcher` + `run_forever` |
| `import-gate/arr_client.py` (modify) | Two new API methods: `get_queue`, `delete_queue_item` |
| `import-gate/config.py` (modify) | Six new settings + margin validation |
| `import-gate/app.py` (modify) | Start the daemon thread in `__main__` |
| `compose.yaml` (modify) | Six env vars on the `import-gate` service |
| `import-gate/tests/test_queue_watch.py` (new) | Gate logic + cycle tests |
| `import-gate/tests/test_arr_client.py` (modify) | Tests for the two new methods |
| `import-gate/tests/test_config.py` (new) | Settings defaults + margin validation |

---

### Task 1: ArrClient queue methods

**Files:**
- Modify: `import-gate/arr_client.py`
- Test: `import-gate/tests/test_arr_client.py`

**Interfaces:**
- Consumes: existing `ArrClient._req(method, path, **kw)`
- Produces:
  - `ArrClient.get_queue(include_episode: bool = False) -> list[dict]`
  - `ArrClient.delete_queue_item(queue_id: int, blocklist: bool = True) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `import-gate/tests/test_arr_client.py`:

```python
def test_get_queue_returns_records():
    s = FakeSession()
    s.responses[("GET", "http://radarr:7878/api/v3/queue")] = FakeResp(200, {
        "records": [{"id": 1, "downloadId": "ABC"}]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_arr_client.py -q`
Expected: FAIL — `AttributeError: 'ArrClient' object has no attribute 'get_queue'`

- [ ] **Step 3: Write minimal implementation**

Append to `import-gate/arr_client.py`:

```python
    def get_queue(self, include_episode: bool = False) -> list:
        params = {"pageSize": 200}
        if include_episode:
            # Sonarr only: brings EpisodeResource (with airDateUtc) inline,
            # which the pre-air gate needs.
            params["includeEpisode"] = "true"
        return self._req("GET", "/api/v3/queue", params=params).json().get("records", [])

    def delete_queue_item(self, queue_id: int, blocklist: bool = True) -> None:
        # blocklist=true is what stops the *arr from re-grabbing the same release
        # on the next RSS pass; without it this would loop.
        self._req("DELETE", f"/api/v3/queue/{queue_id}", params={
            "removeFromClient": "true",
            "blocklist": "true" if blocklist else "false",
            "skipRedownload": "false",
            "changeCategory": "false",
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_arr_client.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add import-gate/arr_client.py import-gate/tests/test_arr_client.py
git commit -m "feat(import-gate): add queue read/delete to ArrClient

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Settings for the watcher

**Files:**
- Modify: `import-gate/config.py`
- Test: `import-gate/tests/test_config.py` (create)

**Interfaces:**
- Produces: `Settings` gains six frozen fields —
  `queue_watch_enabled: bool`, `queue_watch_interval_min: int`,
  `queue_watch_min_age_min: int`, `queue_watch_max_per_cycle: int`,
  `queue_watch_preair_enabled: bool`, `queue_watch_preair_margin_h: int`.
  `Settings.from_env()` raises `ValueError` when the margin is below 1.

- [ ] **Step 1: Write the failing tests**

Create `import-gate/tests/test_config.py`:

```python
import pytest
from config import Settings

BASE_ENV = {"RADARR_API_KEY": "R", "SONARR_API_KEY": "S"}


def _env(monkeypatch, **overrides):
    for k in list(overrides) + list(BASE_ENV):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_queue_watch_defaults(monkeypatch):
    _env(monkeypatch)
    s = Settings.from_env()
    assert s.queue_watch_enabled is True
    assert s.queue_watch_interval_min == 10
    assert s.queue_watch_min_age_min == 15
    assert s.queue_watch_max_per_cycle == 3
    assert s.queue_watch_preair_enabled is True
    assert s.queue_watch_preair_margin_h == 24


def test_queue_watch_reads_overrides(monkeypatch):
    _env(monkeypatch, QUEUE_WATCH_ENABLED="false", QUEUE_WATCH_INTERVAL_MIN="5",
         QUEUE_WATCH_PREAIR_ENABLED="false", QUEUE_WATCH_PREAIR_MARGIN_H="48")
    s = Settings.from_env()
    assert s.queue_watch_enabled is False
    assert s.queue_watch_interval_min == 5
    assert s.queue_watch_preair_enabled is False
    assert s.queue_watch_preair_margin_h == 48


@pytest.mark.parametrize("value", ["0", "-1"])
def test_preair_margin_below_one_is_rejected(monkeypatch, value):
    """A zero margin is the aggressive mode that would blocklist legitimate
    releases appearing ~2h before airDateUtc. Fail loudly at startup instead."""
    _env(monkeypatch, QUEUE_WATCH_PREAIR_MARGIN_H=value)
    with pytest.raises(ValueError, match="QUEUE_WATCH_PREAIR_ENABLED"):
        Settings.from_env()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_config.py -q`
Expected: FAIL — `TypeError: Settings.__init__() got an unexpected keyword argument` or `AttributeError`

- [ ] **Step 3: Write minimal implementation**

In `import-gate/config.py`, add this helper above the `@dataclass` line:

```python
def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")
```

Add the six fields at the end of the `Settings` field list (after `skip_intro_fraction`):

```python
    queue_watch_enabled: bool
    queue_watch_interval_min: int
    queue_watch_min_age_min: int
    queue_watch_max_per_cycle: int
    queue_watch_preair_enabled: bool
    queue_watch_preair_margin_h: int
```

In `from_env`, immediately before the `return cls(` line:

```python
        preair_margin = int(os.environ.get("QUEUE_WATCH_PREAIR_MARGIN_H", "24"))
        if preair_margin < 1:
            raise ValueError(
                f"QUEUE_WATCH_PREAIR_MARGIN_H must be >= 1 (got {preair_margin}). "
                "To turn the pre-air gate off use QUEUE_WATCH_PREAIR_ENABLED=false; "
                "a zero margin would blocklist legitimate releases, which routinely "
                "appear a couple of hours before airDateUtc."
            )
```

And add to the `cls(...)` call, after `skip_intro_fraction=...`:

```python
            queue_watch_enabled=_env_bool("QUEUE_WATCH_ENABLED", "true"),
            queue_watch_interval_min=int(os.environ.get("QUEUE_WATCH_INTERVAL_MIN", "10")),
            queue_watch_min_age_min=int(os.environ.get("QUEUE_WATCH_MIN_AGE_MIN", "15")),
            queue_watch_max_per_cycle=int(os.environ.get("QUEUE_WATCH_MAX_PER_CYCLE", "3")),
            queue_watch_preair_enabled=_env_bool("QUEUE_WATCH_PREAIR_ENABLED", "true"),
            queue_watch_preair_margin_h=preair_margin,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_config.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole suite for regressions**

Run: `cd import-gate && python -m pytest tests/ -q`
Expected: PASS, no failures

- [ ] **Step 6: Commit**

```bash
git add import-gate/config.py import-gate/tests/test_config.py
git commit -m "feat(import-gate): queue-watch settings with margin guard

QUEUE_WATCH_PREAIR_MARGIN_H below 1 raises at startup. Zero would read as
'no margin' -- the aggressive mode that rejects legitimate releases appearing
hours before airDateUtc -- so disabling gets its own flag instead.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Grouping by downloadId

**Files:**
- Create: `import-gate/queue_watch.py`
- Test: `import-gate/tests/test_queue_watch.py` (create)

**Interfaces:**
- Produces: `group_by_download_id(records: list[dict]) -> dict[str, list[dict]]`

**Why this exists:** a Sonarr queue record is ONE episode, so a season pack is N records
sharing a `downloadId`. Counting records instead of groups would let a single 10-episode
pack blow the per-cycle cap of 3 by itself and wedge the watcher into permanent "anomaly"
mode, and would fire 10 DELETEs at one torrent (9 of them 404).

- [ ] **Step 1: Write the failing tests**

Create `import-gate/tests/test_queue_watch.py`:

```python
from queue_watch import group_by_download_id


def test_groups_records_sharing_a_download_id():
    records = [
        {"id": 1, "downloadId": "PACK"},
        {"id": 2, "downloadId": "PACK"},
        {"id": 3, "downloadId": "OTHER"},
    ]
    groups = group_by_download_id(records)
    assert set(groups) == {"PACK", "OTHER"}
    assert [r["id"] for r in groups["PACK"]] == [1, 2]
    assert [r["id"] for r in groups["OTHER"]] == [3]


def test_records_without_download_id_are_dropped():
    """No downloadId means no safe way to act on it."""
    assert group_by_download_id([{"id": 1}, {"id": 2, "downloadId": ""}]) == {}


def test_empty_input_gives_empty_groups():
    assert group_by_download_id([]) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'queue_watch'`

- [ ] **Step 3: Write minimal implementation**

Create `import-gate/queue_watch.py`:

```python
"""Queue gates.

Gate A ("stuck"): clears items the qBittorrent excluded_file_names guard strands.
When every file in a torrent is filtered out, qBit reports it complete at 0 bytes and
the *arr blocks forever on "No files found are eligible for import".

Gate B ("pre-air"): kills grabs of episodes that have not aired yet. Every such release
is fake by construction, and this catches the ones packaged as .mkv, which the extension
guard cannot see.
"""
import logging

logger = logging.getLogger(__name__)


def group_by_download_id(records: list) -> dict:
    """One queue record is one episode; a season pack is N records sharing a downloadId.

    Grouping is a correctness requirement, not an optimisation: the group is the unit of
    decision, of counting against the per-cycle cap, and of action.
    """
    groups = {}
    for record in records:
        download_id = record.get("downloadId")
        if not download_id:
            continue
        groups.setdefault(download_id, []).append(record)
    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add import-gate/queue_watch.py import-gate/tests/test_queue_watch.py
git commit -m "feat(import-gate): group queue records by downloadId

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Gate A — stuck items

**Files:**
- Modify: `import-gate/queue_watch.py`
- Test: `import-gate/tests/test_queue_watch.py`

**Interfaces:**
- Consumes: `group_by_download_id` (Task 3)
- Produces:
  - `NO_FILES_MSG: str` module constant
  - `find_stuck(groups: dict, now: datetime, first_seen: dict, min_age_min: int) -> tuple[list[dict], dict]`
  - Candidate dict shape: `{"download_id": str, "records": list[dict], "gates": ["stuck"]}`

`find_stuck` never mutates `first_seen`; it returns a fresh dict containing only the
downloadIds still stuck this round, which prunes orphan keys automatically.

- [ ] **Step 1: Write the failing tests**

Append to `import-gate/tests/test_queue_watch.py` (add the imports at the top of the file):

```python
from datetime import datetime, timedelta, timezone

from queue_watch import NO_FILES_MSG, find_stuck, group_by_download_id

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _stuck_record(rec_id=1, download_id="ABC"):
    return {
        "id": rec_id,
        "downloadId": download_id,
        "status": "completed",
        "title": "Silo S03E05 MULTI 1080p WEB H264-HiggsBoson",
        "indexer": "LimeTorrents (Prowlarr)",
        "statusMessages": [{
            "title": "Silo S03E05",
            "messages": [f"{NO_FILES_MSG} in /data/torrents/complete/Silo.scr"],
        }],
    }


def test_matching_record_becomes_candidate_once_mature():
    groups = group_by_download_id([_stuck_record()])
    seen = {"ABC": NOW - timedelta(minutes=20)}
    candidates, _ = find_stuck(groups, NOW, seen, 15)
    assert [c["download_id"] for c in candidates] == ["ABC"]
    assert candidates[0]["gates"] == ["stuck"]


def test_first_sighting_is_timed_but_not_returned():
    groups = group_by_download_id([_stuck_record()])
    candidates, new_seen = find_stuck(groups, NOW, {}, 15)
    assert candidates == []
    assert new_seen == {"ABC": NOW}


def test_record_without_the_message_is_ignored():
    record = _stuck_record()
    record["statusMessages"] = [{"title": "x", "messages": ["Something else entirely"]}]
    candidates, new_seen = find_stuck(group_by_download_id([record]), NOW, {}, 15)
    assert candidates == []
    assert new_seen == {}


def test_record_not_completed_is_ignored():
    record = _stuck_record()
    record["status"] = "downloading"
    candidates, _ = find_stuck(group_by_download_id([record]), NOW, {}, 15)
    assert candidates == []


def test_download_id_gone_from_queue_is_pruned():
    candidates, new_seen = find_stuck({}, NOW, {"OLD": NOW - timedelta(hours=5)}, 15)
    assert candidates == []
    assert "OLD" not in new_seen


def test_first_seen_argument_is_not_mutated():
    groups = group_by_download_id([_stuck_record()])
    original = {"OLD": NOW - timedelta(hours=5)}
    snapshot = dict(original)
    find_stuck(groups, NOW, original, 15)
    assert original == snapshot


def test_pack_with_message_on_one_record_still_matches():
    """A season pack blocks on whichever record Sonarr attached the message to."""
    quiet = _stuck_record(rec_id=2, download_id="ABC")
    quiet["statusMessages"] = []
    groups = group_by_download_id([_stuck_record(rec_id=1), quiet])
    candidates, _ = find_stuck(groups, NOW, {"ABC": NOW - timedelta(minutes=20)}, 15)
    assert len(candidates) == 1
    assert len(candidates[0]["records"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: FAIL — `ImportError: cannot import name 'NO_FILES_MSG'`

- [ ] **Step 3: Write minimal implementation**

Add to `import-gate/queue_watch.py` — the import at the top:

```python
from datetime import timedelta
```

and after `group_by_download_id`:

```python
# Matched on the message rather than trackedDownloadState: Sonarr alternates between
# importPending and importBlocked across versions, while this string is stable.
NO_FILES_MSG = "No files found are eligible for import"


def _is_stuck_record(record: dict) -> bool:
    if record.get("status") != "completed":
        return False
    for status_message in record.get("statusMessages") or []:
        for message in status_message.get("messages") or []:
            if NO_FILES_MSG in message:
                return True
    return False


def find_stuck(groups: dict, now, first_seen: dict, min_age_min: int):
    """Groups stuck for at least min_age_min, plus the new first-seen map.

    Returns a fresh first_seen instead of mutating the argument: keeps the function pure
    and makes orphan pruning observable in a test rather than a side effect. Uses
    first-sighting rather than the record's `added` field because what matters is how long
    the item has been *stuck*, not how long it has been queued.
    """
    candidates = []
    new_first_seen = {}
    for download_id, records in groups.items():
        if not any(_is_stuck_record(r) for r in records):
            continue
        seen_at = first_seen.get(download_id, now)
        new_first_seen[download_id] = seen_at
        if now - seen_at >= timedelta(minutes=min_age_min):
            candidates.append({
                "download_id": download_id,
                "records": records,
                "gates": ["stuck"],
            })
    return candidates, new_first_seen
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add import-gate/queue_watch.py import-gate/tests/test_queue_watch.py
git commit -m "feat(import-gate): gate A detects stuck 'no files eligible' groups

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Gate B — pre-air

**Files:**
- Modify: `import-gate/queue_watch.py`
- Test: `import-gate/tests/test_queue_watch.py`

**Interfaces:**
- Consumes: `group_by_download_id` (Task 3)
- Produces: `find_preair(groups: dict, now: datetime, margin_h: int) -> list[dict]`
  - Candidate dict shape: `{"download_id": str, "records": list[dict], "gates": ["preair"], "hours_early": float}`

**The rule, and why:** a group is pre-air only when **every** record's
`episode.airDateUtc` is beyond `now + margin_h`. Measured against 230 real grabs,
legitimate WEB-DL routinely appears ~2h before `airDateUtc` while the known fakes ran
116–158h early — so a naive "future airDate" rule would reject good releases. The
`all()` quantifier is also what protects season packs: an in-flight season always has at
least one aired episode. Missing `episode` or missing `airDateUtc` never authorises action.

- [ ] **Step 1: Write the failing tests**

Append to `import-gate/tests/test_queue_watch.py` (extend the `queue_watch` import with `find_preair`):

```python
def _preair_record(hours_ahead, rec_id=1, download_id="ABC"):
    return {
        "id": rec_id,
        "downloadId": download_id,
        "status": "downloading",
        "title": "Silo S03E05 MULTI 1080p WEB H264-HiggsBoson",
        "indexer": "LimeTorrents (Prowlarr)",
        "episode": {
            "airDateUtc": (NOW + timedelta(hours=hours_ahead))
                          .isoformat().replace("+00:00", "Z"),
        },
    }


def test_real_fake_158h_early_is_caught():
    """The actual Silo S03E05 .scr case."""
    candidates = find_preair(group_by_download_id([_preair_record(158)]), NOW, 24)
    assert [c["download_id"] for c in candidates] == ["ABC"]
    assert candidates[0]["gates"] == ["preair"]
    assert candidates[0]["hours_early"] == 158.0


def test_legitimate_release_2h_early_is_spared():
    """Regression guard: Silo S03E04 CAKES was grabbed 2.1h early and is in the library.
    A naive 'airDateUtc in the future' rule would have blocklisted it."""
    assert find_preair(group_by_download_id([_preair_record(2.1)]), NOW, 24) == []


def test_exactly_on_the_margin_is_spared():
    assert find_preair(group_by_download_id([_preair_record(24)]), NOW, 24) == []


def test_mixed_pack_with_one_aired_episode_is_spared():
    """An in-flight season pack always has an aired episode; only an all-future group is fake."""
    records = [_preair_record(158, rec_id=1), _preair_record(-48, rec_id=2)]
    assert find_preair(group_by_download_id(records), NOW, 24) == []


def test_pack_where_every_episode_is_preair_is_caught():
    records = [_preair_record(158, rec_id=1), _preair_record(182, rec_id=2)]
    candidates = find_preair(group_by_download_id(records), NOW, 24)
    assert len(candidates) == 1
    assert candidates[0]["hours_early"] == 158.0  # reports the earliest


def test_record_without_episode_is_spared():
    record = _preair_record(158)
    del record["episode"]
    assert find_preair(group_by_download_id([record]), NOW, 24) == []


def test_record_without_air_date_is_spared():
    record = _preair_record(158)
    record["episode"] = {}
    assert find_preair(group_by_download_id([record]), NOW, 24) == []


def test_unparseable_air_date_is_spared():
    record = _preair_record(158)
    record["episode"]["airDateUtc"] = "not a date"
    assert find_preair(group_by_download_id([record]), NOW, 24) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: FAIL — `ImportError: cannot import name 'find_preair'`

- [ ] **Step 3: Write minimal implementation**

Extend the datetime import in `import-gate/queue_watch.py`:

```python
from datetime import datetime, timedelta
```

and append:

```python
def _parse_air_date(value):
    """Returns an aware datetime, or None when the field is missing or malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def find_preair(groups: dict, now, margin_h: int):
    """Groups whose every episode airs beyond now + margin_h.

    The margin is the rule, not a tuning knob: measured over 230 grabs, legitimate
    releases appeared up to 2.1h before airDateUtc while known fakes ran 116-158h early.
    `all()` rather than `any()` protects season packs. A missing or unparseable air date
    spares the whole group -- absent data never authorises action.
    """
    threshold = now + timedelta(hours=margin_h)
    candidates = []
    for download_id, records in groups.items():
        air_dates = [_parse_air_date((r.get("episode") or {}).get("airDateUtc"))
                     for r in records]
        if not air_dates or any(a is None for a in air_dates):
            continue
        if all(a > threshold for a in air_dates):
            earliest = min(air_dates)
            candidates.append({
                "download_id": download_id,
                "records": records,
                "gates": ["preair"],
                "hours_early": round((earliest - now).total_seconds() / 3600, 1),
            })
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add import-gate/queue_watch.py import-gate/tests/test_queue_watch.py
git commit -m "feat(import-gate): gate B kills pre-air grabs

Margin of 24h derived from 230 measured grabs: legitimate releases show up to
2.1h before airDateUtc, known fakes 116-158h. Test suite pins the 2.1h case as
a regression guard against the naive rule.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The cycle — cap, dedup, action, notification

**Files:**
- Modify: `import-gate/queue_watch.py`
- Test: `import-gate/tests/test_queue_watch.py`

**Interfaces:**
- Consumes: `find_stuck`, `find_preair`, `group_by_download_id`; `ArrClient.get_queue` and
  `ArrClient.delete_queue_item` (Task 1); `Settings` fields (Task 2);
  `notify.push(url, title, tags, priority, message)`
- Produces: `QueueWatcher(settings, sonarr, radarr, notify_fn, now_fn=None)` with
  `run_once() -> list[dict]` returning the candidates acted on.

- [ ] **Step 1: Write the failing tests**

Append to `import-gate/tests/test_queue_watch.py` (extend the import with `QueueWatcher`):

```python
class FakeArr:
    def __init__(self, records=None, fail=False):
        self.records = records or []
        self.fail = fail
        self.deleted = []
        self.include_episode = None

    def get_queue(self, include_episode=False):
        if self.fail:
            raise RuntimeError("connection refused")
        self.include_episode = include_episode
        return self.records

    def delete_queue_item(self, queue_id, blocklist=True):
        self.deleted.append((queue_id, blocklist))


class FakeSettings:
    ntfy_url = "http://ntfy/arr-media"
    queue_watch_min_age_min = 15
    queue_watch_max_per_cycle = 3
    queue_watch_preair_enabled = True
    queue_watch_preair_margin_h = 24


def _watcher(sonarr, radarr, settings=None, notes=None):
    settings = settings or FakeSettings()
    notes = notes if notes is not None else []

    def notify(url, title, tags, priority, message):
        notes.append((title, priority, message))

    return QueueWatcher(settings, sonarr, radarr, notify, now_fn=lambda: NOW), notes


def test_mature_stuck_group_is_removed_and_notified():
    sonarr = FakeArr([_stuck_record()])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()                     # first sighting only
    assert sonarr.deleted == []
    watcher._now = lambda: NOW + timedelta(minutes=20)
    acted = watcher.run_once()
    assert sonarr.deleted == [(1, True)]
    assert len(acted) == 1
    assert len(notes) == 1
    assert "LimeTorrents" in notes[0][2]


def test_preair_group_is_removed_on_the_first_cycle():
    """No minimum-age wait: the signal is a date, and acting early saves the bandwidth."""
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == [(1, True)]
    assert "158" in notes[0][2]


def test_sonarr_queue_is_fetched_with_episode_data():
    sonarr = FakeArr()
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.include_episode is True


def test_preair_gate_is_not_applied_to_radarr():
    """Radarr has native Minimum Availability; the gate is Sonarr-only."""
    radarr = FakeArr([_preair_record(158)])
    watcher, _ = _watcher(FakeArr(), radarr)
    watcher.run_once()
    assert radarr.deleted == []


def test_pack_of_many_records_gets_exactly_one_delete():
    records = [_preair_record(158, rec_id=n, download_id="PACK") for n in (7, 3, 9)]
    sonarr = FakeArr(records)
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == [(3, True)]          # lowest id, once


def test_group_tripping_both_gates_acts_once():
    record = _preair_record(158)
    record["status"] = "completed"
    record["statusMessages"] = [{"title": "t", "messages": [NO_FILES_MSG]}]
    sonarr = FakeArr([record])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(minutes=30)}}
    watcher.run_once()
    assert len(sonarr.deleted) == 1
    assert len(notes) == 1


def test_above_cap_acts_on_nothing_and_notifies_once():
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in range(1, 6)]
    sonarr = FakeArr(records)
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == []
    assert len(notes) == 1
    assert notes[0][1] == 5                        # high priority
    watcher.run_once()                             # still anomalous
    assert len(notes) == 1                         # not repeated


def test_anomaly_flag_resets_when_count_returns_to_normal():
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in range(1, 6)]
    sonarr = FakeArr(records)
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert len(notes) == 1
    sonarr.records = []
    watcher.run_once()
    sonarr.records = records
    watcher.run_once()
    assert len(notes) == 2                         # notifies again


def test_one_app_down_aborts_the_cycle_without_acting():
    sonarr = FakeArr([_preair_record(158)])
    watcher, _ = _watcher(sonarr, FakeArr(fail=True))
    with pytest.raises(RuntimeError):
        watcher.run_once()
    assert sonarr.deleted == []


def test_preair_disabled_leaves_gate_a_working():
    class NoPreair(FakeSettings):
        queue_watch_preair_enabled = False

    sonarr = FakeArr([_preair_record(158), _stuck_record(rec_id=50, download_id="STUCK")])
    watcher, _ = _watcher(sonarr, FakeArr(), settings=NoPreair())
    watcher._first_seen = {"sonarr": {"STUCK": NOW - timedelta(minutes=30)}}
    watcher.run_once()
    assert sonarr.deleted == [(50, True)]


def test_delete_failure_does_not_abort_remaining_candidates():
    class Flaky(FakeArr):
        def delete_queue_item(self, queue_id, blocklist=True):
            if queue_id == 1:
                raise RuntimeError("boom")
            self.deleted.append((queue_id, blocklist))

    sonarr = Flaky([_preair_record(158, rec_id=1, download_id="A"),
                    _preair_record(158, rec_id=2, download_id="B")])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == [(2, True)]
```

Add `import pytest` to the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: FAIL — `ImportError: cannot import name 'QueueWatcher'`

- [ ] **Step 3: Write minimal implementation**

Extend the datetime import in `import-gate/queue_watch.py`:

```python
from datetime import datetime, timedelta, timezone
```

and append:

```python
class QueueWatcher:
    """Runs both gates over the Sonarr and Radarr queues.

    State lives in memory on purpose: a container restart zeroes the clock and makes the
    watcher wait out the minimum age again, which is the safe direction to fail.
    """

    def __init__(self, settings, sonarr, radarr, notify_fn, now_fn=None):
        self._settings = settings
        self._sonarr = sonarr
        self._radarr = radarr
        self._notify = notify_fn
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._first_seen = {"sonarr": {}, "radarr": {}}
        self._anomaly_notified = False

    def run_once(self) -> list:
        now = self._now()
        settings = self._settings

        # Both queues are collected before anything is evaluated. A systemic failure hits
        # both apps at once, so acting on a partial view is exactly what the cap exists to
        # prevent; if either call raises, the whole cycle aborts having done nothing.
        queues = [
            ("sonarr", self._sonarr, self._sonarr.get_queue(include_episode=True)),
            ("radarr", self._radarr, self._radarr.get_queue()),
        ]

        candidates = []
        new_first_seen = {}
        for kind, client, records in queues:
            groups = group_by_download_id(records)
            stuck, seen = find_stuck(groups, now, self._first_seen.get(kind, {}),
                                     settings.queue_watch_min_age_min)
            new_first_seen[kind] = seen
            found = list(stuck)
            # Gate B is Sonarr-only: Radarr's native Minimum Availability covers movies.
            if kind == "sonarr" and settings.queue_watch_preair_enabled:
                found += find_preair(groups, now, settings.queue_watch_preair_margin_h)
            for candidate in found:
                candidate["kind"] = kind
                candidate["client"] = client
            candidates += found
        self._first_seen = new_first_seen

        candidates = _merge_by_group(candidates)

        cap = settings.queue_watch_max_per_cycle
        if len(candidates) > cap:
            logger.error("queue-watch: %d candidates exceed cap %d; taking no action",
                         len(candidates), cap)
            if not self._anomaly_notified:
                self._notify(
                    settings.ntfy_url, "⚠️ queue-watch: anomalia em massa", "no_entry", 5,
                    f"{len(candidates)} itens da fila viraram candidatos a remoção num "
                    f"único ciclo (teto {cap}). Nenhuma ação tomada — isso costuma ser "
                    f"falha sistêmica (disco cheio ou desmontado). Verifique à mão.",
                )
                self._anomaly_notified = True
            return []
        self._anomaly_notified = False

        acted = []
        for candidate in candidates:
            if self._act(candidate):
                acted.append(candidate)
        return acted

    def _act(self, candidate) -> bool:
        records = candidate["records"]
        # removeFromClient drops the whole torrent, so the sibling records of a season
        # pack vanish with it -- one DELETE per group, not per record.
        queue_id = min(r["id"] for r in records)
        title = records[0].get("title", "?")
        indexer = records[0].get("indexer") or "?"
        try:
            candidate["client"].delete_queue_item(queue_id)
        except Exception as e:
            logger.error("queue-watch: could not remove %s: %s", title, e)
            return False

        if "preair" in candidate["gates"]:
            reason = (f"episódio ainda não exibido "
                      f"({candidate.get('hours_early')}h de antecedência)")
            heading = "🚫 queue-watch: grab pre-air"
        else:
            reason = "nenhum arquivo elegível para import (payload barrado)"
            heading = "🧹 queue-watch: fila destravada"
        message = f"{title}\nIndexer: {indexer}\nMotivo: {reason}\nRemovido + blocklist."
        logger.warning("queue-watch: removed %s (%s) via %s",
                       title, indexer, "+".join(candidate["gates"]))
        self._notify(self._settings.ntfy_url, heading, "lock", 4, message)
        return True


def _merge_by_group(candidates: list) -> list:
    """One group tripping both gates is one action and counts once against the cap."""
    merged = {}
    for candidate in candidates:
        key = (candidate["kind"], candidate["download_id"])
        if key in merged:
            merged[key]["gates"] += candidate["gates"]
            if "hours_early" in candidate:
                merged[key].setdefault("hours_early", candidate["hours_early"])
        else:
            merged[key] = candidate
    return list(merged.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: PASS (29 tests)

- [ ] **Step 5: Commit**

```bash
git add import-gate/queue_watch.py import-gate/tests/test_queue_watch.py
git commit -m "feat(import-gate): queue-watch cycle with cap, dedup and ntfy

Cap counts groups, not records: a 10-episode season pack would otherwise blow
a cap of 3 by itself and wedge the watcher into permanent anomaly mode.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Thread, wiring and compose

**Files:**
- Modify: `import-gate/queue_watch.py`
- Modify: `import-gate/app.py:128-165` (the `__main__` block)
- Modify: `compose.yaml:357-364` (import-gate `environment:`)
- Test: `import-gate/tests/test_queue_watch.py`

**Interfaces:**
- Consumes: `QueueWatcher.run_once` (Task 6), `Settings.queue_watch_*` (Task 2)
- Produces: `run_forever(watcher, interval_min, sleep_fn=time.sleep) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `import-gate/tests/test_queue_watch.py` (extend the import with `run_forever`):

```python
def test_run_forever_survives_a_failing_cycle():
    """A cycle that raises must not kill the thread."""
    calls = []

    class Boom:
        def run_once(self):
            calls.append(1)
            raise RuntimeError("sonarr down")

    def sleep_fn(_seconds):
        if len(calls) >= 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_forever(Boom(), 10, sleep_fn=sleep_fn)
    assert len(calls) == 3


def test_run_forever_sleeps_the_configured_interval():
    slept = []

    class Once:
        def run_once(self):
            return []

    def sleep_fn(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_forever(Once(), 10, sleep_fn=sleep_fn)
    assert slept == [600]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_forever'`

- [ ] **Step 3: Write minimal implementation**

Add `import time` at the top of `import-gate/queue_watch.py` and append:

```python
def run_forever(watcher, interval_min: int, sleep_fn=time.sleep) -> None:
    """Poll loop. Any cycle failure is logged and swallowed so the thread survives."""
    while True:
        try:
            watcher.run_once()
        except Exception as e:
            logger.error("queue-watch: cycle failed: %s", e)
        sleep_fn(interval_min * 60)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd import-gate && python -m pytest tests/test_queue_watch.py -q`
Expected: PASS (31 tests)

- [ ] **Step 5: Wire it into app.py**

In `import-gate/app.py`, add `import threading` to the imports at the top.

Replace the `application = create_app(...)` block in `__main__` with:

```python
    radarr_client = ArrClient(s.radarr_url, s.radarr_key, "radarr")
    sonarr_client = ArrClient(s.sonarr_url, s.sonarr_key, "sonarr")

    application = create_app(
        s,
        radarr_client,
        sonarr_client,
        AttemptStore(os.path.join(s.state_dir, "attempts.db")),
        validate_fn, _push,
    )

    # Queue gates run in a daemon thread. /health deliberately keeps measuring only the
    # Flask app: a poller failure must never mark the container unhealthy nor disturb
    # import validation, which is the more valuable defence.
    if s.queue_watch_enabled:
        from queue_watch import QueueWatcher, run_forever
        watcher = QueueWatcher(s, sonarr_client, radarr_client, _push)
        threading.Thread(
            target=run_forever,
            args=(watcher, s.queue_watch_interval_min),
            daemon=True,
            name="queue-watch",
        ).start()
        logger.info("queue-watch: started (every %d min, min age %d min, cap %d, "
                    "pre-air %s margin %dh)",
                    s.queue_watch_interval_min, s.queue_watch_min_age_min,
                    s.queue_watch_max_per_cycle,
                    "on" if s.queue_watch_preair_enabled else "off",
                    s.queue_watch_preair_margin_h)
    else:
        logger.info("queue-watch: disabled by QUEUE_WATCH_ENABLED")
```

- [ ] **Step 6: Add the env vars to compose.yaml**

In `compose.yaml`, in the `import-gate` service's `environment:` list, after the
`HF_HOME` line:

```yaml
      - QUEUE_WATCH_ENABLED=${QUEUE_WATCH_ENABLED:-true}
      - QUEUE_WATCH_INTERVAL_MIN=${QUEUE_WATCH_INTERVAL_MIN:-10}
      - QUEUE_WATCH_MIN_AGE_MIN=${QUEUE_WATCH_MIN_AGE_MIN:-15}
      - QUEUE_WATCH_MAX_PER_CYCLE=${QUEUE_WATCH_MAX_PER_CYCLE:-3}
      - QUEUE_WATCH_PREAIR_ENABLED=${QUEUE_WATCH_PREAIR_ENABLED:-true}
      - QUEUE_WATCH_PREAIR_MARGIN_H=${QUEUE_WATCH_PREAIR_MARGIN_H:-24}
```

- [ ] **Step 7: Verify compose parses and the suite is green**

```bash
cd /home/prvrc/dev/homelab/media
docker compose config --quiet && echo "compose OK"
cd import-gate && python -m pytest tests/ -q
```
Expected: `compose OK`, and the full suite passes.

- [ ] **Step 8: Commit**

```bash
git add import-gate/queue_watch.py import-gate/app.py import-gate/tests/test_queue_watch.py compose.yaml
git commit -m "feat(import-gate): start queue-watch thread, expose env knobs

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Live deploy and verification

**Files:**
- Modify: `README.md` (the fake-release guard bullet)

No new tests — this task proves the thing runs against the real stack.

- [ ] **Step 1: Rebuild and restart the container**

```bash
cd /home/prvrc/dev/homelab/media
docker compose up -d --build import-gate
```

- [ ] **Step 2: Confirm the thread started and the container is healthy**

```bash
docker logs import-gate --since 2m 2>&1 | grep -i "queue-watch"
docker inspect --format '{{.State.Health.Status}}' import-gate
```
Expected: a `queue-watch: started (every 10 min, ...)` line, and `healthy`.

- [ ] **Step 3: Prove the margin guard fails loudly**

```bash
docker run --rm --entrypoint python \
  -e RADARR_API_KEY=x -e SONARR_API_KEY=x -e QUEUE_WATCH_PREAIR_MARGIN_H=0 \
  media-import-gate -c "from config import Settings; Settings.from_env()"
```
Expected: exits non-zero with the `ValueError` naming `QUEUE_WATCH_PREAIR_ENABLED`.
(If the image tag differs, read it from `docker compose config | grep -A2 import-gate`.)

- [ ] **Step 4: Prove the kill switch actually stops the thread**

```bash
cd /home/prvrc/dev/homelab/media
QUEUE_WATCH_ENABLED=false docker compose up -d import-gate
docker logs import-gate --since 1m 2>&1 | grep -i "queue-watch"
```
Expected: the line `queue-watch: disabled by QUEUE_WATCH_ENABLED` and **no** `started` line.
Then restore the default:

```bash
docker compose up -d import-gate
docker logs import-gate --since 1m 2>&1 | grep -i "queue-watch: started"
```

- [ ] **Step 5: Watch one full cycle against the live queue**

```bash
sleep 660 && docker logs import-gate --since 12m 2>&1 | grep -i "queue-watch"
```
Expected: no errors. With a healthy queue there is nothing to act on, so silence past the
startup line is the correct result. **Record what was actually observed in this plan file**
— do not claim success without the output.

- [ ] **Step 6: Update the README**

In `README.md`, in the fake-release guard bullet, replace this sentence:

```markdown
  Automating the cleanup, plus a second gate that kills
  grabs of episodes that have not aired yet, is specced in
  `docs/superpowers/specs/2026-07-25-queue-watch-design.md`.
```

with:

```markdown
  Both of those are now automated by **queue-watch** (a poller inside
  `import-gate`, specced in
  `docs/superpowers/specs/2026-07-25-queue-watch-design.md`): gate A removes
  and blocklists queue items stuck on "no files eligible" after 15 min, and
  gate B kills grabs of episodes airing more than 24h out. That margin is
  measured, not guessed — legitimate WEB-DL shows up to ~2h before
  `airDateUtc`, while the known fakes ran 116–158h early. Both gates notify
  over ntfy with the source indexer, and both refuse to act at all when more
  than 3 groups qualify in one cycle, since that pattern means a systemic
  failure (full or unmounted disk) rather than bad releases.
```

- [ ] **Step 7: Commit**

```bash
git add README.md docs/superpowers/plans/2026-07-25-queue-watch.md
git commit -m "docs(media): queue-watch live, record verification output

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Deferred (from the spec, do not build)

- Other queue stall modes (`stalled with no connections`, aged `importPending`).
- Querying qBit to confirm which extension was blocked.
- Persisting `first_seen` to disk.
- Intercepting the grab instead of the queue.
- Automatic indexer reputation scoring.
