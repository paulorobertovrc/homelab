"""Queue gates.

Gate A ("stuck"): clears items the qBittorrent excluded_file_names guard strands.
When every file in a torrent is filtered out, qBit reports it complete at 0 bytes and
the *arr blocks forever on "No files found are eligible for import".

Gate B ("pre-air"): kills grabs of episodes that have not aired yet. Every such release
is fake by construction, and this catches the ones packaged as .mkv, which the extension
guard cannot see.
"""
import logging
from datetime import datetime, timedelta, timezone

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
        self._dry_run_notified = set()

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

        # Forget dry-run reports for groups that left the queue, so an item that comes back
        # is reported again rather than staying silent forever.
        self._dry_run_notified &= {(c["kind"], c["download_id"]) for c in candidates}

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
        # group_by_download_id guarantees every record here has an "id".
        queue_id = min(r["id"] for r in records)
        title = records[0].get("title", "?")
        indexer = records[0].get("indexer") or "?"
        is_preair = "preair" in candidate["gates"]
        gates = "+".join(candidate["gates"])

        if is_preair:
            reason = (f"episódio ainda não exibido "
                      f"({candidate.get('hours_early')}h de antecedência)")
            heading = "🚫 queue-watch: grab pre-air"
        else:
            reason = "nenhum arquivo elegível para import (payload barrado)"
            heading = "🧹 queue-watch: fila destravada"
        summary = f"{title}\nIndexer: {indexer}\nMotivo: {reason}"

        if self._settings.queue_watch_dry_run:
            # Dry-run leaves the item in the queue, so it stays a candidate every cycle.
            # Without this dedup the same item would notify every 10 minutes and the mode
            # would be unusable.
            key = (candidate["kind"], candidate["download_id"])
            if key in self._dry_run_notified:
                return False
            self._dry_run_notified.add(key)
            logger.warning("queue-watch [DRY-RUN]: would remove %s (%s) via %s",
                           title, indexer, gates)
            self._notify(self._settings.ntfy_url, f"[SIMULAÇÃO] {heading}", "eyes", 3,
                         f"{summary}\n\nNADA foi removido — "
                         f"QUEUE_WATCH_DRY_RUN=true.")
            return True

        try:
            # Pre-air skips the forced re-search: the episode does not exist yet, so an
            # immediate search can only surface another fake and feed the loop. Let the
            # scheduled RSS pick it up naturally once it has actually aired.
            candidate["client"].delete_queue_item(queue_id, skip_redownload=is_preair)
        except Exception as e:
            logger.error("queue-watch: could not remove %s: %s", title, e)
            return False

        logger.warning("queue-watch: removed %s (%s) via %s", title, indexer, gates)
        self._notify(self._settings.ntfy_url, heading, "lock", 4,
                     f"{summary}\nRemovido + blocklist.")
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
