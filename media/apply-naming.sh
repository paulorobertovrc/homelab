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
