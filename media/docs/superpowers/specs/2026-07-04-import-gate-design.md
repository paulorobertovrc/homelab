# import-gate — post-import audio-language & integrity gate

**Status:** Design approved 2026-07-04. Ready for implementation planning.
**Scope owner:** media stack (`/home/prvrc/dev/homelab/media`).

## Problem

Sonarr/Radarr import releases whose *tagged* language is trusted but sometimes
wrong: a release labeled "English" can actually carry only a dubbed track (this
burned the user before — "O Negócio" imported with Russian audio, American Horror
Stories S02 with corrupt files). Nothing in the current stack catches this:

- The torrent client hash-check guarantees the file is bit-complete, not that it
  is the right *content*.
- Custom formats / language profiles select on the release's language **tag**,
  which lies or is missing.
- ffprobe reads the audio stream's language **tag** — same blind spot.

Only something that **listens** to the audio can catch a mislabeled/dubbed track.
That is the core reason this component exists.

## Goal

A post-import gate that, for each imported movie/episode:

1. Verifies file integrity (cheap, ffprobe).
2. Verifies that a track in the **original language** is actually present, by
   transcribing a sample with Whisper (the unique value).
3. On failure: quarantines the file (preserved, not deleted), blocklists the
   release, triggers a re-search, and notifies — with a loop guard.

### Explicitly out of scope (YAGNI)

- **Missing audio/subtitle track enforcement** — belongs in custom formats /
  language profiles at grab time, not a post-import gate. Revisit only if a
  concrete recurring pain appears.
- **Auto-deletion of quarantined files** — quarantine preserves; the human decides.

## Decisions (locked during brainstorming)

| Question | Decision |
|----------|----------|
| What to catch | Wrong spoken language (Whisper, core) + file integrity (ffprobe, cheap add-on). Skip missing-track. |
| "Correct" language rule | File must contain **at least one track in the title's original language** (`originalLanguage` from *arr metadata). Reject if only a dub is present. |
| Trigger topology | **Webhook → sidecar validator service** (same mechanism the existing ntfy connection uses). Not a custom script inside the *arr (hotio images lack ffmpeg/whisper); not an independent folder watcher (loses event metadata). |
| Whisper compute | **CPU** (faster-whisper, `small`/`medium`). The gate runs async and occasionally; CPU latency (~30–90s/sample) is irrelevant. Avoids the `nvidia-container-toolkit` infra dependency. Migrate to the RTX 4090 later only if it ever becomes a bottleneck. |
| Action on failure | **Quarantine + blocklist + auto re-search + ntfy notify.** Human-in-the-loop on the file; proactive on finding a replacement. |
| Cheap layer first | Language profile / custom format rejects wrongly-*tagged* releases at grab time (config, not code — reduces how often Whisper runs). |

## Architecture

New sidecar container **`import-gate`** on `servarr_network` (static IP
`172.39.0.17`), custom image (slim base + `ffmpeg` + `faster-whisper` + a small
HTTP server). It mounts:

- `${LIBRARY}:/data/media:ro` — same path the *arr use, so the file path in the
  webhook payload resolves 1:1.
- `/mnt/d/quarantine/arr_server:/data/quarantine:rw` — quarantine, **outside the
  library** (so the *arr never rescan/re-import it), on the D: drive (survives a
  WSL reset, same disk as backups).
- `${CONFIG_ROOT}/import-gate/` — config + persistent state (attempt counter).

### Data flow

```
Sonarr/Radarr ──[Webhook: On Import / On Upgrade]──▶ import-gate HTTP server
  1. Parse payload: file path, movie/episode id, originalLanguage,
     release info, grabbed-history id.
  2. ffprobe integrity: opens? ≥1 video stream? ≥1 audio stream?
     duration sane (vs expected runtime, else a minimum floor)?
       └─ fail here → reject as corrupt/fake, WITHOUT spending Whisper.
  3. ffprobe enumerate audio streams + language tags; locate the track(s)
     that should be originalLanguage.
  4. Whisper on the suspect track only, with anti-false-positive sampling.
  5. Decide: PASS → no-op (optional info ntfy) | FAIL → self-heal.
```

## Validation logic

### ffprobe integrity (always first, cheap)

Reject immediately (no Whisper) if: the container won't open, there is no video
stream, there is no audio stream, or the duration is implausibly short versus the
expected runtime from *arr metadata (fall back to a minimum-seconds floor when
runtime is unknown). This path handles the "corrupt/fake" class.

