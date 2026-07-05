# Library Folder Tags (#1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Sonarr/Radarr name library root folders in the Plex-standard tagged form (`Title (Year) {tvdb-id}` / `{tmdb-id}`) and reconcile the 5 existing untagged managed folders, without renaming any files.

**Architecture:** One idempotent bash script `media/apply-naming.sh` (lives next to `stack.sh`). Default run sets the two folder-format settings via the Sonarr/Radarr REST APIs (GET-modify-PUT `config/naming`). `--reconcile` reports managed paths still missing a tag (dry-run); `--reconcile --yes` moves them to the tagged name via `PUT /series|/movie?moveFiles=true` (app moves disk + DB atomically). File-rename flags are never touched.

**Tech Stack:** bash, curl, jq (1.8.1, present), Sonarr/Radarr API v3. No pytest — this is operational tooling verified live against the running stack (same pattern as `stack.sh`, which has no unit tests).

## Global Constraints

- **Naming-only, folder-only.** Do NOT change `renameEpisodes`/`renameMovies` (stay `false`). Never rename the ~1648 existing files.
- **Never `mv` library folders by hand.** Every folder move goes through the Sonarr/Radarr API so the app DB and disk stay in sync.
- **API keys** read from `media/.env` (`SONARR_API_KEY`, `RADARR_API_KEY`) — never hardcoded. Sonarr `http://localhost:8989`, Radarr `http://localhost:7878`.
- **Target formats (verbatim):** Sonarr `seriesFolderFormat` = `{Series Title} ({Series Year}) {tvdb-{TvdbId}}`; Radarr `movieFolderFormat` = `{Movie Title} ({Release Year}) {tmdb-{TmdbId}}`.
- **The 91 already-tagged folders must not move.** Reconcile only touches paths lacking `{tvdb-`/`{tmdb-`.
- Stack must be up (`./stack.sh status`) — the script talks to live Sonarr/Radarr.

---

### Task 1: `apply-naming.sh` — set the two folder formats (idempotent)

**Files:**
- Create: `media/apply-naming.sh`
- Modify: `media/README.md` (document the script under the ops-scripts section)

**Interfaces:**
- Consumes: `media/.env` (`SONARR_API_KEY`, `RADARR_API_KEY`).
- Produces: executable `apply-naming.sh` whose default (no-arg) run sets `seriesFolderFormat`/`movieFolderFormat` and is a no-op on re-run. Task 2 appends a `--reconcile` block to the same file.

- [ ] **Step 1: Create `media/apply-naming.sh` with the format-setting logic**

```bash
#!/usr/bin/env bash
# Enforce Plex-standard {tvdb-}/{tmdb-} root-folder naming in Sonarr/Radarr.
#
#   ./apply-naming.sh              set the two folder formats (idempotent)
#   ./apply-naming.sh --reconcile        report managed paths still missing a tag (dry-run)
#   ./apply-naming.sh --reconcile --yes  move those paths to the tagged name (files moved by the app)
#
# Files are never renamed: renameEpisodes/renameMovies are left untouched.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source ./.env; set +a

S="http://localhost:8989"; SK="${SONARR_API_KEY:?set SONARR_API_KEY in media/.env}"
R="http://localhost:7878"; RK="${RADARR_API_KEY:?set RADARR_API_KEY in media/.env}"
SFMT='{Series Title} ({Series Year}) {tvdb-{TvdbId}}'
RFMT='{Movie Title} ({Release Year}) {tmdb-{TmdbId}}'

api() {  # method url key [body]
  local m=$1 url=$2 key=$3 body=${4:-}
  if [ -n "$body" ]; then
    curl -sf -m 20 -X "$m" "$url" -H "X-Api-Key: $key" \
         -H 'Content-Type: application/json' -d "$body"
  else
    curl -sf -m 20 -X "$m" "$url" -H "X-Api-Key: $key"
  fi
}

set_format() {  # label base key field want
  local label=$1 base=$2 key=$3 field=$4 want=$5 cur now new
  cur=$(api GET "$base/api/v3/config/naming" "$key")
  now=$(echo "$cur" | jq -r ".$field")
  if [ "$now" = "$want" ]; then
    echo "  $label $field already correct: $want"
  else
    new=$(echo "$cur" | jq --arg w "$want" ".$field=\$w")
    api PUT "$base/api/v3/config/naming" "$key" "$new" >/dev/null
    echo "  $label $field: '$now' -> '$want'"
  fi
}

echo "== naming formats =="
set_format Sonarr "$S" "$SK" seriesFolderFormat "$SFMT"
set_format Radarr "$R" "$RK" movieFolderFormat  "$RFMT"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x media/apply-naming.sh`

