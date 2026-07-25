from datetime import datetime, timedelta, timezone

import pytest

from queue_watch import (NO_FILES_MSG, QueueWatcher, find_preair, find_stuck,
                          group_by_download_id, run_forever)

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


def test_records_without_id_are_dropped():
    """The action picks min(record['id']); an id-less record would raise KeyError there,
    outside the per-candidate try, aborting the cycle and skipping every other candidate."""
    assert group_by_download_id([{"downloadId": "ABC"}]) == {}


def test_one_id_less_record_discards_its_whole_group():
    """Dropping just the bad record would leave a PARTIAL group -- and the pre-air gate's
    all() over a subset is precisely the failure pagination exists to prevent. Proven
    adversarially: a pack whose only aired episode lacks an id looks entirely pre-air and
    gets destroyed. Either the group is whole or it is not considered."""
    records = [{"id": 1, "downloadId": "PACK"}, {"downloadId": "PACK"}]
    assert group_by_download_id(records) == {}


def test_empty_input_gives_empty_groups():
    assert group_by_download_id([]) == {}


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


def test_naive_air_date_is_spared_not_crashed():
    """A timezone-less timestamp parses fine but cannot be compared to an aware `now`:
    Python raises TypeError, which would escape find_preair and abort the whole cycle.
    Verified in a prototype. Treat it as unparseable instead."""
    record = _preair_record(158)
    record["episode"]["airDateUtc"] = "2026-07-31T04:00:00"
    assert find_preair(group_by_download_id([record]), NOW, 24) == []


def test_date_only_air_date_is_spared_not_crashed():
    """Sonarr also carries a plain `airDate` (date, no time). If that ever lands in this
    field it parses to a naive midnight -- same TypeError, same handling."""
    record = _preair_record(158)
    record["episode"]["airDateUtc"] = "2026-07-31"
    assert find_preair(group_by_download_id([record]), NOW, 24) == []


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

    def delete_queue_item(self, queue_id, blocklist=True, skip_redownload=False):
        self.deleted.append((queue_id, blocklist, skip_redownload))


class FakeSettings:
    ntfy_url = "http://ntfy/arr-media"
    queue_watch_min_age_min = 15
    queue_watch_max_per_cycle = 3
    queue_watch_preair_enabled = True
    queue_watch_preair_margin_h = 24
    queue_watch_dry_run = False        # the armed behaviour is what most tests assert


class DryRun(FakeSettings):
    queue_watch_dry_run = True


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
    assert sonarr.deleted == [(1, True, False)]     # gate A wants the re-search
    assert len(acted) == 1
    assert len(notes) == 1
    assert "LimeTorrents" in notes[0][2]


def test_preair_group_is_removed_on_the_first_cycle():
    """No minimum-age wait: the signal is a date, and acting early saves the bandwidth."""
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == [(1, True, True)]
    assert "158" in notes[0][2]


def test_preair_skips_redownload_but_gate_a_does_not():
    """Forcing an immediate re-search for an episode that does not exist yet can only
    turn up another fake, feeding the loop. Gate A's re-search, by contrast, is the point:
    a real release for that episode probably exists."""
    preair = FakeArr([_preair_record(158)])
    watcher, _ = _watcher(preair, FakeArr())
    watcher.run_once()
    assert preair.deleted[0][2] is True

    stuck = FakeArr([_stuck_record()])
    watcher, _ = _watcher(stuck, FakeArr())
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(minutes=30)}}
    watcher.run_once()
    assert stuck.deleted[0][2] is False


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
    assert sonarr.deleted == [(3, True, True)]     # lowest id, once


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
    assert sonarr.deleted == [(50, True, False)]


def test_delete_failure_does_not_abort_remaining_candidates():
    class Flaky(FakeArr):
        def delete_queue_item(self, queue_id, blocklist=True, skip_redownload=False):
            if queue_id == 1:
                raise RuntimeError("boom")
            self.deleted.append((queue_id, blocklist, skip_redownload))

    sonarr = Flaky([_preair_record(158, rec_id=1, download_id="A"),
                    _preair_record(158, rec_id=2, download_id="B")])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    assert sonarr.deleted == [(2, True, True)]


def test_dry_run_notifies_but_deletes_nothing():
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr(), settings=DryRun())
    watcher.run_once()
    assert sonarr.deleted == []
    assert len(notes) == 1
    assert "[SIMULAÇÃO]" in notes[0][0]
    assert "NADA foi removido" in notes[0][2]


def test_dry_run_does_not_re_notify_the_same_item():
    """The item stays queued, so it is a candidate every cycle. Without dedup this would
    fire every 10 minutes and the mode would be unusable."""
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr(), settings=DryRun())
    watcher.run_once()
    watcher.run_once()
    watcher.run_once()
    assert len(notes) == 1


def test_dry_run_reports_again_after_the_item_leaves_and_returns():
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr(), settings=DryRun())
    watcher.run_once()
    sonarr.records = []
    watcher.run_once()
    sonarr.records = [_preair_record(158)]
    watcher.run_once()
    assert len(notes) == 2


def test_dry_run_still_respects_the_cap():
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in range(1, 6)]
    watcher, notes = _watcher(FakeArr(records), FakeArr(), settings=DryRun())
    watcher.run_once()
    assert len(notes) == 1
    assert "anomalia" in notes[0][0]


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
