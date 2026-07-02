# Media stack (Servarr) — homelab WSL2

Radarr / Sonarr / Bazarr / Prowlarr / qBittorrent behind a Mullvad WireGuard
kill‑switch (gluetun). Runs on **WSL2 (Ubuntu 26.04)**; **Plex on the Windows host**
serves the final library.

## VPN scope — only the stack, never the machine

The VPN lives **inside the gluetun container's network namespace**. Nothing on the
host (Windows, Plex, your browsing, other containers) touches it.

| Component | Egress path |
|---|---|
| **qBittorrent** | 🔒 Mullvad (`network_mode: service:gluetun`) |
| **Prowlarr** | 🔒 Mullvad (`network_mode: service:gluetun`) |
| Radarr / Sonarr / Bazarr | 🌐 normal network (talk to TMDB/TVDB metadata; reach qbit/prowlarr through gluetun) |
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
qBittorrent `:8080` · Prowlarr `:9696` · Radarr `:7878` · Sonarr `:8989` · Bazarr `:6767`.

---

## Finish setup (2 steps)

1. **Fill the Mullvad WireGuard values** in `media/.env` (Account → WireGuard →
   Generate key): `WIREGUARD_PRIVATE_KEY` (already set) and `WIREGUARD_ADDRESSES`
   (the `Address = …` line, still empty).

2. **Start it** (from `media/`):
   ```bash
   docker compose up -d
   docker compose logs -f gluetun     # wait for "healthy" / an IP
   ```
   qbit/prowlarr only start once gluetun is healthy (by design).

## First‑run app config

- **qBittorrent** (`:8080`, temp password in `docker logs qbittorrent`):
  Save path `/data/torrents/complete`, incomplete `/data/torrents/incomplete`.
- **Prowlarr** (`:9696`): add indexers; Settings → Apps → add Radarr `http://172.39.0.4:7878`
  and Sonarr `http://172.39.0.3:8989`.
- **Radarr** (`:7878`): root folder `/data/media/Movies`; download client qBittorrent
  host `172.39.0.2` port `8080` (paths already match — no remote path mapping).
- **Sonarr** (`:8989`): root folder `/data/media/TV Shows`; same download client.
- **Bazarr** (`:6767`): connect Radarr `172.39.0.4:7878` + Sonarr `172.39.0.3:8989`.
- **Plex (Windows)**: libraries point to `F:\Media\Movies` and `F:\Media\TV Shows`.

## Verify no leak (after it's up)

```bash
docker exec qbittorrent wget -qO- https://ipinfo.io/ip   # -> a Mullvad IP
curl -s https://ipinfo.io/ip                             # -> your real IP (host is NOT on VPN)
```