- [ ] **Step 3: Run it — first application (expect drift → written)**

Run: `cd media && ./apply-naming.sh`
Expected output (formats changed from the untagged defaults):
```
== naming formats ==
  Sonarr seriesFolderFormat: '{Series Title}' -> '{Series Title} ({Series Year}) {tvdb-{TvdbId}}'
  Radarr movieFolderFormat: '{Movie Title} ({Release Year})' -> '{Movie Title} ({Release Year}) {tmdb-{TmdbId}}'
```

- [ ] **Step 4: Run it again — verify idempotency (no-op)**

Run: `cd media && ./apply-naming.sh`
Expected output:
```
== naming formats ==
  Sonarr seriesFolderFormat already correct: {Series Title} ({Series Year}) {tvdb-{TvdbId}}
  Radarr movieFolderFormat already correct: {Movie Title} ({Release Year}) {tmdb-{TmdbId}}
```

- [ ] **Step 5: Independently verify the live config took**

Run:
```bash
cd media && set -a; source ./.env; set +a
curl -s "http://localhost:8989/api/v3/config/naming" -H "X-Api-Key: $SONARR_API_KEY" | jq -r '.seriesFolderFormat, .renameEpisodes'
curl -s "http://localhost:7878/api/v3/config/naming" -H "X-Api-Key: $RADARR_API_KEY" | jq -r '.movieFolderFormat, .renameMovies'
```
Expected:
```
{Series Title} ({Series Year}) {tvdb-{TvdbId}}
false
{Movie Title} ({Release Year}) {tmdb-{TmdbId}}
false
```
(`renameEpisodes`/`renameMovies` MUST still be `false` — confirms files untouched.)

- [ ] **Step 6: Document the script in `media/README.md`**

Add, in the section that lists the ops scripts (near where `stack.sh` is described):
```markdown
### `apply-naming.sh` — Plex-standard root-folder tags

Sets Sonarr/Radarr folder formats to the Plex-recommended tagged form
(`Title (Year) {tvdb-id}` / `{tmdb-id}`) so new library folders match the agent.
Idempotent — safe to re-run; the naming config otherwise lives only in
`/docker/appdata` (outside git), so this script is its version-controlled record.

    ./apply-naming.sh              # set the two folder formats (no-op if already set)
    ./apply-naming.sh --reconcile        # report managed folders still missing a tag (dry-run)
    ./apply-naming.sh --reconcile --yes  # move those folders to the tagged name (app moves the files)

File names are never touched (`renameEpisodes`/`renameMovies` stay off) — only root folders.
```

- [ ] **Step 7: Commit**

