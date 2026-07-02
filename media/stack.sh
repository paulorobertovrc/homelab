#!/usr/bin/env bash
# One-command control for the WHOLE media stack (every service in compose.yaml).
# "off" removes the containers -> zero CPU/RAM. Your data is safe: it lives in bind
# mounts (/docker/appdata, /data/torrents, F:\Media), never inside the containers.
#
#   ./stack.sh up        bring everything up (VPN first, then the apps)
#   ./stack.sh down      stop + remove everything (data kept)
#   ./stack.sh restart   down then up
#   ./stack.sh status    what's running
#   ./stack.sh logs [svc] follow logs (all, or one service)
#   ./stack.sh pull      update images (run 'up' afterwards)
#
# Tip: add an alias so it's one word from anywhere:
#   echo "alias mstack='$HOME/dev/homelab/media/stack.sh'" >> ~/.zshrc
set -euo pipefail

# Run from the compose dir so docker picks up ./compose.yaml + ./.env automatically.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

dc() { docker compose "$@"; }

case "${1:-status}" in
  up)
    dc up -d
    echo "▶  stack UP — dashboard: http://localhost:7575"
    ;;
  down)
    dc down
    echo "⏹  stack DOWN — containers removed, data in /docker/appdata kept"
    ;;
  restart)
    dc down && dc up -d
    echo "↻  stack RESTARTED"
    ;;
  status|ps)
    dc ps
    ;;
  logs)
    shift
    dc logs -f "$@"
    ;;
  pull)
    dc pull
    echo "images pulled — run './stack.sh up' to apply"
    ;;
  *)
    echo "usage: $0 {up|down|restart|status|logs [service]|pull}" >&2
    exit 1
    ;;
esac
