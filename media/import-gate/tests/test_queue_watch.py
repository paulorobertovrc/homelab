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
