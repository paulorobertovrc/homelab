# Diun Update Notifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar o Diun (Docker Image Update Notifier) ao stack: vigia os digests das imagens dos containers e notifica no ntfy (tópico `arr-media`, já assinado no celular) quando sai versão nova — fechando o último item de "higiene" da Etapa 2. Só notifica; **nunca** atualiza nada.

**Architecture:** Um serviço novo no `compose.yaml` usando o provider Docker do Diun (socket read-only, mesmo padrão do Homepage). `watchByDefault=true` — todo container atual e futuro é vigiado automaticamente; as duas imagens **buildadas localmente** (`import-gate`, `suggest-bot`) são excluídas por label `diun.enable=false` (não existem em registry → dariam erro de lookup toda checagem). Notificação via notifier ntfy nativo do Diun apontando pro ntfy interno (mesmo alvo dos alertas do import-gate/suggest-bot/backup). Checagem semanal.

**Tech Stack:** `crazymax/diun:latest` (convenção do stack: tags rolling, exceto gluetun pinado), ntfy interno (`SET_IP_NTFY:80`), docker compose.

## Global Constraints

- Docker socket **read-only** (`/var/run/docker.sock:/var/run/docker.sock:ro`) — Diun só lê; precedente: homepage (ro), autoheal (rw por necessidade).
- Estado persistente em `${CONFIG_ROOT}/diun` (bolt db) — sem ele, todo restart re-notificaria tudo.
- IP estático novo: `.21` (livre no `.env` live — usados: .2–.6, .10, .14–.20). Default no compose segue a convenção do `.env.example` (`172.31.0.21`); o `.env` live usa a subnet real (`172.39.0.21`).
- Primeira execução **não** notifica (default `firstchecknotif=false` do Diun) — sem enxurrada inicial; a primeira checagem apenas popula o estado.
- Sem widget no Homepage (YAGNI — o card de containers via docker socket já mostra o Diun rodando; widget dedicado só se sentir falta depois).

---

### Task 1: Serviço diun no compose + exclusão das imagens locais

**Files:**
- Modify: `compose.yaml` (novo serviço ao final; labels em `import-gate` e `suggest-bot`)
- Modify: `.env.example` (SET_IP_DIUN)

**Interfaces:**
- Consumes: `SET_IP_NTFY` (ntfy interno, já existente), `CONFIG_ROOT`, `TZ`.
- Produces: serviço `diun` (container `diun`) que a Task 2 valida.

- [ ] **Step 1: Adicionar o serviço ao `compose.yaml`** (após o bloco `homepage`)

```yaml
  ###############################################
  # DIUN — Docker Image Update Notifier
  # Watches every container's image digest (docker socket, read-only) and
  # notifies ntfy topic arr-media when upstream publishes a new one.
  # Notify-only by design: updating remains a manual, deliberate act.
  # Locally-built images (import-gate, suggest-bot) opt out via label —
  # they don't exist in any registry, lookups would just error weekly.
  ###############################################
  diun:
    image: crazymax/diun:latest
    container_name: diun
    restart: unless-stopped
    networks:
      servarr_network:
        ipv4_address: ${SET_IP_DIUN:-172.31.0.21}
    environment:
      - TZ=${TZ:-America/Cuiaba}
      - LOG_LEVEL=info
      - DIUN_WATCH_WORKERS=10
      # Weekly check, Monday 06:00 local; jitter avoids thundering-herd on registries.
      - DIUN_WATCH_SCHEDULE=0 6 * * 1
      - DIUN_WATCH_JITTER=30s
      # Also check once on container start: makes validation immediate and a
      # stack reboot self-heals a missed schedule. State db dedups notifications.
      - DIUN_WATCH_RUNONSTARTUP=true
      - DIUN_PROVIDERS_DOCKER=true
      - DIUN_PROVIDERS_DOCKER_WATCHBYDEFAULT=true
      - DIUN_NOTIF_NTFY_ENDPOINT=http://${SET_IP_NTFY:-172.31.0.10}:80
      - DIUN_NOTIF_NTFY_TOPIC=arr-media
    volumes:
      - ${CONFIG_ROOT:-/docker/appdata}/diun:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

- [ ] **Step 2: Excluir as duas imagens locais por label**

Em `import-gate` (serviço com `build: ./import-gate`) e `suggest-bot` (`build: ./suggest-bot`), adicionar a cada um:

```yaml
    labels:
      - diun.enable=false   # locally-built image — no registry to check
