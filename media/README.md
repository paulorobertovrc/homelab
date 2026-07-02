# Media stack (Servarr) — homelab WSL2

Radarr / Sonarr / Bazarr / Prowlarr / qBittorrent behind a **NordVPN (NordLynx/WireGuard)**
kill‑switch (gluetun), Brazil servers. Mullvad config is kept commented in `.env` as a
fallback. Runs on **WSL2 (Ubuntu 26.04)**; **Plex on the Windows host** serves the library.

## VPN scope — only the stack, never the machine

The VPN lives **inside the gluetun container's network namespace**. Nothing on the
host (Windows, Plex, your browsing, other containers) touches it.

| Component | Egress path |
|---|---|
| **qBittorrent** | 🔒 NordVPN (`network_mode: service:gluetun`) |
| **Prowlarr** | 🔒 NordVPN (`network_mode: service:gluetun`) |
| Radarr / Sonarr / Bazarr | 🌐 normal network (talk to TMDB/TVDB metadata; reach qbit/prowlarr through gluetun) |
| FlareSolverr | 🌐 normal network — **not tunneled**. It fetches 1337x/EZTV pages on Prowlarr's behalf from its own (non-VPN) IP. Only qBittorrent's actual downloads and Prowlarr's own egress are VPN'd. |
| **Host + Plex + everything else** | 🌐 normal network |

If gluetun's tunnel drops, its firewall blocks **all** egress from qbit/prowlarr →
**no IP leak**. This is a kill‑switch, not just a route.

> Want Radarr/Sonarr/Bazarr behind the VPN too? Not recommended (they only hit
> metadata APIs), but you'd give each `network_mode: service:gluetun` + a
> `depends_on: gluetun` block and drop their `networks:`/`ports:` (publish those
> ports on gluetun instead).

## Storage — WSL‑aware (this is the important part)

| Data | Host path | Filesystem | Why |
|---|---|---|---|
| App configs / DBs | `/docker/appdata/*` | ext4 (WSL disk) | SQLite is slow/corruption‑prone on 9p |
| Active downloads | `/data/torrents` | ext4 (WSL disk) | fast; absorbs torrent churn |
| Final library | `/mnt/f/Media` (`F:\Media`) | drvfs/9p | 7 TB; **Plex on Windows reads it natively** |

Flow you asked for: qBittorrent downloads onto the **fast ext4 disk**; when a download
finishes, **Radarr/Sonarr import = move + rename into `F:\Media\Movies` / `F:\Media\TV Shows`**.
Because downloads (ext4) and library (F:) are different filesystems, import is a real
**copy across disks** (no hardlink) — expected, and exactly "move to F when done".
Plex then picks the file up from `F:\Media`.

Inside the containers everything is unified under `/data`:
`/data/torrents/{incomplete,complete}` and `/data/media/{Movies,"TV Shows"}`.

## Network

`servarr_network` = `172.39.0.0/24` (set in `.env`). Static IPs: gluetun `.2`,
radarr `.4`, sonarr `.3`, bazarr `.6`. qbit/prowlarr share gluetun's IP (`.2`).
`FIREWALL_OUTBOUND_SUBNETS` tracks the subnet so the *arr apps can reach qbit/prowlarr
through the VPN firewall.

Access (WSL mirrored networking → reachable on `localhost`, `192.168.0.151`, or Tailscale):
qBittorrent `:8090` · Prowlarr `:9696` · Radarr `:7878` · Sonarr `:8989` · Bazarr `:6767`.
(Host port 8080 is taken on Windows, so the qBittorrent WebUI is published on 8090 →
container 8080; the Radarr/Sonarr → qbit link still uses the internal `172.39.0.2:8080`.)

---

## Current state (fully wired, verified live)

Everything below was configured end‑to‑end via each app's API and verified with real
requests — not just "should work."

- **qBittorrent** (`:8090`): permanent WebUI password set (was: regenerating temp
  password). Categories `radarr`/`sonarr` created; save path `/data/torrents/complete`,
  incomplete `/data/torrents/incomplete`. Host header validation disabled
  (`WebUI\HostHeaderValidation=false`) — required because the WebUI is remapped
  (host 8090 → container 8080; host 8080 was taken on Windows) and qBittorrent
  otherwise 401s on the port mismatch.
- **Radarr** (`:7878`): root folder `/data/media/Movies` (already sees the existing
  library — **not yet imported**, see below). Download client qBittorrent
  (`172.39.0.2:8080`, category `radarr`) added and connection‑tested OK.
- **Sonarr** (`:8989`): root folder `/data/media/TV Shows`, same download client
  wiring (category `sonarr`), tested OK.
- **Prowlarr** (`:9696`): registered as an Application against both Radarr
  (`http://172.39.0.4:7878`) and Sonarr (`http://172.39.0.3:8989`) — connection
  tested OK both ways.
