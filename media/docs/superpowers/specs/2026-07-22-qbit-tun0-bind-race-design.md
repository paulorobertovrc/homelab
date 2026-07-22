# Design: blindagem do bind tun0 do qBittorrent (boot race + reconexão)

**Data:** 2026-07-22 · **Status:** implementado e validado ao vivo em 2026-07-22
(commits `af85063` + `2f74cfa`, docs `1ab47db`/`f800c36`; validação registrada no plano
`../plans/2026-07-22-qbit-tun0-bind-race.md`, incluindo o reteste estendido que forçou
os dois ramos — wait-loop bloqueando de fato e recycle via autoheal)

## Problema

Em restarts orquestrados pelo daemon (reboot do host, restart do dockerd, `docker restart`
solto), o Docker Engine reinicia cada container pela sua política `restart:` sem respeitar o
`depends_on: condition: service_healthy` do compose — essa trava só vale para `docker compose up`.
Resultado observado ao vivo em 2026-07-22:

- 08:41:56 gluetun sobe; 08:42:02 qbittorrent sobe; 08:42:04 WireGuard completa o setup.
- 08:42:03 o qBit tenta bindar **antes** do tun0 existir. Com `Session\Interface=tun0`
  persistido e a interface ausente, o qBit **descarta silenciosamente a configuração** e cai
  para "Any" (`0.0.0.0`) → binda só `127.0.0.1` / `172.39.0.2` (eth0) / `::1`.
- Todo pacote de peer/tracker sai por eth0 e morre no `OUTPUT DROP` do gluetun: 0 peers em
  tudo, kill-switch furado no nível do bind. Sem retry automático: ficou 15 min errado até um
  save manual de preferências (08:57:34) regravar o conf e forçar o rebind.
- O healthcheck vigente (`curl google`) ficou **healthy o tempo todo**: o curl usa a rota
  default (tun0), independente de onde o libtorrent bindou. Cego para este modo de falha.

O fix anterior (`Session\Interface=tun0` persistido, commit 0a4da1d) resolve "operador
esqueceu de configurar", mas não resolve a corrida de boot. Correção urgente, não hardening.

## Decisão

Duas guardas compostas, ambas só em `compose.yaml` (serviço qbittorrent) — nenhuma mudança
em imagem, gluetun ou firewall:

### Guarda 1 — prevenção: entrypoint wait

```yaml
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
```

- **Prontidão = tun0 com IPv4**, não apenas existir/UP: TUN devices reportam `state UNKNOWN`,
  e o que o libtorrent precisa para bindar é o endereço, que o gluetun atribui por último.
- **Sem timeout** (semântica de kill-switch): sem túnel, o processo qBit nunca sobe. O escape
  do limbo é a Guarda 2 (start_period 120s expira → unhealthy → autoheal recicla).
- **`exec /init`** preserva o s6-overlay como PID 1 após a espera; o `trap` cobre
  `docker stop` durante a janela (bash como PID 1 ignora TERM por default).
- Sem bind-mount sobre scripts internos da imagem hotio — sobrevive a updates da `:release`.

### Guarda 2 — detecção e cura: healthcheck bind-aware

Trocar apenas o `test` do healthcheck existente (interval/timeout/retries/start_period
inalterados):

```yaml
test: ["CMD-SHELL", "ss -tln | grep -q '%tun0:' && curl -sf -m 10 https://www.google.com -o /dev/null || exit 1"]
```

- O sufixo `%tun0` no `ss` só aparece para socket vinculado ao device — exatamente o listener
  do libtorrent (`10.5.0.2%tun0:34124`, confirmado ao vivo). Fallback para `0.0.0.0` não
  produz essa linha → unhealthy → autoheal (já existente no stack) recicla → cai na Guarda 1
  → sobe limpo. Verificação por **nome** de interface, imune a mudança de IP em redial.
- Bind check primeiro (barato, local); o curl de conectividade permanece como segunda condição.
- Sem falso-unhealthy conhecido: o listener TCP do libtorrent existe sempre que a sessão está
  ativa, com ou sem torrents. Ferramentas (`ss`, `ip`, `bash`, `curl`) confirmadas na imagem.

### Documentação

Estender a seção "qBittorrent MUST be bound to tun0" do README com: o incidente
(daemon-driven restart ignora `depends_on`; qBit descarta silenciosamente o bind por nome
quando a interface não existe no boot), as duas guardas e por que o healthcheck antigo era cego.

## Matriz de falhas

| Cenário | Guarda | Resultado |
|---|---|---|
| `compose up` normal | `depends_on` (existente) + wait | sobe com tun0 |
| Reboot host / dockerd restart (incidente) | wait | espera tun0, binda certo |
| VPN nunca conecta | wait segura o app; healthcheck expira → autoheal | ciclo de retry, sem vazamento |
| tun0 recriado em redial mid-run | healthcheck (se o libtorrent não re-bindar só) → autoheal | recupera em ≤ ~6 min |
| Regressão futura com bind errado | healthcheck | unhealthy → recicla |

## Validação (teste disruptivo autorizado pelo usuário)

1. **Boot:** `docker compose restart` → log do qBit abre direto com `"tun0:34124"` /
   `Successfully listening on IP: "10.5.0.2"`, sem bloco `0.0.0.0`.
2. **Corrida (decisivo):** `docker restart gluetun qbittorrent` — bypassa o `depends_on`,
   reproduz o incidente → o wait deve segurar o qBit até o tun0 vir.
3. **Reconexão VPN sem restart de container:** control server do gluetun (`:8000`,
   `PUT /v1/vpn/status`) para parar/religar só o VPN, recriando o tun0 na mesma netns.
   Observar e documentar qual dos dois ocorre: libtorrent re-binda sozinho, ou
   healthcheck+autoheal reciclam. Ambos aceitáveis.
4. **Critério comum:** `ss -tln | grep '%tun0:'` presente, container `healthy`, nenhum listen
   em `0.0.0.0` no log do qBit.

## Fora de escopo (YAGNI)

- Watchdog de rebind via WebUI API (mais peças móveis para evitar um restart barato).
- Migração para o WireGuard embutido da imagem hotio (abandona o gluetun, que também serve o
  prowlarr).
- Qualquer mudança em gluetun/firewall/routing — já descartados como causa.
