# Design — Plex-standard TVDB/TMDB tags on library root folders

**Date:** 2026-07-05
**Status:** Approved (brainstorming) — pending spec review
**Scope owner:** media stack (Sonarr/Radarr → Plex library on `F:\Media`)

## Problem

Plex's recommended folder convention tags each root folder with its metadata id —
`Series Title (Year) {tvdb-<id>}` for TV, `Movie Title (Year) {tmdb-<id>}` for movies —
so the Plex agent matches unambiguously. Most managed root folders already follow this (91 of 96
— 18 of 21 series, 73 of 75 movies), but recent additions came out **untagged**:

- **Sonarr** `seriesFolderFormat = {Series Title}` (no year, no tvdb token), `renameEpisodes = false`.
- **Radarr** `movieFolderFormat = {Movie Title} ({Release Year})` (no tmdb token), `renameMovies = false`.

The already-tagged folders were created under a prior config; the current formats dropped the
tokens, so anything added since lands untagged. Five managed folders are currently untagged
(plus one orphan disk folder not tracked by either app).

## Goal

Sonarr/Radarr name **root folders** in the Plex-standard tagged form automatically, and the five
existing untagged managed folders are reconciled to that form. Nothing else changes.

## Scope

**In scope (naming only, folder only):**
1. Fix the two folder-format settings so future folders get the tag.
2. Relocate the five untagged managed paths to their tagged names (via app API).
3. Remove one empty orphan disk folder.
4. A versioned, idempotent `apply-naming.sh` that sets the formats (durable/documented config).

**Out of scope (explicit):**
- Renaming the 1648 existing **files** — `renameEpisodes`/`renameMovies` stay `false`. Only root
  folders are touched.
- The 1643 already-tagged folders — untouched; changing the format does not move anything retroactively.
- **Why** the four empty folders' content never imported (Sopranos, House of Cards, Obsession, Heat
  all downloaded-but-not-imported; their torrents are likely the only copy). Tracked as a separate task.

## Changes

### 1. Folder-format config

| App | Field | From | To |
|---|---|---|---|
| Sonarr | `seriesFolderFormat` | `{Series Title}` | `{Series Title} ({Series Year}) {tvdb-{TvdbId}}` |
| Radarr | `movieFolderFormat` | `{Movie Title} ({Release Year})` | `{Movie Title} ({Release Year}) {tmdb-{TmdbId}}` |

`renameEpisodes` / `renameMovies` remain `false`. Applied via `PUT /api/v3/config/naming`.

### 2. Reconcile the five untagged managed paths

Via `PUT /api/v3/series` (Sonarr) / `PUT /api/v3/movie` (Radarr) with the updated `path` and
`moveFiles=true` — the app moves files on disk **and** updates its DB atomically (no manual disk
moves, so the DB never desyncs). Targets (year/id read from each app's own object, not guessed):

| App | Current path | Target folder | Files |
|---|---|---|---|
| Sonarr | `TV Shows/Industry` | `Industry (2020) {tvdb-371796}` | 4 (S04) — real move |
| Sonarr | `TV Shows/The Sopranos` | `The Sopranos (1999) {tvdb-75299}` | 0 (empty) |
| Sonarr | `TV Shows/House of Cards (US)` | `House of Cards (US) (2013) {tvdb-262980}` | 0 (empty) |
| Radarr | `Movies/Obsession (2026)` | `Obsession (2026) {tmdb-1339713}` | 0 (empty) |
| Radarr | `Movies/Heat (1995)` | `Heat (1995) {tmdb-949}` | 0 (empty) |

### 3. Orphan cleanup

`TV Shows/O Negócio (2013)` (untagged, **empty**, not tracked by Sonarr — Sonarr manages the tagged
`O Negócio (2013) {tvdb-272420}`) → `rmdir`. Verified empty before removal.

### 4. `apply-naming.sh` (versioned)

New script in `media/` (alongside `stack.sh`). Reads API keys from `.env` (never hardcoded).

- **Default run:** idempotently `PUT`s the two naming formats (section 1). Safe to re-run; reports
  current vs desired and only writes on drift. This is the durable, version-controlled record of the
  naming config (which otherwise lives only in `/docker/appdata`, outside git).
- **`--reconcile` flag:** additionally scans every series/movie whose `path` lacks a `{tvdb-`/`{tmdb-`
  tag and moves it to the tagged name (section 2), plus the orphan cleanup (section 3). Without the
  flag it only **reports** untagged paths (dry-run) — so the destructive move is opt-in.

## Safety & verification

- **Authoritative path only:** every folder change goes through the Sonarr/Radarr API; no raw `mv`
  that would desync the app DB from disk.
- **Idempotent & reversible:** re-running is a no-op once tagged; a path can be moved back.
- **Defensive checks:** for Industry (has files) confirm the 4 files land in the new folder and the old
  path is gone; for the empty ones confirm the folder was renamed.
- **Post-condition (the verification query):** re-query both apps → **every** series/movie `path`
  contains `{tvdb-` / `{tmdb-`; on disk the five tagged folders exist, the five old untagged names and
  the orphan are gone, and no already-tagged folder moved.

## Follow-ups (separate tasks, not this spec)

- Investigate why Sopranos / House of Cards / Obsession / Heat / Industry S1–S3 downloaded but never
  imported (their torrents are the only copy — do not delete). Ties into the #3 download-hygiene finding.
