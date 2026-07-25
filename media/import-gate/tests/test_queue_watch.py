import logging
from datetime import datetime, timedelta, timezone

import pytest

from queue_watch import (NO_FILES_MSG, QueueWatcher, find_preair, find_stuck,
                          group_by_download_id, run_forever)

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _stuck_record(rec_id=1, download_id="ABC", episode_id=None):
    return {
        "id": rec_id,
        "downloadId": download_id,
        "episodeId": rec_id * 100 if episode_id is None else episode_id,
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
    candidates, new_seen = find_stuck(group_by_download_id([record]), NOW, {}, 15)
    assert candidates == []
    # new_seen is the assertion that actually pins the status filter. With an empty
    # first_seen, `candidates == []` holds either way (seen_at becomes now, so the
    # item is never mature on its first sighting) -- so without this line the whole
    # `status != "completed"` check could be deleted with the suite still green.
    assert new_seen == {}


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


def _preair_record(hours_ahead, rec_id=1, download_id="ABC", episode_id=None):
    return {
        "id": rec_id,
        "downloadId": download_id,
        "episodeId": rec_id * 100 if episode_id is None else episode_id,
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
    # Deliberately NOT the production defaults (15/3/24). When the fake carries the
    # same numbers the code would hardcode, a test that reads a literal is
    # indistinguishable from one that reads the setting -- mutation testing proved
    # all three knobs could be replaced by constants with the whole suite still green.
    ntfy_url = "http://ntfy/arr-media"
    max_attempts = 3                   # shared with the webhook self-heal
    queue_watch_interval_min = 10      # the backwards-jump tolerance
    queue_watch_min_age_min = 45
    queue_watch_max_per_cycle = 2
    queue_watch_preair_enabled = True
    queue_watch_preair_margin_h = 72
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
    watcher._now = lambda: NOW + timedelta(minutes=50)
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
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(minutes=60)}}
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
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(minutes=60)}}
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
    watcher._first_seen = {"sonarr": {"STUCK": NOW - timedelta(minutes=60)}}
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


# --- the knobs are actually read, and the boundaries hold ---------------------
# FakeSettings deliberately carries non-default values (45/2/72). These tests fail
# if any of the three is replaced by a hardcoded literal -- which mutation testing
# showed was possible for all three with the entire suite still green.

def test_cap_comes_from_settings_not_a_literal():
    """Turning the cap down before arming is the obvious safety move; nothing proved
    the knob was connected."""
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in (1, 2, 3)]
    sonarr = FakeArr(records)
    watcher, notes = _watcher(sonarr, FakeArr())          # cap = 2
    watcher.run_once()
    assert sonarr.deleted == []
    assert "anomalia" in notes[0][0]


def test_exactly_at_the_cap_still_acts():
    """The boundary the cap turns on. `> cap` vs `>= cap` was unpinned."""
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in (1, 2)]
    sonarr = FakeArr(records)
    watcher, notes = _watcher(sonarr, FakeArr())          # cap = 2, exactly 2 groups
    acted = watcher.run_once()
    assert len(acted) == 2
    assert sorted(d[0] for d in sonarr.deleted) == [1, 2]
    assert not any("anomalia" in n[0] for n in notes)


def test_min_age_comes_from_settings_not_a_literal():
    sonarr = FakeArr([_stuck_record()])
    watcher, _ = _watcher(sonarr, FakeArr())              # min age = 45
    watcher.run_once()                                    # first sighting
    watcher._now = lambda: NOW + timedelta(minutes=20)     # past 15, short of 45
    watcher.run_once()
    assert sonarr.deleted == []


def test_exactly_at_the_min_age_acts():
    """`>=` vs `>` on the maturity comparison was unpinned."""
    sonarr = FakeArr([_stuck_record()])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    watcher._now = lambda: NOW + timedelta(minutes=45)
    watcher.run_once()
    assert sonarr.deleted == [(1, True, False)]


def test_preair_margin_comes_from_settings_not_a_literal():
    """A 48h-early grab is caught under the production default of 24h and spared
    under this fake's 72h. A hardcoded 24 would destroy it."""
    sonarr = FakeArr([_preair_record(48)])
    watcher, _ = _watcher(sonarr, FakeArr())              # margin = 72
    watcher.run_once()
    assert sonarr.deleted == []


# --- the Radarr action path ---------------------------------------------------

