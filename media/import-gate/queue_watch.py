"""Queue gates.

Gate A ("stuck"): clears items the qBittorrent excluded_file_names guard strands.
When every file in a torrent is filtered out, qBit reports it complete at 0 bytes and
the *arr blocks forever on "No files found are eligible for import".

Gate B ("pre-air"): kills grabs of episodes that have not aired yet. Every such release
is fake by construction, and this catches the ones packaged as .mkv, which the extension
guard cannot see.
"""
import logging
from datetime import datetime, timedelta

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


def _parse_air_date(value):
    """Returns an aware datetime, or None when missing, malformed, or timezone-naive.

    The naive case matters: `datetime.fromisoformat` accepts a timestamp with no offset
    and returns a naive object, which then raises TypeError when compared against an aware
    `now` -- escaping this function and aborting the entire cycle. Rejecting it here turns
    a crash into a spared group, which is the safe direction.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


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
