# AirVPN Port-Forward Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar o gluetun de NordVPN (sem port forwarding → qBittorrent firewalled → stalls em swarms públicos) para AirVPN (porta estática reservada no dashboard), **sem quebrar a configuração Nord atual em nenhum momento** — a troca real acontece só via `.env`, com rollback trivial.

**Architecture:** O `compose.yaml` já é env-driven (`VPN_SERVICE_PROVIDER`, chaves, etc. vêm do `.env` gitignored). A migração tem 3 fases: **Fase 0** (repo) adiciona duas env vars novas com default vazio — inócuas para a Nord; **Fase 1** (trial, só `.env` + operação) compra plano AirVPN de €2/3 dias, troca o `.env`, valida conectabilidade e velocidade com gates de decisão; **Fase 2** consolida (assina 1 ano + docs) ou faz rollback (restaura `.env`). Nenhuma fase altera o maquinário tun0 existente (entrypoint-wait, healthcheck `%tun0`, autoheal).

**Tech Stack:** gluetun v3.41 (já pinado), AirVPN WireGuard (Config Generator), qBittorrent (hotio), docker compose.

## Global Constraints

- **Nunca quebrar a Nord até a decisão final**: toda mudança no repo precisa render config idêntica com o `.env` atual (verificado com `docker compose config` diff).
- **Segredos só no `.env`** (gitignored) — chave privada/preshared da AirVPN jamais em arquivo versionado.
- **Não tocar** no entrypoint do qBittorrent, healthchecks, autoheal, IPs estáticos (`172.39.0.0/24` no `.env` live).
- **Mullvad está descartada (decisão do usuário, 2026-07-23)** — a disputa é só Nord × AirVPN. A Task 1 expurga as referências à Mullvad do repo (default do compose passa a `nordvpn`, bloco Mullvad do `.env.example` sai); seguro porque o `.env` live define `VPN_SERVICE_PROVIDER=nordvpn` explicitamente, então o default nunca é consultado.
- Recriar o gluetun recria também qbittorrent/prowlarr/flaresolverr (`network_mode: service:gluetun`) — executar em momento ocioso (sem download ativo importante; seeds pausam ~1–2 min, contadores de seed congelam com stack down — comportamento conhecido).
- `WIREGUARD_ADDRESSES` da AirVPN: **remover o endereço IPv6** (caveat do wiki do gluetun; o setup Docker/WSL2 não tem IPv6 configurado).
- Fatos verificados 2026-07-23: gluetun suporta AirVPN via WireGuard exigindo `WIREGUARD_PRIVATE_KEY` + `WIREGUARD_PRESHARED_KEY` + `WIREGUARD_ADDRESSES` do Config Generator, porta entra via `FIREWALL_VPN_INPUT_PORTS` ([wiki gluetun/AirVPN](https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/airvpn.md), [wiki port forwarding](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/vpn-port-forwarding.md)). AirVPN: até 5 portas por conta, reservadas em [airvpn.org/ports](https://airvpn.org/ports/); **Brasil tem 1 servidor só (Peony, São Paulo)** — medir no trial.

---

### Task 1 (Fase 0): Expor as env vars da AirVPN e expurgar Mullvad — sem afetar a Nord

**Files:**
- Modify: `compose.yaml` (bloco `gluetun.environment`, linhas ~48–59)
- Modify: `.env.example` (seção VPN)

**Interfaces:**
- Produces: vars `WIREGUARD_PRESHARED_KEY` e `FIREWALL_VPN_INPUT_PORTS` consumíveis pelo `.env` (Task 2 depende delas); repo sem nenhuma referência à Mullvad.

- [ ] **Step 1: Snapshot da config renderizada atual (baseline)**

```bash
cd /home/prvrc/dev/homelab/media
docker compose config > /tmp/claude-1000/-home-prvrc-dev-homelab-media/*/scratchpad/compose-before.yaml 2>/dev/null \
  || docker compose config > /tmp/compose-before.yaml
```

Expected: arquivo gerado sem erro (a config atual, com Nord, renderiza limpa).

- [ ] **Step 2: Editar `compose.yaml` — adicionar as duas vars com default vazio e remover Mullvad**

No bloco `environment:` do serviço `gluetun`: trocar o default do provider (linha ~49) de `:-mullvad` para `:-nordvpn` e reescrever o trecho do WireGuard assim:

```yaml
      - VPN_SERVICE_PROVIDER=${VPN_SERVICE_PROVIDER:-nordvpn}
      - VPN_TYPE=${VPN_TYPE:-wireguard}
      # OpenVPN alternative (leave commented for WireGuard):
      #- OPENVPN_USER=${OPENVPN_USER}
      #- OPENVPN_PASSWORD=${OPENVPN_PASSWORD}
      - WIREGUARD_PRIVATE_KEY=${WIREGUARD_PRIVATE_KEY:?Set WIREGUARD_PRIVATE_KEY in media/.env}
      # WIREGUARD_ADDRESSES: AirVPN requires it (IPv4 only); NordVPN sets it automatically — leave unset on NordVPN.
      - WIREGUARD_ADDRESSES=${WIREGUARD_ADDRESSES:-}
      # AirVPN only (empty/ignored on NordVPN): preshared key from AirVPN's
      # Config Generator, and the port reserved at airvpn.org/ports (static, per-account).
      # Empty default keeps this a no-op for providers that don't use them.
      - WIREGUARD_PRESHARED_KEY=${WIREGUARD_PRESHARED_KEY:-}
      - FIREWALL_VPN_INPUT_PORTS=${FIREWALL_VPN_INPUT_PORTS:-}
```

(Única referência à Mullvad no `compose.yaml` são essas duas linhas — o default e o comentário; confirmar com `grep -in mullvad compose.yaml` → vazio após a edição.)

- [ ] **Step 3: Editar `.env.example` — substituir o bloco Mullvad pelo par Nord/AirVPN**

Substituir a seção VPN inteira (de `VPN_SERVICE_PROVIDER=mullvad` até `WIREGUARD_ADDRESSES=`, incluindo o comentário "From your Mullvad WireGuard config") por:

```bash
# ── VPN (gluetun) — NordVPN (current) or AirVPN (port forwarding) ────
VPN_SERVICE_PROVIDER=nordvpn
VPN_TYPE=wireguard
SERVER_COUNTRIES=Brazil
HEALTH_VPN_DURATION_INITIAL=30s

# NordVPN: NordLynx private key from the host CLI; leave WIREGUARD_ADDRESSES
# unset (Nord sets it automatically) and the AirVPN-only vars empty.
WIREGUARD_PRIVATE_KEY=

# AirVPN switch: VPN_SERVICE_PROVIDER=airvpn + the three WireGuard values from
# AirVPN's Config Generator (https://airvpn.org/generator/ -> WireGuard):
#   PrivateKey   -> WIREGUARD_PRIVATE_KEY
#   PresharedKey -> WIREGUARD_PRESHARED_KEY
#   Address      -> WIREGUARD_ADDRESSES  (IPv4 ONLY — strip the IPv6 part)
# Reserve a static port at https://airvpn.org/ports/ and put it in BOTH:
#   FIREWALL_VPN_INPUT_PORTS (here) and qBittorrent's listening port (WebUI).
WIREGUARD_ADDRESSES=
WIREGUARD_PRESHARED_KEY=
FIREWALL_VPN_INPUT_PORTS=
```

Depois: `grep -in mullvad .env.example` → vazio.

- [ ] **Step 4: Verificar que a config renderizada só ganhou as duas linhas novas**

```bash
docker compose config > /tmp/compose-after.yaml
diff /tmp/compose-before.yaml /tmp/compose-after.yaml
```

Expected: diff mostra **apenas** `WIREGUARD_PRESHARED_KEY: ""` e `FIREWALL_VPN_INPUT_PORTS: ""` adicionadas ao environment do gluetun. Qualquer outra diferença = PARAR e investigar.

- [ ] **Step 5: Validar live que a Nord continua OK (momento ocioso)**

```bash
docker compose up -d
# aguardar ~2 min (start_period)
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'gluetun|qbittorrent|prowlarr'
docker exec gluetun wget -qO- https://ipinfo.io/ip
```

Expected: `gluetun` healthy; `qbittorrent` healthy (healthcheck `%tun0` passa); IP retornado é da Nord (conferir em ipinfo.io que a org é da Nord/Packethub, não residencial). Se gluetun não subir: `git checkout compose.yaml` + `docker compose up -d` (rollback imediato).

- [ ] **Step 6: Commit**

```bash
git add compose.yaml .env.example
git commit -m "feat(media): expose AirVPN env vars in gluetun, drop Mullvad (no-op on NordVPN)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2 (Fase 1): Trial AirVPN — troca via .env com gates de decisão

**Pré-requisitos manuais do usuário (fora do repo):**
1. Comprar plano AirVPN de **€2/3 dias** (validação antes de comprometer 1 ano).
2. No [Config Generator](https://airvpn.org/generator/): modo WireGuard, qualquer servidor → anotar `PrivateKey`, `PresharedKey`, `Address` (as chaves são por device, valem para todos os servidores; gluetun escolhe o servidor via `SERVER_COUNTRIES`).
3. Em [airvpn.org/ports](https://airvpn.org/ports/): reservar 1 porta (anotar como **P**). Não configurar DDNS.

**Files:**
- Modify: `.env` (gitignored — **nenhum commit nesta task**)

**Interfaces:**
- Consumes: vars expostas na Task 1.
- Produces: decisão documentada (adotar / rollback) para a Task 3.

- [ ] **Step 1: Backup do `.env` atual**

```bash
cp /home/prvrc/dev/homelab/media/.env /home/prvrc/dev/homelab/media/.env.bak-nord
```

Expected: cópia existe. (Fica fora do git — `.env*` é gitignored no repo pai.)

- [ ] **Step 2: Editar `.env` — bloco AirVPN, preservando o bloco Nord comentado**

```bash
# >>> NordVPN standby (rollback: reativar estas linhas e comentar o bloco AirVPN)
#VPN_SERVICE_PROVIDER=nordvpn
#WIREGUARD_PRIVATE_KEY=<manter o valor Nord atual aqui, comentado>

# >>> AirVPN trial (2026-07-XX)
VPN_SERVICE_PROVIDER=airvpn
WIREGUARD_PRIVATE_KEY=<PrivateKey do Config Generator>
WIREGUARD_PRESHARED_KEY=<PresharedKey do Config Generator>
WIREGUARD_ADDRESSES=<Address IPv4 apenas, ex: 10.x.x.x/32 — SEM o IPv6>
FIREWALL_VPN_INPUT_PORTS=<P>
SERVER_COUNTRIES=Brazil
```

- [ ] **Step 3: Aplicar e verificar a conexão**

```bash
docker compose up -d
docker logs gluetun --tail 40   # esperar "healthy"
docker exec gluetun wget -qO- https://ipinfo.io/ip
```

Expected: logs mostram provider airvpn + servidor no Brasil (Peony/São Paulo); IP pertence à AirVPN (checar org no ipinfo). `docker ps`: gluetun, qbittorrent, prowlarr healthy. Se falhar a conexão: gate de rollback (Task 3, caminho B).

- [ ] **Step 4: Apontar o listening port do qBittorrent para P**

WebUI `http://localhost:8090` → Options → Connection → "Port used for incoming connections" = **P**; desmarcar "Use different port on each startup" se marcado → Save. (App-config, não versionado — mesmo padrão dos demais configs do stack.)

- [ ] **Step 5: Testar conectabilidade real (o motivo da migração)**

1. Na página [airvpn.org/ports](https://airvpn.org/ports/), usar o botão de teste da porta P. Expected: **porta aberta/alcançável (TCP)**.
2. No qBittorrent, forçar reannounce num torrent público ativo e observar por ~10 min. Expected: ícone de status de conexão na barra inferior **verde** (conectável), peers de entrada aparecendo.

Se a porta não abrir: conferir que P está em `FIREWALL_VPN_INPUT_PORTS` **e** no qBit, e que a porta no dashboard AirVPN está associada ao device certo ("All Devices" resolve). Persistindo: gate de rollback.

- [ ] **Step 6: Medir velocidade (comparar com baselines Nord conhecidos)**

1. **Swarm saudável:** baixar Ubuntu ISO via torrent oficial. Baseline Nord: 57 MB/s. Expected: mesma ordem de grandeza no Peony; se muito abaixo, repetir com `SERVER_COUNTRIES=United States` (`docker compose up -d` após editar) para isolar congestão do servidor único BR.
2. **Swarm problemático (o caso real):** re-grabar um título de tracker público que estalava na Nord (padrão House of Cards). Expected: progresso > 0 com seeds firewalled agora alcançáveis. Nota honesta: itens com **0 seeds** (The Business) continuarão em 0 — isso é disponibilidade, não conectividade; não usar como critério.

- [ ] **Step 7: Gate de decisão (usuário)**

Critérios para adotar: porta aberta ✓ + swarm saudável ≥ ~70% do baseline Nord ✓ + caso problemático melhorou ✓. Registrar a decisão → Task 3.

---

### Task 3 (Fase 2): Consolidar ou rollback

**Caminho A — adotar (critérios da Task 2 atendidos):**

- [ ] **Step A1:** Usuário assina plano anual (~€49; promoções ~US$37/ano).
- [ ] **Step A2:** Regenerar chaves? Não — as chaves do trial permanecem válidas na mesma conta ao estender o plano. Confirmar apenas que a porta P segue reservada em airvpn.org/ports após a renovação.
- [ ] **Step A3:** Atualizar `README.md` — na seção de VPN, substituir a menção NordVPN: provider = AirVPN, porta estática P (dashboard) espelhada em `FIREWALL_VPN_INPUT_PORTS` + qBit listen_port, rollback = `.env.bak-nord`. Citar que `WIREGUARD_ADDRESSES` deve ser IPv4-only.
- [ ] **Step A4:** Remover `.env.bak-nord` após 2 semanas estáveis (não antes — postura defensiva).
- [ ] **Step A5: Commit docs**

```bash
git add README.md
git commit -m "docs(media): VPN provider switched to AirVPN (static port-forward)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step A6:** Atualizar a memória do backlog (item Tier 1 #2 → DONE, com P e datas) — tarefa mecânica.

**Caminho B — rollback (qualquer gate falhou):**

- [ ] **Step B1:**

```bash
cp /home/prvrc/dev/homelab/media/.env.bak-nord /home/prvrc/dev/homelab/media/.env
docker compose up -d
docker exec gluetun wget -qO- https://ipinfo.io/ip   # Expected: IP Nord novamente
```

- [ ] **Step B2:** qBit listen_port pode ficar em P (irrelevante sem forwarding). Registrar na memória do backlog o que falhou (velocidade? porta?) para orientar a próxima tentativa (ProtonVPN dinâmico como plano C).
- [ ] **Step B3:** As vars da Task 1 **ficam no repo** (vazias = no-op) — não reverter o commit; servem para a próxima tentativa.

---

## Self-Review (executada na escrita)

1. **Cobertura:** requisito "não quebrar Nord" → Task 1 Steps 4–5 (diff de config + validação live com rollback); "porta estática ponta-a-ponta" → Task 2 Steps 4–5; "decisão informada antes de pagar 1 ano" → trial €2 + gates; "Mullvad descartada" → Task 1 Steps 2–3 expurgam compose e `.env.example` (greps de confirmação incluídos). ✓
2. **Placeholders:** `<P>`, `<PrivateKey...>` são segredos/valores do usuário — inevitáveis e explicitamente marcados; nenhum "TBD" de engenharia. ✓
3. **Consistência:** `FIREWALL_VPN_INPUT_PORTS` idêntico em compose/env/README; porta P referida uniformemente. ✓
