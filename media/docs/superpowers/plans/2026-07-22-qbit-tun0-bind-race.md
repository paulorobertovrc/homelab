# qBittorrent tun0 Bind Race — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the qBittorrent tun0 bind race (daemon-driven restarts bypass compose's `depends_on`, letting qBit boot before tun0 has an IPv4 address and silently fall back to binding `0.0.0.0`/eth0) with a wait-for-tun0 entrypoint plus a bind-aware healthcheck that lets the existing autoheal service recycle the container if the bind is ever wrong.

**Architecture:** Two independent guards added to the `qbittorrent` service in `compose.yaml` only — no changes to the image, gluetun, or firewall/routing. Guard 1 (prevention) overrides `entrypoint` with an inline bash wait-loop that blocks until `tun0` has an IPv4 address, then hands off to the image's own `/init`. Guard 2 (detection+cure) replaces the healthcheck test to additionally require a `ss`-visible listener bound to `%tun0`, so a bad bind flips the container unhealthy and the pre-existing `autoheal` container recycles it — which re-enters Guard 1's wait on the next boot.

**Tech Stack:** Docker Compose (compose.yaml), bash/iproute2/iproute2-ss/curl (already present in the `ghcr.io/hotio/qbittorrent:release` image), `willfarrell/autoheal` (already deployed in this stack).

## Global Constraints

- No bind-mounts over hotio image internals (`/etc/s6-overlay/...`) — must survive `:release` tag updates. (spec §Guarda 1)
- Guard 1 has **no timeout** — kill-switch semantics: without a tunnel, qBittorrent must never start. (spec §Guarda 1)
- Guard 1 readiness check is **tun0 has an IPv4 address**, not merely "interface exists/UP". (spec §Guarda 1)
- Guard 1 must `exec /init` (not just run it as a child process) so s6-overlay remains PID 1, and must trap `TERM`/`INT` so `docker stop` during the wait window doesn't hang for the full SIGKILL grace period. (spec §Guarda 1)
- Guard 2 must keep the existing `interval`/`timeout`/`retries`/`start_period` values unchanged — only the `test` command changes. (spec §Guarda 2)
- Guard 2's bind check must key on the interface **name** (`%tun0`), not an IP address, so it survives tun0 getting a different address on redial. (spec §Guarda 2)
- No changes to gluetun, its firewall, or its routing — already ruled out as the cause. (spec §Fora de escopo)
- No WebUI-API rebind watchdog, no migration to the hotio image's built-in WireGuard — explicitly out of scope (YAGNI). (spec §Fora de escopo)

---

### Task 1: Entrypoint wait-for-tun0 guard

**Files:**
- Modify: `compose.yaml:71-96` (qbittorrent service block)

**Interfaces:**
- Consumes: nothing from other tasks (first task).
- Produces: an `entrypoint:` override on the `qbittorrent` service. Task 2 modifies the same service's `healthcheck:` key — both tasks touch the same YAML block, so Task 2 must be applied after this one lands (sequential, not parallel).

- [ ] **Step 1: Add the entrypoint override to `compose.yaml`**

Current block (`compose.yaml:71-78`):

```yaml
  qbittorrent:
    <<: *common-keys
    image: ghcr.io/hotio/qbittorrent:release
    container_name: qbittorrent
    network_mode: service:gluetun
    depends_on:
      gluetun:
        condition: service_healthy
    environment:
```

Replace with (inserts `entrypoint:` between `network_mode` and `depends_on`):

```yaml
  qbittorrent:
    <<: *common-keys
    image: ghcr.io/hotio/qbittorrent:release
    container_name: qbittorrent
    network_mode: service:gluetun
    # Guards against the 2026-07-22 boot race: on a daemon-driven restart (host
    # reboot, dockerd restart, `docker restart` outside `docker compose up`),
    # Docker restarts each container per its own `restart:` policy and ignores
    # `depends_on: condition: service_healthy` entirely — that gate is a
    # compose-CLI construct, not enforced by the Engine. If qBittorrent's
    # process starts before gluetun's tun0 has an address, it silently
    # discards `Session\Interface=tun0` and falls back to binding `0.0.0.0`
    # (eth0), defeating the kill-switch at the bind level. See README.md
    # "qBittorrent MUST be bound to tun0".
    entrypoint:
      - /bin/bash
      - -c
      - |
        trap 'exit 0' TERM INT
        until ip -4 addr show tun0 2>/dev/null | grep -q inet; do
          echo "[tun0-wait] tun0 sem IPv4; aguardando VPN..."
          sleep 2
        done
        echo "[tun0-wait] tun0 pronto; iniciando qBittorrent."
        exec /init
    depends_on:
      gluetun:
        condition: service_healthy
    environment:
```