def test_stuck_radarr_group_is_deleted_via_the_radarr_client():
    """No test ever deleted a Radarr queue item, so routing every DELETE through the
    Sonarr client passed the whole suite -- and a queue id that exists in both apps
    would then remove the WRONG item. This is the path that destroys movie torrents."""
    radarr = FakeArr([_stuck_record(rec_id=99, download_id="MOVIE")])
    sonarr = FakeArr()
    watcher, notes = _watcher(sonarr, radarr)
    watcher._first_seen = {"radarr": {"MOVIE": NOW - timedelta(minutes=60)}}
    watcher.run_once()
    assert radarr.deleted == [(99, True, False)]
    assert sonarr.deleted == []
    assert len(notes) == 1


def test_radarr_queue_is_fetched_without_episode_data():
    _s, radarr = FakeArr(), FakeArr()
    watcher, _ = _watcher(_s, radarr)
    watcher.run_once()
    assert radarr.include_episode is False


# --- a group tripping both gates: which treatment wins ------------------------

def test_both_gates_group_takes_the_preair_treatment():
    """Asserting only the counts left the actual decision unpinned. Forcing a
    re-search for an episode that does not exist yet can only surface another fake,
    so pre-air's skipRedownload must win over gate A's re-search."""
    record = _preair_record(158)
    record["status"] = "completed"
    record["statusMessages"] = [{"title": "t", "messages": [NO_FILES_MSG]}]
    sonarr = FakeArr([record])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(minutes=60)}}
    watcher.run_once()
    assert sonarr.deleted == [(1, True, True)]      # skip_redownload wins
    assert "pre-air" in notes[0][0]                 # and so does the heading


# --- gate A's match must not be loosened --------------------------------------

def test_a_different_sonarr_message_does_not_match():
    """Guards the whole NO_FILES_MSG string. The only previous negative used
    'Something else entirely', which fails against any shortening of the constant --
    so truncating it to 'No files found' passed the suite."""
    record = _stuck_record()
    record["statusMessages"] = [{
        "title": "Silo S03E05",
        "messages": ["No files found in /data/torrents/complete/Silo"],
    }]
    candidates, new_seen = find_stuck(group_by_download_id([record]), NOW, {}, 15)
    assert candidates == []
    assert new_seen == {}


def test_message_match_is_case_sensitive():
    record = _stuck_record()
    record["statusMessages"] = [{"title": "x", "messages": [NO_FILES_MSG.upper()]}]
    _c, new_seen = find_stuck(group_by_download_id([record]), NOW, {}, 15)
    assert new_seen == {}


# --- a failed delete is not a success -----------------------------------------

def test_failed_delete_is_not_counted_as_acted():
    class AllFail(FakeArr):
        def delete_queue_item(self, queue_id, blocklist=True, skip_redownload=False):
            raise RuntimeError("sonarr said no")

    sonarr = AllFail([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr())
    acted = watcher.run_once()
    assert acted == []          # nothing was actually removed
    assert notes == []          # and nothing claimed otherwise on the phone


# --- the anomaly notification must inform, not assert a cause -----------------

def _cap_breach():
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in (1, 2, 3)]
    sonarr = FakeArr(records)
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    return notes[0]


def test_anomaly_message_lists_the_candidates():
    """Refusing to act is only half the job: without the list, the operator cannot
    tell a broken mount from three RARed grabs off one indexer, which is the whole
    decision the notification exists to support."""
    _title, _prio, message = _cap_breach()
    assert "Silo S03E05" in message
    assert "LimeTorrents" in message


def test_anomaly_message_does_not_assert_a_cause():
    """It used to state 'isso costuma ser falha sistêmica (disco cheio ou
    desmontado)'. Two common routes to the cap have nothing to do with disk: a
    batch of RARed releases (qBit excludes *.rar, so they all strand on the same
    'no files eligible' message), and dry-run, where candidates accumulate because
    nothing is ever removed."""
    _title, _prio, message = _cap_breach()
    assert "disco cheio" not in message


def test_anomaly_message_says_when_it_is_only_simulating():
    """In dry-run the count is cumulative -- nothing leaves the queue -- so the same
    number means something different from the armed case."""
    records = [_preair_record(158, rec_id=n, download_id=f"D{n}") for n in (1, 2, 3)]
    watcher, notes = _watcher(FakeArr(records), FakeArr(), settings=DryRun())
    watcher.run_once()
    assert "SIMULAÇÃO" in notes[0][0] or "simula" in notes[0][2].lower()


# --- clock sanity -------------------------------------------------------------
# Gate B compares airDateUtc against the container clock. A clock running behind
# makes an ALREADY-AIRED episode look like a pre-air fake, and gate B has no
# minimum-age wait, so the first cycle after a skew is destructive and skips the
# re-search. Cross-checking against Sonarr does not help: it is a container on the
# same Docker host and shares the kernel clock, so host drift moves both together.
# What is detectable is the transition -- the cycle where time jumps backwards.

