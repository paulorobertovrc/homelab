"""Queue gates.

Gate A ("stuck"): clears items the qBittorrent excluded_file_names guard strands.
When every file in a torrent is filtered out, qBit reports it complete at 0 bytes and
the *arr blocks forever on "No files found are eligible for import".

Gate B ("pre-air"): kills grabs of episodes that have not aired yet. Every such release
is fake by construction, and this catches the ones packaged as .mkv, which the extension
guard cannot see.
"""
import logging
from datetime import timedelta

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
