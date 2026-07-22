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

Access (WSL mirrored networking → reachable on `localhost`, `<your-lan-ip>`, or Tailscale):
**Homepage (dashboard) `:7575` ← start here** · qBittorrent `:8090` · Prowlarr `:9696` ·
Radarr `:7878` · Sonarr `:8989` · Bazarr `:6767`.
(Host port 8080 is taken on Windows, so the qBittorrent WebUI is published on 8090 →
container 8080; the Radarr/Sonarr → qbit link still uses the internal `172.39.0.2:8080`.)

## Start / stop the whole stack

Don't want it running and eating resources? One command turns everything off/on
(`stack.sh` just wraps `docker compose` from this folder):

```bash
./stack.sh up        # bring everything up (gluetun/VPN first, then the apps)
./stack.sh down      # stop + remove all containers -> zero CPU/RAM
./stack.sh restart   # down then up (the safe way to bounce gluetun)
./stack.sh status    # what's running
./stack.sh logs [service]   # follow logs (all, or one)
```

"Off" is safe: all data lives in bind mounts (`/docker/appdata`, `/data/torrents`,
`F:\Media`), never inside the containers — `down` only removes the (stateless) containers.
For one-word convenience: `alias mstack='~/dev/homelab/media/stack.sh'`.

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

### Import-gate — post-import audio-language + integrity gate

`import-gate` (`172.39.0.17:8080`, internal only — no host port) validates every
Sonarr/Radarr import: ffprobe integrity check, then faster-whisper (CPU) confirms
the file actually contains an audio track in the title's original language (not
just a release *tagged* as that language). On a confident reject: quarantines the
file to `/mnt/d/quarantine/arr_server` (outside the library), deletes the *arr file
record, blocklists + re-searches via the *arr API, notifies via ntfy — capped at
3 attempts per title before giving up and asking for manual intervention. See
`docs/superpowers/specs/2026-07-04-import-gate-design.md` for the full design.

**Layer-0 grab-time filter (cheap, reduces how often whisper needs to run) —
checked, already in place, nothing new configured:**

- **Radarr**: every quality profile (including the one actually in use,
  `Remux + WEB 2160p`, id 7) already has `language: Original` — Radarr only grabs
  releases tagged with the movie's own original language.
- **Sonarr**: language profile `English` (id 1, marked "Deprecated" by Sonarr's UI
  but still the one assigned) — matches this library's all-English TV catalog.

Webhook connections (`onDownload` + `onUpgrade` → `http://172.39.0.17:8080/webhook`)
are wired in both apps' Settings → Connect.

### Not yet done (needs your review, not automatable)

**Library import**: Radarr/Sonarr already see the existing files in `F:\Media\Movies`
and `F:\Media\TV Shows` as *unmapped folders*, but haven't added them to their library
yet (0 movies/series tracked). Importing means matching each folder to the correct
TMDB/TVDB entry — a content decision, not run automatically. In each app:
**Library → Import** (or **Movies/Series → Add New → Import Existing**), review the
matches, confirm.

## Extras / operations

- **Dashboard (Homepage)** — one pane of glass at `:7575` (host port 7575 → container 3000;
  3000 was taken by another project). Live widgets pull each app's API: Radarr/Sonarr
  (library counts + download queue), qBittorrent (speeds, active torrents), Prowlarr
  (indexers/grabs), Bazarr (subtitle stats), **Gluetun (live VPN public IP/region)**, plus
  per-tile container health/CPU/RAM via the Docker socket (mounted **read-only**). The Gluetun
  tile reads gluetun's control server (`:8000`, internal-only — not published to the host); it's
  currently unauthenticated (fine while the image is pinned). To harden, set up control-server
  auth — note that restarting gluetun re-creates qbit/prowlarr, so do it with `./stack.sh restart`. Config is versionable YAML in
  `/docker/appdata/homepage/*.yaml` (`services.yaml` holds API keys → **not in git**).
  Homepage guards against CSRF with an allow-list of `Host` headers — set every host:port you
  open it by in `HOMEPAGE_ALLOWED_HOSTS` (keep `localhost:3000` there for the container's own
  healthcheck). The **qBittorrent** widget authenticates via qBittorrent's *subnet whitelist*
  (`WebUI\AuthSubnetWhitelist=172.39.0.0/24`) instead of a stored password — only stack
  containers sit on that subnet, so no plaintext qbit password lives in the dashboard config.

### `apply-naming.sh` — Plex-standard root-folder tags

