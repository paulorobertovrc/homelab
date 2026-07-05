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

if [ "${1:-}" = "--reconcile" ]; then
  execute=false; [ "${2:-}" = "--yes" ] && execute=true
  echo "== reconcile untagged managed folders (execute=$execute) =="

  # Sonarr series whose path lacks a {tvdb-...} tag.
  #
  # GUARD: tvdbId 74205 is "Band of Brothers (2001)" — its existing tag is
  # `{tmdb-74205}`, but 74205 is actually its tvdbId (its real tmdbId is 4613),
  # so it falls through this "no {tvdb-" filter like the genuinely broken
  # items. Unlike those, it's populated (10 files, ~157GB) and actively used
  # in Plex, and the fix was deliberately deferred to a separate manual task
  # (see git log for the review that found this). Do NOT let an automated
  # sweep move this folder. Remove this guard once the tag is corrected by hand.
  api GET "$S/api/v3/series" "$SK" \
    | jq -c '.[] | select((.path|test("\\{tvdb-"))|not) | {id,title,year,tvdbId,path}' \
    | while read -r row; do
        id=$(jq -r .id     <<<"$row"); title=$(jq -r .title <<<"$row")
        year=$(jq -r .year <<<"$row"); tvdb=$(jq -r .tvdbId <<<"$row")
        path=$(jq -r .path <<<"$row"); parent=$(dirname "$path")
        if [ "$tvdb" = "74205" ]; then
          echo "  Sonarr: skipping $title ($year) — known mislabeled tag, deferred, see git log"
          continue
        fi
        new="$parent/$title ($year) {tvdb-$tvdb}"
        echo "  Sonarr: '$path'"
        echo "       -> '$new'"
        if $execute; then
          body=$(api GET "$S/api/v3/series/$id" "$SK" | jq --arg p "$new" '.path=$p')
          api PUT "$S/api/v3/series/$id?moveFiles=true" "$SK" "$body" >/dev/null
          actual=$(api GET "$S/api/v3/series/$id" "$SK" | jq -r .path)
          if [ "$actual" = "$new" ]; then
            echo "          moved -> $new"
          else
            echo "          WARNING: requested '$new' but app reports '$actual' — path may not match on disk, verify manually"
          fi
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
          actual=$(api GET "$R/api/v3/movie/$id" "$RK" | jq -r .path)
          if [ "$actual" = "$new" ]; then
            echo "          moved -> $new"
          else
            echo "          WARNING: requested '$new' but app reports '$actual' — path may not match on disk, verify manually"
          fi
        fi
      done
fi