- **Bazarr** (`:6767`): connected to Radarr (`172.39.0.4:7878`) and Sonarr
  (`172.39.0.3:8989`); SignalR live‑sync confirmed connected to both.
- **Indexers** (public, no login) — added via Prowlarr, live search verified
  (143 real results for a test query):
  | Indexer | Status |
  |---|---|
  | YTS | ✅ added, working |
  | The Pirate Bay | ✅ added, working |
  | LimeTorrents | ✅ added, working |
  | 1337x | ❌ **not added** — Prowlarr rejects it (HTTP 400), see below |
  | EZTV | ❌ **not added** — Prowlarr rejects it (HTTP 400), see below |

### FlareSolverr (Cloudflare bypass) — partial

`1337x.to` and `eztvx.to` sit behind Cloudflare, so a **FlareSolverr** container was
added (`172.39.0.5:8191`, tagged so only these two indexers route through it).
Root‑caused via Prowlarr's debug log: FlareSolverr *does* solve the JS challenge
(confirmed in its own log: `Challenge solved!`, 200 OK, ~12–14s) and Prowlarr *does*
wait for and receive that full response — but still classifies it as blocked. This
means the returned page still carries Cloudflare markers Prowlarr's Cardigann parser
rejects, i.e. these two sites currently have a protection layer FlareSolverr 3.5.0
alone doesn't fully clear. This is a **known external limitation** (site hardening,
not a stack misconfiguration) — not something to keep patching blindly. Prowlarr
rejects the add at save time (HTTP 400), so **1337x/EZTV are not present** in the
indexer list at all right now — only YTS/The Pirate Bay/LimeTorrents are. FlareSolverr
itself is left running (Settings → Indexer Proxies) in case a future site/FlareSolverr
update fixes it — retry adding 1337x/EZTV from Prowlarr's UI (Indexers → Add) then.

### Not yet done (needs your review, not automatable)

**Library import**: Radarr/Sonarr already see the existing files in `F:\Media\Movies`
and `F:\Media\TV Shows` as *unmapped folders*, but haven't added them to their library
yet (0 movies/series tracked). Importing means matching each folder to the correct
TMDB/TVDB entry — a content decision, not run automatically. In each app:
**Library → Import** (or **Movies/Series → Add New → Import Existing**), review the
matches, confirm.

## Extras / operations

- **Backups** — `scripts/arr-backup.sh` via systemd timer `arr-backup.timer` (daily
  04:30, `Persistent=true`). Tars `/docker/appdata` → `D:\backups\arr_server`
  (survives a WSL reset), keeps 14. Restore: stop stack, extract the tar over
  `/docker/appdata`, start.
- **Quality (Recyclarr)** — `recyclarr` container syncs TRaSH custom formats +
  profiles daily. Radarr uses **"Remux + WEB 2160p"**, Sonarr **"WEB-2160p"** — 4K
  prioritized, HDR10+/DV + Atmos/DTS-X/DTS-HD MA/TrueHD scored highest, SDR &
  `DV-w/o-HDR` blocked. All existing items are on the 4K profile; upgrades happen
  gradually (RSS / manual search — no automatic back-catalog flood). Config +
  API keys live in `/docker/appdata/recyclarr/recyclarr.yml` (not in git).
  ⚠️ 4K-strict: a title with no 4K release won't grab until one exists. To also
  accept 1080p as a fallback, widen the profile's `qualities`.
- **Subtitles (Bazarr)** — profile **EN + PT-BR**, external `.srt` only
  (`use_embedded_subs=false`, so Plex never has to burn). Provider `podnapisi`
  (currently offline). Add an **OpenSubtitles.com** free account (Settings →
  Providers) for real coverage.
- **Notifications (ntfy)** — self-hosted at `:8095`, topic **`arr-media`**.
  Radarr/Sonarr/Prowlarr push grab/import/upgrade/health events. Subscribe: install
  the ntfy app → add server `http://192.168.0.151:8095` (or via Tailscale) → topic
  `arr-media`.
- **Self-healing (autoheal)** — restarts any `autoheal=true` container that goes
  unhealthy: gluetun (VPN recovery) and qbit/prowlarr (reconnect if gluetun bounced).
- **Remote access (Tailscale)** — this WSL node is `gabinete-host`
  (`gabinete-host.gab.internal`, `100.64.0.1`). All UIs are reachable over the tailnet
  at `http://gabinete-host.gab.internal:<port>` (encrypted by WireGuard; no LAN
  firewall rule needed). LAN access from other devices still needs the Hyper-V
  firewall rule (see git history).

## Verify no leak

```bash
docker exec qbittorrent wget -qO- https://ipinfo.io/ip   # -> a NordVPN Brazil IP
curl -s https://ipinfo.io/ip                             # -> your real ISP IP (host is NOT on VPN)
```
