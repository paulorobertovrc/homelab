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
    incomplete = set()
    for record in records:
        download_id = record.get("downloadId")
        if not download_id:
            continue
        if record.get("id") is None:
            # The action needs min(id) across the group, so an id-less record is
            # unactionable. Dropping only that record would leave a PARTIAL group, and the
            # pre-air gate's all() over a subset is the exact failure mode pagination
            # exists to prevent -- so the whole group goes.
            incomplete.add(download_id)
            continue
        groups.setdefault(download_id, []).append(record)
    for download_id in incomplete:
        groups.pop(download_id, None)
    return groups