```

(Atenção: `import-gate` hoje não tem bloco `labels:`; criar. Idem `suggest-bot`.)

- [ ] **Step 3: `.env.example` — IP estático**

Na seção Network, após `SET_IP_SUGGEST_BOT`:

```bash
SET_IP_DIUN=172.31.0.21
```

- [ ] **Step 4: Validar renderização**

```bash
cd /home/prvrc/dev/homelab/media
docker compose config --quiet && echo OK
```

Expected: `OK` (sem erro de sintaxe/env).

- [ ] **Step 5: Adicionar ao `.env` live (usuário/executor)**

```bash
# em media/.env, seção dos SET_IP:
SET_IP_DIUN="172.39.0.21"
```

(Subnet live é `172.39.0.0/24`, diferente do default do example — conferir com `grep SERVARR_SUBNET .env` antes.)

---

### Task 2: Subir, validar vigilância e notificação real

**Files:** nenhum (operação/validação).

**Interfaces:**
- Consumes: serviço `diun` da Task 1.
- Produces: evidência de funcionamento (lista de imagens vigiadas + notificação de teste recebida no celular).

- [ ] **Step 1: Subir só o serviço novo**

```bash
docker compose up -d diun
docker logs diun --tail 30
```

Expected: logs com `Starting Diun`, provider docker detectado, e (por `RUNONSTARTUP=true`) uma primeira checagem: `Found N image(s) to analyze` com N ≈ 14 (todas as imagens de registry; **sem** erros de lookup para import-gate/suggest-bot).

- [ ] **Step 2: Conferir a lista de imagens vigiadas**

```bash
docker compose exec diun diun image list
```

Expected: gluetun, qbittorrent, prowlarr, radarr, sonarr, bazarr, flaresolverr, autoheal, recyclarr, ntfy, tailscale/tailscale, addarr, jellyseerr, homepage, diun — e **nenhuma** entrada para import-gate/suggest-bot. Se as locais aparecerem: labels da Task 1 Step 2 não aplicadas (recriar os containers com `docker compose up -d import-gate suggest-bot` — labels só valem após recreate).

- [ ] **Step 3: Notificação de teste ponta-a-ponta**

```bash
docker compose exec diun diun notif test
```

Expected: saída confirmando envio ao notifier `ntfy`, e a notificação de teste **chegando no celular** (app ntfy, tópico `arr-media`). Evidência antes de asserção: só declarar validado após ver a notificação.

- [ ] **Step 4: Verificar estado persistido (sobrevive a restart sem re-notificar)**

```bash
ls -la /docker/appdata/diun/
docker restart diun && sleep 20 && docker logs diun --tail 10
```

Expected: `diun.db` existe; após restart, a checagem de startup roda de novo e **não** dispara notificações repetidas (digests já conhecidos no estado).

- [ ] **Step 5: Commit**

```bash
git add compose.yaml .env.example
git commit -m "feat(media): diun image-update notifier -> ntfy arr-media

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Documentar no README + fechar o item de higiene

**Files:**
- Modify: `README.md` (seção de serviços/observabilidade)

- [ ] **Step 1: README** — adicionar o Diun à lista de serviços, no mesmo nível de detalhe dos vizinhos (autoheal/ntfy): o que vigia (digest das imagens, semanal, segunda 06:00), pra onde notifica (`ntfy/arr-media`), o que NÃO faz (não atualiza — update é ato manual), e a exclusão das imagens buildadas localmente via `diun.enable=false`.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(media): document diun in the service roster

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 3:** Atualizar a memória do backlog (higiene da Etapa 2 → completa: Diun ✓) — tarefa mecânica.

---

## Self-Review (executada na escrita)

1. **Cobertura:** notificar updates ✓ (Task 1) · não atualizar nada ✓ (notify-only por design) · canal existente ✓ (ntfy/arr-media) · imagens locais sem ruído ✓ (labels) · validação e2e ✓ (Task 2 Step 3 exige a notificação no celular).
2. **Placeholders:** nenhum — todo step tem código/comando completo e saída esperada.
3. **Consistência:** `SET_IP_DIUN` usado igual em compose/example/live; nomes de serviço (`import-gate`, `suggest-bot`) conferidos contra o compose real.