Sets Sonarr/Radarr folder formats to the Plex-recommended tagged form
(`Title (Year) {tvdb-id}` / `{tmdb-id}`) so new library folders match the agent.
Idempotent — safe to re-run; the naming config otherwise lives only in
`/docker/appdata` (outside git), so this script is its version-controlled record.

```bash
./apply-naming.sh              # set the two folder formats (no-op if already set)
./apply-naming.sh --reconcile        # report managed folders still missing a tag (dry-run)
./apply-naming.sh --reconcile --yes  # move those folders to the tagged name (app moves the files)
```

File names are never touched (`renameEpisodes`/`renameMovies` stay off) — only root folders.

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
  ⚠️ **Only this one profile is managed.** The stock profiles (`Any`, `SD`, …,
  ids 1–6) keep **zero** CF scores — BR-DISK/SDR/tier scoring has no effect
  there, so anything is grabbable (2026-07-15 incident: 9 movies + 1 series
  sat on `Any` → two full BDMV discs and an SDR 66 GB remux were grabbed; all
  items moved to the managed profile). addarr gotcha: `excludedQualityProfiles`
  in `/docker/appdata/addarr/config.yaml` takes profile **names**, not ids —
  with ids the exclusion silently no-ops and the Telegram keyboard offers
  `Any` as the first button (fixed 2026-07-15 with names; only the managed
  profile remains, so addarr auto-selects it without asking).
- **Download cleanup (qBittorrent share limits)** — a completed torrent is
  auto-removed **with its data** at **ratio 2** / **7 days of seeding** / **24h
  seeding inactivity** (`max_ratio_act=3`; ⚠️ qBit 5.x enum is non-sequential:
  `3` = remove+files, `2` = super-seeding — verify in `views/preferences.html`
  before changing). Safe because import *copies* to `F:\Media` (cross-filesystem)
  — `/data/torrents` is a transient cache. Caveats: the seeding clocks only tick
  while the stack runs (downtime ⇒ wall-clock age ≫ counter); the limits hit
  **every** completed torrent — give a personal (non-*arr) torrent a per-torrent
  share limit of "No limit" if its data must survive. Stalled *downloads* aren't
  covered (share limits apply only after completion) — blocklist/re-search those
  in Sonarr/Radarr.
