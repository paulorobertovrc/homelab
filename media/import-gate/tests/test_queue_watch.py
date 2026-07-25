from datetime import datetime, timedelta, timezone

from queue_watch import NO_FILES_MSG, find_preair, find_stuck, group_by_download_id

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