```bash
cd /home/prvrc/dev/homelab/media
git add apply-naming.sh README.md
git commit -m "feat(library-tags): apply-naming.sh sets Plex tvdb/tmdb folder formats

Idempotent GET-modify-PUT of Sonarr seriesFolderFormat / Radarr movieFolderFormat
via API; file-rename flags left off. Versioned record of naming config that
otherwise lives only in /docker/appdata. Ref docs/superpowers/specs/2026-07-05-library-folder-tags-design.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `--reconcile` — move the 5 untagged managed folders + remove the orphan

**Files:**
- Modify: `media/apply-naming.sh` (append the `--reconcile` block after the format section)

**Interfaces:**
- Consumes: the `api()` helper, `$S/$SK/$R/$RK` vars from Task 1.
- Produces: `--reconcile` (dry-run report) and `--reconcile --yes` (execute) behavior.

- [ ] **Step 1: Append the reconcile block to `media/apply-naming.sh`**

Add at the end of the file (after the `set_format` calls):
```bash
if [ "${1:-}" = "--reconcile" ]; then
  execute=false; [ "${2:-}" = "--yes" ] && execute=true
  echo "== reconcile untagged managed folders (execute=$execute) =="

  # Sonarr series whose path lacks a {tvdb-...} tag.
  api GET "$S/api/v3/series" "$SK" \
    | jq -c '.[] | select((.path|test("\\{tvdb-"))|not) | {id,title,year,tvdbId,path}' \
    | while read -r row; do
        id=$(jq -r .id     <<<"$row"); title=$(jq -r .title <<<"$row")
        year=$(jq -r .year <<<"$row"); tvdb=$(jq -r .tvdbId <<<"$row")
        path=$(jq -r .path <<<"$row"); parent=$(dirname "$path")
        new="$parent/$title ($year) {tvdb-$tvdb}"
        echo "  Sonarr: '$path'"
        echo "       -> '$new'"
        if $execute; then
          body=$(api GET "$S/api/v3/series/$id" "$SK" | jq --arg p "$new" '.path=$p')
          api PUT "$S/api/v3/series/$id?moveFiles=true" "$SK" "$body" >/dev/null
          echo "          moved."
        fi
      done

  # Radarr movies whose path lacks a {tmdb-...} tag.
  api GET "$R/api/v3/movie" "$RK" \
    | jq -c '.[] | select((.path|test("\\{tmdb-"))|not) | {id,title,year,tmdbId,path}' \
    | while read -r row; do
        id=$(jq -r .id     <<<"$row"); title=$(jq -r .title <<<"$row")
        year=$(jq -r .year <<<"$row"); tmdb=$(jq -r .tmdbId <<<"$row")
        path=$(jq -r .path <<<"$row"); parent=$(dirname "$path")
        new="$parent/$title ($year) {tmdb-$tmdb}"
        echo "  Radarr: '$path'"
        echo "       -> '$new'"
        if $execute; then
          body=$(api GET "$R/api/v3/movie/$id" "$RK" | jq --arg p "$new" '.path=$p')
          api PUT "$R/api/v3/movie/$id?moveFiles=true" "$RK" "$body" >/dev/null
          echo "          moved."
        fi
      done
fi
```

- [ ] **Step 2: Dry-run — verify it lists exactly the 5 known untagged folders**

Run: `cd media && ./apply-naming.sh --reconcile`
Expected (order may vary; the `-> ` target lines are what matter):
```
== naming formats ==
  Sonarr seriesFolderFormat already correct: ...
  Radarr movieFolderFormat already correct: ...
== reconcile untagged managed folders (execute=false) ==
  Sonarr: '/data/media/TV Shows/Industry'
       -> '/data/media/TV Shows/Industry (2020) {tvdb-371796}'
  Sonarr: '/data/media/TV Shows/The Sopranos'
       -> '/data/media/TV Shows/The Sopranos (1999) {tvdb-75299}'
  Sonarr: '/data/media/TV Shows/House of Cards (US)'
       -> '/data/media/TV Shows/House of Cards (US) (2013) {tvdb-262980}'
  Radarr: '/data/media/Movies/Obsession (2026)'
       -> '/data/media/Movies/Obsession (2026) {tmdb-1339713}'
  Radarr: '/data/media/Movies/Heat (1995)'
       -> '/data/media/Movies/Heat (1995) {tmdb-949}'