- [ ] **Step 2: Validate the compose file parses**

Run: `cd /home/prvrc/dev/homelab/media && docker compose config --quiet`
Expected: no output, exit code 0. (This only validates YAML/interpolation — it does not start anything.)

- [ ] **Step 3: Boot test — normal `docker compose up` path**

Run:
```bash
cd /home/prvrc/dev/homelab/media
docker compose up -d qbittorrent
sleep 5
docker logs qbittorrent --since 30s | grep -i 'tun0-wait'
docker exec qbittorrent grep -E 'Trying to listen|Successfully listening' \
  /config/data/logs/qbittorrent.log | tail -10
```
`tun0-wait` lines come from the entrypoint's own `echo` and appear in `docker logs`
(container stdout). The app-level bind confirmation (`Trying to listen` /
`Successfully listening`) is written by qBittorrent to its internal log file,
**never** to container stdout — it must be read from
`/config/data/logs/qbittorrent.log` inside the container, not grepped out of
`docker logs`. (A first draft of this step mixed the two sources in one grep
against `docker logs`; that command can never match the app-level line — fixed
here after a task review caught it.)

Expected: either no `tun0-wait` lines (tun0 was already up when the entrypoint ran)
or one/more `[tun0-wait] tun0 sem IPv4; aguardando VPN...` lines followed by
`[tun0-wait] tun0 pronto; iniciando qBittorrent.`. In both cases, the qBittorrent
log tail's most recent bind must show `"tun0:34124"` → `Successfully listening on
IP. IP: "10.5.0.2"` — **never** `"172.39.0.2"` (eth0) or `"0.0.0.0"`.

- [ ] **Step 4: Commit**

```bash
cd /home/prvrc/dev/homelab/media
git add compose.yaml
git commit -m "fix(media): wait for tun0 IPv4 before starting qBittorrent

Daemon-driven restarts (host reboot, dockerd restart) bypass compose's
depends_on: condition: service_healthy entirely — that gate only
applies under \`docker compose up\`. Without it, qBittorrent's process
can start before gluetun's tun0 has an address, silently discarding
Session\\Interface=tun0 and falling back to 0.0.0.0/eth0 (kill-switch
bypassed at the bind level). Confirmed live 2026-07-22 08:42:03.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Bind-aware healthcheck guard

**Files:**
- Modify: `compose.yaml:89-94` (qbittorrent service `healthcheck:` block — line numbers shift by ~13 after Task 1's insertion; locate by the `curl -sf -m 10 https://www.google.com` string instead of by line number)

**Interfaces:**
- Consumes: the `qbittorrent` service block as left by Task 1 (entrypoint already in place).
- Produces: an updated `healthcheck.test` that later tasks (Task 3 docs, Task 4 validation) reference verbatim when describing/verifying behavior.

- [ ] **Step 1: Replace the healthcheck test**

Current (comment + healthcheck block, originally `compose.yaml:88-94`):

```yaml
    # If gluetun restarts, this loses connectivity -> unhealthy -> autoheal restarts it.
    healthcheck:
      test: ["CMD-SHELL", "curl -sf -m 10 https://www.google.com -o /dev/null || exit 1"]
      interval: 60s
      timeout: 15s
      retries: 4
      start_period: 120s
```

Replace with:

```yaml
    # If gluetun restarts, this loses connectivity -> unhealthy -> autoheal restarts it.
    # The ss check catches the OTHER failure mode: qBittorrent bound to 0.0.0.0/eth0
    # instead of tun0 (see the entrypoint comment above and README.md "qBittorrent
    # MUST be bound to tun0"). `%tun0` in `ss` output only appears for a socket
    # actually bound to that device by name, so it's immune to tun0 getting a new
    # IP on VPN redial. A bad bind -> unhealthy -> autoheal recycles the container
    # -> re-enters the entrypoint's wait-for-tun0 loop on the next boot.
    healthcheck:
      test: ["CMD-SHELL", "ss -tln | grep -q '%tun0:' && curl -sf -m 10 https://www.google.com -o /dev/null || exit 1"]
      interval: 60s
      timeout: 15s
      retries: 4
      start_period: 120s
```

- [ ] **Step 2: Validate the compose file parses**