### Whisper spoken-language check (anti-false-positive)

- `ffmpeg` extracts **2–3 windows of ~30s**, skipping the first ~10% of runtime
  (avoids multilingual openings / musical intros), sampled from the middle.
- `faster-whisper` language detection per window (returns language + probability).
- **Reject only on a confident mismatch**: detected language must be non-original
  with high probability across the majority of windows. Ties / low confidence →
  **PASS** (bias toward never destroying a good file).
- Optimization:
  - A track *tagged* as original → Whisper only **confirms** it isn't a
    mislabeled dub.
  - **No** track even tagged as original → likely reject, but still confirm by
    sampling before acting (a track may be untagged yet actually original).

## Self-heal (on reject)

Note the library is mounted **read-only**, so `import-gate` cannot itself delete
the original. It **copies** the file to quarantine (reading from the ro mount),
then lets the *arr delete the original (the *arr hold the rw mount). Ordering:

```
1. Copy the file → /data/quarantine/<title> (<reason>)/   (preserve; ro read)
      └─ folder name encodes the reason, matching the user's existing
         convention (e.g. "O Negocio S03 (audio russo - nao usar)").
2. DELETE the *arr file record via API (DELETE /api/v3/moviefile|episodefile/{id})
      └─ removes the ORIGINAL physical file from the library + the DB record,
         so the item becomes "missing".
3. Mark the "grabbed" history item as FAILED via the *arr API
      └─ native primitive: blocklists the release (won't re-grab the same dub)
         AND triggers a fresh search.
4. Increment the per-title attempt counter (persistent state).
5. ntfy notify with the reason, e.g.
   "🔒 Quarantine: <title> — dub only (orig=EN, detected=PT). Attempt N. Re-search triggered."
```

At implementation, verify the exact Radarr/Sonarr semantics of "mark history
failed" vs. deleting the moviefile (whether failed-marking alone triggers the
search, and whether it needs the file already gone) — the two steps together must
net out to: original removed, release blocklisted, search running.

### Loop guard (critical)

A title with only dubbed releases available would otherwise loop
download→quarantine→re-search forever. After **N = 3** attempts, the gate stops
re-searching, leaves the file in quarantine, and notifies:
"⚠️ Gave up after 3 attempts — <title> needs manual intervention." The user takes
over from there.

## Error handling (defensive)

- If Whisper/ffprobe **errors** (a failure of the gate itself, not a reject):
  **do not quarantine.** Notify "gate unavailable, imported without validation"
  and let the import stand. A broken gate must never destroy imports.
- Webhook is **idempotent**: the same event delivered twice acts once
  (dedupe by grabbed-history id).
- Every API action against the *arr is logged under `${CONFIG_ROOT}/import-gate/`.

## Deployment

- `import-gate` service in `compose.yaml`; `SET_IP_IMPORT_GATE=172.39.0.17` in
  `.env` / `.env.example`.
- **Step 0 (config, not code):** language profile / custom format in both *arr to
  reject wrongly-*tagged* releases at grab time.
- **Webhook** connection in Sonarr and Radarr (On Import + On Upgrade) →
  `http://172.39.0.17:8080/webhook`. The HTTP server listens on container port
  8080; **no host port mapping** — it is only reached internally on
  `servarr_network` by the *arr.

## Testing

Real fixtures already exist: the user's manual quarantine at
`/mnt/f/Media/_quarantena` (58 GB) contains exactly the failure modes —
"O Negocio S03/S04 (audio russo)" (language reject) and "AHS S02 (corrupt)"
(ffprobe reject). Plan:

1. **Extract short (~2–3 min) audio clips** from each as lightweight test
   fixtures; the full 58 GB can then be freed at the user's discretion.
2. **Migrate the folder out of the library** to `/mnt/d/quarantine/arr_server`
   (it currently sits inside `${LIBRARY}`, an anti-pattern the design avoids).
3. Test the three paths: Russian-audio clip → language reject; corrupt clip →
   ffprobe reject; a known-good library file → pass.
4. Test the loop guard: simulate 3 rejects → gives up + notifies.

## Prerequisites / notes

- Nothing is deleted automatically at any point; the 58 GB of existing quarantine
  is preserved until the user explicitly frees it.
- The RTX 4090 is available on the host but Docker GPU access is **not** set up
  (`nvidia-container-toolkit` absent) — intentionally not required by this design.