def test_clock_jumping_backwards_suspends_the_preair_gate():
    sonarr = FakeArr([_preair_record(158)])
    watcher, notes = _watcher(sonarr, FakeArr())
    watcher.run_once()
    sonarr.deleted.clear(); notes.clear()

    watcher._now = lambda: NOW - timedelta(hours=48)      # host resumed, clock wrong
    watcher.run_once()
    assert sonarr.deleted == []
    assert any("relógio" in n[2].lower() for n in notes)


def test_gate_a_still_runs_after_a_backwards_jump():
    """Only gate B reads wall-clock against external data. Gate A measures an elapsed
    delta, and a backwards jump makes items look LESS mature -- safe by itself."""
    sonarr = FakeArr([_stuck_record()])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher._first_seen = {"sonarr": {"ABC": NOW - timedelta(days=9)}}
    watcher._now = lambda: NOW - timedelta(hours=48)
    watcher.run_once()
    assert sonarr.deleted == [(1, True, False)]


def test_the_gate_recovers_once_the_clock_is_sane_again():
    sonarr = FakeArr([_preair_record(158)])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    watcher._now = lambda: NOW - timedelta(hours=48)
    watcher.run_once()
    sonarr.deleted.clear()
    watcher._now = lambda: NOW + timedelta(minutes=10)     # NTP corrected
    watcher.run_once()
    assert sonarr.deleted == [(1, True, True)]


def test_small_backwards_drift_is_not_treated_as_a_jump():
    """Sub-interval jitter is normal; only a jump bigger than the poll interval
    means something actually moved the clock."""
    sonarr = FakeArr([_preair_record(158)])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher.run_once()
    sonarr.deleted.clear()
    watcher._now = lambda: NOW - timedelta(seconds=30)
    watcher.run_once()
    assert sonarr.deleted == [(1, True, True)]


# --- the blocklist loop guard -------------------------------------------------
# Gate A blocklists and asks for a re-search. When the cause is environmental
# rather than release-specific -- a mount, a path mapping, or a release group that
# consistently packs in RAR (qBit excludes *.rar, so every one of their releases
# strands identically) -- the replacement lands in the same state and is
# blocklisted 15 minutes later. Simulated in review: 13 distinct releases of one
# episode blocklisted in 6.5h, one group per cycle, so the per-cycle cap never
# trips. The webhook self-heal already has max_attempts for exactly this shape.

def _blocklist_n_times(n, watcher, sonarr, download_ids):
    for i, did in enumerate(download_ids[:n]):
        sonarr.records = [_stuck_record(rec_id=1, download_id=did, episode_id=555)]
        watcher._first_seen = {"sonarr": {did: NOW - timedelta(minutes=60)}}
        watcher.run_once()


def test_repeated_blocklists_for_one_episode_are_stopped():
    """The release changes every round, so nothing downstream notices the loop."""
    sonarr = FakeArr()
    watcher, notes = _watcher(sonarr, FakeArr())
    _blocklist_n_times(3, watcher, sonarr, ["R1", "R2", "R3"])
    assert len(sonarr.deleted) == 3

    _blocklist_n_times(1, watcher, sonarr, ["R4"])
    assert len(sonarr.deleted) == 3                    # refused, not acted
    assert any("manual" in n[2].lower() for n in notes)


def test_the_give_up_notice_fires_once_not_every_cycle():
    sonarr = FakeArr()
    watcher, notes = _watcher(sonarr, FakeArr())
    _blocklist_n_times(3, watcher, sonarr, ["R1", "R2", "R3"])
    before = len(notes)
    _blocklist_n_times(1, watcher, sonarr, ["R4"])
    _blocklist_n_times(1, watcher, sonarr, ["R5"])
    _blocklist_n_times(1, watcher, sonarr, ["R6"])
    assert len(notes) == before + 1


def test_a_different_episode_is_unaffected():
    sonarr = FakeArr()
    watcher, _ = _watcher(sonarr, FakeArr())
    _blocklist_n_times(3, watcher, sonarr, ["R1", "R2", "R3"])

    sonarr.records = [_stuck_record(rec_id=7, download_id="OTHER", episode_id=999)]
    watcher._first_seen = {"sonarr": {"OTHER": NOW - timedelta(minutes=60)}}
    watcher.run_once()
    assert sonarr.deleted[-1] == (7, True, False)