Run: `cd /home/prvrc/dev/homelab/media && docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 3: Apply and confirm healthy**

Run:
```bash
cd /home/prvrc/dev/homelab/media
docker compose up -d qbittorrent
sleep 15
docker exec qbittorrent ss -tln | grep '%tun0:'
docker inspect qbittorrent --format '{{.State.Health.Status}}'
```
Expected: the `ss` line shows something like `LISTEN 0 30 10.5.0.2%tun0:34124 0.0.0.0:*`, and `docker inspect` prints `starting` (still inside `start_period`) or `healthy` — never `unhealthy`.

- [ ] **Step 4: Commit**

```bash
cd /home/prvrc/dev/homelab/media
git add compose.yaml
git commit -m "fix(media): healthcheck fails if qBittorrent isn't bound to tun0

The prior healthcheck (plain curl to google.com) only proves gluetun's
own egress route works — it uses the container's default route and
stayed 'healthy' for the full 15 minutes of the 2026-07-22 incident
while qBittorrent itself was bound to 0.0.0.0/eth0. Checking for a
%tun0-suffixed listener in \`ss\` output verifies the actual libtorrent
bind by interface name, so autoheal now recycles the container if this
regresses (bad bind at boot, or a redial that doesn't self-rebind).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: README documentation update

**Files:**
- Modify: `README.md:257-284` (the "qBittorrent MUST be bound to `tun0`" section)

**Interfaces:**
- Consumes: the exact `entrypoint` and `healthcheck.test` strings introduced in Tasks 1–2 (quoted verbatim below).
- Produces: nothing consumed by later tasks — this is documentation only.

- [ ] **Step 1: Append a subsection after the existing "Corroborating signals" paragraph**

Current end of section (`README.md:280-284`):

```markdown
Corroborating signals: tracker rows showing `Operation not permitted` (that string is
EPERM from the firewall, *not* a dead tracker), no `Detected external IP` line in the
log, and the IP-geolocation DB download timing out. A torrent whose swarm really is
dead reads `num_complete 0 / num_incomplete 0` — check that *after* confirming the
bind, never before.
```

Append immediately after it (new content, same file):

```markdown

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
   (`until ip -4 addr show tun0 | grep -q inet; do sleep 2; done`) until `tun0` actually
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

Design rationale and the full failure-mode matrix:
`docs/superpowers/specs/2026-07-22-qbit-tun0-bind-race-design.md`.
```

- [ ] **Step 2: Confirm the doc renders sensibly**

Run: `cd /home/prvrc/dev/homelab/media && sed -n '250,320p' README.md`
Expected: the new subsection appears directly after the existing "Corroborating signals" paragraph, with no broken Markdown (matched code fences, no stray backticks).

- [ ] **Step 3: Commit**

```bash
cd /home/prvrc/dev/homelab/media
git add README.md
git commit -m "docs(media): explain the tun0 boot race and the two new guards

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Validation — boot, daemon-restart race, and VPN redial

**Validation result:** Run 2026-07-22. All three scenarios passed. Step 1 (`docker compose restart qbittorrent`): clean `tun0:34124` → `10.5.0.2` bind, no `0.0.0.0`/`172.39.0.2`. Step 2 (`docker restart gluetun qbittorrent`, bypassing `depends_on`): entrypoint logged `[tun0-wait] tun0 pronto; iniciando qBittorrent.` (zero `sem IPv4; aguardando` lines — gluetun's tun0 was already up by the time qBittorrent's entrypoint checked, so no actual race this run, but the guard executed correctly); bind stayed clean. Step 3 (VPN stop/start via gluetun control server, no container restart): outcome **(a) self-rebind** — `ss -tln | grep '%tun0:'` showed no match immediately after `{"status":"stopped"}`, then showed `10.5.0.2%tun0:34124` again ~15s after `{"status":"running"}`; `RestartCount` stayed at 0 throughout; the health-check log shows a passing check at 10:20:58 (before the stop) and the next passing check at 10:21:58 (after tun0 was already restored) — no failing check was ever recorded, i.e. the down-window was shorter than the health-check interval and libtorrent rebound on its own before autoheal could have intervened.

**Files:**
- None modified. This task only runs commands against the live stack to validate Tasks 1–3.

**Interfaces:**
- Consumes: the `entrypoint` wait loop (Task 1) and `healthcheck.test` (Task 2) exactly as committed — this task does not modify either.
- Produces: a pass/fail record for each scenario, appended to the plan file itself (Step 4 below) so the outcome is preserved next to the plan.

