# Stream Deck+ — toggle físico da stack media (backlog)

**Data da análise:** 2026-07-10 · **Status:** documentado para execução futura
**Objetivo:** tecla física no Stream Deck+ que sobe/derruba a stack media
(`stack.sh up|down`) com **indicador de status ao vivo** na própria tecla.

## Fatos verificados (2026-07-10 — reverificar se o ambiente mudar)

- **Hardware:** Stream Deck+ (teclas LCD → dá para pintar status; dials/touch strip
  disponíveis para evolução).
- **App:** Stream Deck **7.5.0.22885** no Windows, rodando; MCP habilitado.
- **MCP:** `@elgato/mcp-server` registrado no Claude Code em **escopo user**
  (`claude mcp list` → ✔ Connected). Expõe como tools as ações do perfil
  **"MCP Actions"** — disparo por IA, **não** pinta teclas.
- **WSL:** modo **mirrored** (localhost bidirecional). Distro default:
  **`Ubuntu-26.04`** (existe também `docker-desktop`).
- **Docker:** engine linux **29.6.1** acessível de dentro do Ubuntu via
  `/var/run/docker.sock`. A distro `docker-desktop` presente sugere engine do
  Docker Desktop por trás — **a verificar** se o named pipe
  `//./pipe/docker_engine` existe no lado Windows (abriria a Rota C).
- **Node:** v24 no host Windows e v24.16 no WSL.
- **Alvo:** `~/dev/homelab/media/stack.sh` — interface `up|down|restart|status`;
  ordem VPN-primeiro (gluetun) já resolvida internamente. O botão **não** deve
  duplicar lógica de orquestração: sempre chamar o script.

## Rotas analisadas

### Rota A — teclas nativas, sem código (fazível em ~5 min, sem status real)

Ações **System → Open** no app do Stream Deck chamando o WSL:

```text
subir:  wsl.exe -d Ubuntu-26.04 -- /home/prvrc/dev/homelab/media/stack.sh up
descer: wsl.exe -d Ubuntu-26.04 -- /home/prvrc/dev/homelab/media/stack.sh down
```

Duas teclas, ou uma só com **Multi-Action Switch** (alterna, mas é *cego*: o
estado dessincroniza se a stack for mexida pelo terminal). Limitações: flash de
janela de console ao disparar; nenhum indicador de status.

**Bônus MCP:** colocar essas mesmas ações no perfil "MCP Actions" → qualquer
sessão Claude passa a poder subir/derrubar a stack sob pedido.

### Rota B — plugin próprio via Stream Deck SDK (a resposta certa p/ toggle+status)

Plugin Node (SDK oficial `@elgato/streamdeck`), roda no lado Windows, spawnado
pelo app:

- **Poll** a cada N segundos: `wsl.exe -d Ubuntu-26.04 -- docker compose -f
  /home/prvrc/dev/homelab/media/compose.yaml ps --format json` → pinta a tecla:
  **verde** = todos os serviços up · **amarelo** = parcial/transição ·
  **cinza/vermelho** = down.
- **Pressionar** = toggle conforme último estado conhecido (up→down / down→up),
  com estado "transição" durante a execução.
- Sem flash de console (spawn oculto) e estado sempre fiel.

**Sinergia estratégica:** o esqueleto (poll → paint → act via `wsl.exe`) é a
semente da fase 3 do ctx-dash (teclas como *cards físicos* de sessões Claude —
ver `dotfiles/docs/superpowers/specs/2026-07-10-ctx-dash-design.md`). Construir
uma vez, dois consumidores.

### Rota C — plugin de marketplace (atalho a verificar antes de construir a B)

Plugins Docker existentes miram o Docker Desktop (named pipe/API no Windows).
Só funcionam aqui se `//./pipe/docker_engine` existir no host. Mesmo assim,
semântica de status agregado (10 serviços, autoheal) e o toggle via `stack.sh`
provavelmente não se encaixam. Checar rapidamente; não investir.

## Recomendação e sequência

1. **Agora (manual, sem código):** Rota A no app do Stream Deck — comandos
   prontos acima. Opcional: duplicar as ações no perfil MCP Actions.
2. **Depois do ctx-dash v1:** micro-projeto Rota B (brainstorm próprio; escopo
   pequeno: 1 tecla, 1 plugin genérico "WSL toggle" configurável por comando).
3. Rota C: só uma espiada de 10 min antes de iniciar a B.

## Fora deste backlog

- Pintar teclas via MCP (o server da Elgato não expõe display — é disparo).
- Dials/touch strip do Deck+ (evolução da Rota B, se fizer sentido).