def test_the_guard_keys_on_content_not_on_the_release():
    """Keying on downloadId would count every round as a first attempt -- the loop
    changes release every time, which is the entire problem."""
    sonarr = FakeArr()
    watcher, _ = _watcher(sonarr, FakeArr())
    _blocklist_n_times(4, watcher, sonarr, ["A", "B", "C", "D"])
    assert len(sonarr.deleted) == 3


def test_a_record_without_a_content_id_still_acts():
    """Defence in depth, not a primary gate: if the identifier is missing the counter
    cannot function, but the per-cycle cap and the ntfy still apply. Refusing to act
    here would let a renamed API field disable gate A entirely."""
    record = _stuck_record(rec_id=4, download_id="NOID")
    del record["episodeId"]
    sonarr = FakeArr([record])
    watcher, _ = _watcher(sonarr, FakeArr())
    watcher._first_seen = {"sonarr": {"NOID": NOW - timedelta(minutes=60)}}
    watcher.run_once()
    assert sonarr.deleted == [(4, True, False)]


def test_the_guard_state_stays_bounded():
    """A long-uptime thread must not accumulate one entry per episode forever."""
    sonarr = FakeArr()
    watcher, _ = _watcher(sonarr, FakeArr())
    for n in range(1200):
        sonarr.records = [_stuck_record(rec_id=1, download_id=f"D{n}", episode_id=n)]
        watcher._first_seen = {"sonarr": {f"D{n}": NOW - timedelta(minutes=60)}}
        watcher.run_once()
    assert len(watcher._blocklists) <= watcher.BLOCKLIST_MEMORY


# --- liveness -----------------------------------------------------------------
# Every log line in this module was error or warning, so a healthy cycle produced
# NOTHING. Combined with /health measuring only Flask (deliberate) and run_forever
# swallowing failures, "polling fine" and "thread died three weeks ago" looked
# identical in `docker logs`. That ambiguity makes the dry-run soak unmeasurable:
# its pass condition is silence.

def test_a_healthy_cycle_leaves_a_trace(caplog):
    sonarr = FakeArr([_stuck_record()])
    watcher, _ = _watcher(sonarr, FakeArr())
    with caplog.at_level(logging.INFO, logger="queue_watch"):
        watcher.run_once()
    assert "cycle ok" in caplog.text


def test_the_trace_carries_the_counts(caplog):
    sonarr = FakeArr([_stuck_record(rec_id=1, download_id="A"),
                      _stuck_record(rec_id=2, download_id="B")])
    watcher, _ = _watcher(sonarr, FakeArr())
    with caplog.at_level(logging.INFO, logger="queue_watch"):
        watcher.run_once()
    assert "2 groups" in caplog.text


class _Stalled:
    """A watcher whose cycles always fail."""

    def __init__(self):
        self.alerts = []
        self.cycles = 0

    def run_once(self):
        self.cycles += 1
        raise RuntimeError("sonarr unreachable")

    def alert_stalled(self, consecutive, exc):
        self.alerts.append((consecutive, str(exc)))


def test_repeated_cycle_failures_raise_an_alarm():
    """Both gates are down and nothing says so: /health still reports healthy, because
    it measures Flask on purpose."""
    w = _Stalled()

    def sleep_fn(_s):
        if w.cycles >= 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_forever(w, 10, sleep_fn=sleep_fn, alert_after=3)
    assert w.alerts == [(3, "sonarr unreachable")]


def test_the_stall_alarm_does_not_repeat_every_cycle():
    w = _Stalled()

    def sleep_fn(_s):
        if w.cycles >= 12:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_forever(w, 10, sleep_fn=sleep_fn, alert_after=3)
    assert len(w.alerts) == 1


def test_a_recovered_cycle_rearms_the_alarm():
    """A flapping Sonarr must alert again on the next stall, not stay quiet forever."""
    class Flaky(_Stalled):
        def run_once(self):
            self.cycles += 1
            if self.cycles == 4:      # one good cycle in the middle
                return []
            raise RuntimeError("sonarr unreachable")

    w = Flaky()

    def sleep_fn(_s):
        if w.cycles >= 8:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_forever(w, 10, sleep_fn=sleep_fn, alert_after=3)
    assert len(w.alerts) == 2


def test_the_watcher_alerts_over_ntfy_when_stalled():
    watcher, notes = _watcher(FakeArr(), FakeArr())
    watcher.alert_stalled(3, RuntimeError("connection refused"))
    assert len(notes) == 1
    assert notes[0][1] >= 4                       # loud enough to reach the phone
    assert "connection refused" in notes[0][2]