- [ ] **Step 1: Boot scenario — `docker compose restart`**

Run:
```bash
cd /home/prvrc/dev/homelab/media
docker compose restart qbittorrent
sleep 10
docker exec qbittorrent grep -E 'Trying to listen|Successfully listening' \
  /config/data/logs/qbittorrent.log | tail -10
```
Expected: the most recent "Trying to listen" line reads `"tun0:34124"` (not `"0.0.0.0:34124,[::]:34124"`), immediately followed by `Successfully listening on IP. IP: "10.5.0.2"` lines. No `0.0.0.0` or `172.39.0.2` bind in this boot's tail.

- [ ] **Step 2: Race scenario (the decisive test) — daemon-driven restart bypassing `depends_on`**

This reproduces the exact incident: restarting both containers directly via the Docker
Engine (not `docker compose up`), which does not honor `depends_on` ordering.

Run:
```bash
cd /home/prvrc/dev/homelab/media
docker restart gluetun qbittorrent
sleep 10
docker logs qbittorrent --since 30s | grep -i 'tun0-wait'
docker exec qbittorrent grep -E 'Trying to listen|Successfully listening' \
  /config/data/logs/qbittorrent.log | tail -10
docker exec qbittorrent ss -tln | grep '%tun0:'
```
Expected: zero or more `[tun0-wait] tun0 sem IPv4; aguardando VPN...` lines (proving the
wait loop engaged if there was any race at all) followed by `[tun0-wait] tun0 pronto;
iniciando qBittorrent.`; the qBittorrent app log's most recent bind is `"tun0:34124"` →
`10.5.0.2`, never `0.0.0.0`; and the `ss` line is present.

- [ ] **Step 3: VPN redial scenario — recreate tun0 without restarting either container**

This targets the previously-untested case: gluetun's own VPN reconnecting mid-run
(tun0 recreated inside the same network namespace, no container restart).

Run:
```bash
docker exec gluetun wget -qO- --method=PUT --body-data='{"status":"stopped"}' \
  --header='Content-Type: application/json' http://127.0.0.1:8000/v1/vpn/status
sleep 5
docker exec gluetun wget -qO- --method=PUT --body-data='{"status":"running"}' \
  --header='Content-Type: application/json' http://127.0.0.1:8000/v1/vpn/status
sleep 15
docker exec qbittorrent ss -tln | grep '%tun0:'
docker inspect qbittorrent --format '{{.State.Health.Status}}'
docker inspect qbittorrent --format 'RestartCount: {{.RestartCount}}'
```
Record which of the two acceptable outcomes occurred:
- **(a)** the `ss` grep still shows `%tun0:` and `RestartCount` did **not** increase —
  libtorrent re-bound on its own; or
- **(b)** the `ss` grep initially shows no match / health goes `unhealthy`, then within
  ~6 minutes (`interval`×`retries` + `start_period` margin) autoheal recycles the
  container (`RestartCount` increases by 1) and the `ss` grep then shows `%tun0:` again.

Both are acceptable per the spec's failure-mode matrix. If neither occurs (bind stays
wrong and no restart happens after 10 minutes), that is a plan failure — stop and
re-open the design.

- [ ] **Step 4: Record the outcome in this plan file**

Edit this file (`docs/superpowers/plans/2026-07-22-qbit-tun0-bind-race.md`) to append,
directly under this Task 4 header, a `**Validation result:**` line stating the date run
and which outcome ((a) or (b)) occurred in Step 3, then commit:

```bash
cd /home/prvrc/dev/homelab/media
git add docs/superpowers/plans/2026-07-22-qbit-tun0-bind-race.md
git commit -m "docs(media): record tun0 guard validation results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** Guarda 1 (entrypoint wait) → Task 1. Guarda 2 (healthcheck) → Task 2.
  README section → Task 3. All three validation scenarios from the spec (boot, race,
  redial) → Task 4 Steps 1–3. Failure-mode matrix is fully exercised. Out-of-scope items
  (API watchdog, hotio built-in WireGuard, gluetun changes) are intentionally absent —
  matches spec.
- **Placeholder scan:** no TBD/TODO; every step has literal commands and exact
  before/after YAML or Markdown blocks.
- **Type/interface consistency:** the `healthcheck.test` string is identical, character
  for character, across Task 2 Step 1, Task 3 Step 1, and Task 4 Steps 1–2 references.
  The `entrypoint` block is identical across Task 1 Step 1 and Task 3 Step 1's summary.