- **Fake-release guard (qBittorrent "Excluded file names")** — enabled
  (`Options → Downloads`); matching files inside any new torrent are never
  downloaded. Patterns: `*.exe *.scr *.bat *.cmd *.com *.lnk *.pif *.vbs *.js
  *.jse *.wsf *.wsh *.ps1 *.msi *.hta *.reg` (our anti-malware extension —
  2026-07-15: two fake HotD "episodes" were single 1–1.5 GB `.exe`s served by
  LimeTorrents; Sonarr's importer already refused them, this stops even the
  download) plus `*.rar` / `*.r[0-9]*` (TRaSH's own recommendation — no
  unpackerr in this stack, RARed releases can't import anyway). A torrent whose
  files are **all** excluded completes instantly at 0 bytes and the *arr shows
  "no files eligible" — blocklist it and move on. ⚠️ Global setting: a personal
  torrent that legitimately ships executables needs its files re-enabled by hand
  (torrent → Content tab). Lives in qBittorrent's config (not in git) — this
  note is its version-controlled record.
- **Subtitles (Bazarr)** — profile **EN + PT-BR**, external `.srt` only
  (`use_embedded_subs=false`, so Plex never has to burn). Providers:
  **OpenSubtitles.com** (primary, verified downloading en + pt-BR) + `podnapisi`
  (fallback, auto-recovers when its site is back up). Credentials in Bazarr's
  config (not in git).
- **Notifications (ntfy)** — self-hosted at `:8095`, topic **`arr-media`**.
  Radarr/Sonarr/Prowlarr push grab/import/upgrade/health events. Subscribe: install
  the ntfy app → add server `http://<your-lan-ip>:8095` (or via Tailscale) → topic
  `arr-media`.
- **Self-healing (autoheal)** — restarts any `autoheal=true` container that goes
  unhealthy: gluetun (VPN recovery) and qbit/prowlarr (reconnect if gluetun bounced).
- **Remote access (Tailscale)** — this WSL node is your WSL Tailscale node
  (`<your-tailscale-host>`, `<tailnet-ip>`). All UIs are reachable over the tailnet
  at `http://<your-tailscale-host>:<port>` (encrypted by WireGuard; no LAN
  firewall rule needed). LAN access from other devices still needs the Hyper-V
  firewall rule (see git history).

## Verify no leak

```bash
docker exec qbittorrent wget -qO- https://ipinfo.io/ip   # -> a NordVPN Brazil IP
curl -s https://ipinfo.io/ip                             # -> your real ISP IP (host is NOT on VPN)
```

## qBittorrent MUST be bound to `tun0`

`Session\Interface=tun0` / `Session\InterfaceName=tun0` in `qBittorrent.conf`. This is
both correctness and kill-switch: bound to the tunnel, qBit simply stops if the VPN
drops instead of falling back to `eth0`.

With the binding left empty ("Any interface") qBit enumerates interfaces at startup
and can silently come up **without** `tun0` — it then binds `eth0` only, and every
peer/tracker packet leaves with source `172.39.0.2` (gluetun's `eth0`), hits its
`ip rule 100 -> table 200 -> eth0`, and dies on the `OUTPUT DROP`. Symptom: the whole
client goes to 0 peers.

**This failure mode looks exactly like "all my torrents are dead releases."** It is not.
Every download stalls at once — including ones already at 80%+ — and Radarr reports
`The download is stalled with no connections`. Before blocklisting anything, check the
qBit log for the bind list:

```bash
# Must list 10.5.0.2 (tun0). If it only shows 172.39.0.2 / 127.0.0.1, the bind is wrong.
docker exec qbittorrent grep -E 'listen on|Successfully listening' \
  /config/data/logs/qbittorrent.log | tail
```

Corroborating signals: tracker rows showing `Operation not permitted` (that string is
EPERM from the firewall, *not* a dead tracker), no `Detected external IP` line in the
log, and the IP-geolocation DB download timing out. A torrent whose swarm really is
dead reads `num_complete 0 / num_incomplete 0` — check that *after* confirming the
bind, never before.

### Why this can happen even with `Session\Interface=tun0` already set

`Session\Interface=tun0` being persisted in `qBittorrent.conf` is **not** sufficient on
its own. Confirmed live on 2026-07-22: `docker compose`'s `depends_on: condition:
service_healthy` only orders startup when containers are launched via `docker compose
up`. On a **daemon-driven restart** — host reboot, `dockerd` restart, or a bare `docker
restart` touching both containers — the Docker Engine restarts each container per its
own `restart:` policy and does not consult `depends_on` at all. If qBittorrent's process
starts before gluetun's `tun0` has an IPv4 address, it silently discards the
`Session\Interface=tun0` setting (no error logged) and falls back to binding
`0.0.0.0`/`eth0` — the exact failure mode described above. It does **not** self-correct:
in the observed incident it stayed misbound for 15 minutes until a manual WebUI
preferences save forced a rebind.

Two guards close this gap, both in `compose.yaml` on the `qbittorrent` service only:

1. **Entrypoint wait.** `qbittorrent`'s `entrypoint` blocks in a loop
   (`until ip -4 addr show tun0 2>/dev/null | grep -q inet; do sleep 2; done`) until `tun0` actually
   has an address, before handing off to the image's own `/init`. No timeout — this is
   kill-switch semantics: without a tunnel, qBittorrent must never start.
2. **Bind-aware healthcheck.** The healthcheck test became
   `ss -tln | grep -q '%tun0:' && curl -sf ...` — the old version (`curl` alone) only
   proves the *container's* default route works, and stayed `healthy` for the entire
   15-minute window of the incident above while qBittorrent itself was bound wrong. The
   `%tun0` suffix in `ss` output only appears for a socket bound to that device **by
   name**, so it also survives `tun0` getting a new address on VPN redial. A bad bind
   flips the container `unhealthy`, and the stack's existing `autoheal` service (see
   the `autoheal` service block) recycles it — which re-enters the entrypoint wait on
   the next boot.

During a **sustained** VPN provider outage (tun0 never comes back), this is expected to
repeat on a ~6-minute cadence (`start_period` + `interval × retries` before `unhealthy`,
then an immediate autoheal recycle back into the entrypoint's wait) for as long as the
outage lasts. That churn is the kill-switch working as intended — qBittorrent never
binds wrong — not a new fault; live-validated 2026-07-22 by holding gluetun's VPN down
for ~4m15s and observing exactly this cycle.

Design rationale and the full failure-mode matrix:
`docs/superpowers/specs/2026-07-22-qbit-tun0-bind-race-design.md`.