```
If any UNEXPECTED path appears, STOP and investigate before executing.

- [ ] **Step 3: Execute the moves**

Run: `cd media && ./apply-naming.sh --reconcile --yes`
Expected: same 5 pairs, each now followed by `          moved.`

- [ ] **Step 4: Verify every managed path is now tagged (the post-condition query)**

Run:
```bash
cd media && set -a; source ./.env; set +a
echo "Sonarr untagged remaining:"; curl -s "http://localhost:8989/api/v3/series" -H "X-Api-Key: $SONARR_API_KEY" | jq -r '[.[]|select((.path|test("\\{tvdb-"))|not)]|length'
echo "Radarr untagged remaining:"; curl -s "http://localhost:7878/api/v3/movie"  -H "X-Api-Key: $RADARR_API_KEY" | jq -r '[.[]|select((.path|test("\\{tmdb-"))|not)]|length'
```
Expected: `0` and `0`.

- [ ] **Step 5: Verify on disk — new tagged folders exist, old names gone, Industry's files moved**

Run:
```bash
ls -d "/mnt/f/Media/TV Shows/Industry (2020) {tvdb-371796}" "/mnt/f/Media/TV Shows/The Sopranos (1999) {tvdb-75299}" "/mnt/f/Media/Movies/Heat (1995) {tmdb-949}" 2>/dev/null
echo "--- old untagged names should be gone: ---"
ls -d "/mnt/f/Media/TV Shows/Industry" "/mnt/f/Media/TV Shows/The Sopranos" 2>/dev/null || echo "old names gone (good)"
echo "--- Industry files landed in the tagged folder: ---"
find "/mnt/f/Media/TV Shows/Industry (2020) {tvdb-371796}" -type f -name '*.mkv' | wc -l
```
Expected: the tagged folders listed; old names gone; Industry file count = `4`.

- [ ] **Step 6: Remove the empty orphan folder (guarded — only if empty)**

The untagged `O Negócio (2013)` dir is a disk leftover NOT tracked by Sonarr (Sonarr manages `O Negócio (2013) {tvdb-272420}`). Remove only if empty:
```bash
ORPHAN="/mnt/f/Media/TV Shows/O Negócio (2013)"
if [ -d "$ORPHAN" ] && [ -z "$(find "$ORPHAN" -mindepth 1 -print -quit)" ]; then
  rmdir "$ORPHAN" && echo "removed empty orphan: $ORPHAN"
else
  echo "NOT empty or absent — left in place, investigate: $ORPHAN"
fi
```
Expected: `removed empty orphan: ...` (it was verified empty during design).

- [ ] **Step 7: Commit**

```bash
cd /home/prvrc/dev/homelab/media
git add apply-naming.sh
git commit -m "feat(library-tags): --reconcile moves untagged folders to tagged names

Scans Sonarr/Radarr for managed paths lacking {tvdb-}/{tmdb-} and moves them via
PUT ?moveFiles=true (dry-run by default, --yes to execute). Applied live: 5 folders
reconciled (Industry/Sopranos/House of Cards + Obsession/Heat), empty orphan
'O Negócio (2013)' removed. All managed paths now tagged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **`moveFiles=true` endpoint check:** if `PUT /series/{id}?moveFiles=true` (or the Radarr equivalent) returns 4xx, the move-query name may differ by version — GET one series object first, confirm the field set, and check Sonarr/Radarr API docs for the move parameter. Do NOT fall back to a manual `mv` (Global Constraints).
- **Path construction assumes simple titles** (no colons) — true for all 5 targets. If a future untagged title contains a colon or other illegal char, the app's `colonReplacementFormat` would differ from naive construction; verify the resulting folder matches before trusting it.
- **Blast-radius guard:** the dry-run (Task 2 Step 2) is the gate. If it lists anything beyond the known 5, stop — a mismatch means either a new untagged item or a path-construction bug.
