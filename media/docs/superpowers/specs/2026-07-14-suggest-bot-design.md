# Design — Descoberta + sugestões proativas (Jellyseerr + suggest-bot)

**Date:** 2026-07-14
**Status:** Approved (brainstorming) — pending spec review
**Scope owner:** media stack (`/home/prvrc/dev/homelab/media`)

## Problem

A stack resolve bem "adicionar por nome" (Addarr no Telegram, ou direto no
Sonarr/Radarr), mas não tem **descoberta**: nada informa o usuário do que existe
para baixar. Faltam dois modos, ambos pedidos:

1. **Portal de descoberta** — navegar tendências/recomendações e pedir com um
   clique (estilo Netflix).
2. **Sugestões que procuram o usuário** — recomendações chegam sozinhas, com
   ação de download a um toque.

## Goal

- Portal web para buscar/navegar/pedir filmes e séries, integrado a Plex,
  Sonarr e Radarr.
- Digest **semanal** no Telegram (~5 títulos) + comando **`/sugira`** sob
  demanda, cada título como card (pôster, sinopse, notas) com botão
  **➕ Adicionar** que dispara o pedido de download.

## Decisions (locked during brainstorming)

| Decisão | Escolha | Racional |
|---|---|---|
| Modelo de interação | Portal **e** push proativo | Portal cobre "buscar/navegar" pronto; o push é o pedaço construído |
| Portal | **Jellyseerr** (imagem pronta, zero código nosso) | Fork ativo do Overseerr; discover TMDB, login Plex (watchlist + "já tenho"), request → Sonarr/Radarr, API REST |
| Base das sugestões | **Trending/populares + watchlist do Plex** | Sem Trakt *pessoal* (OAuth/scrobbling) — descartado pelo usuário |
| Fontes de trending | **TMDB + Trakt trending (merge)** | Sinais diferentes (cliques vs. assistidas reais); interseção = sinal forte. Trakt trending público exige só API key de app, não OAuth |
| Notas nos cards/filtro | **MDBList** (IMDb + RT + Metacritic) | IMDb não tem API pública oficial; agregador com key gratuita é o caminho limpo |
| TVDB | **Fora** | Metadado de séries (Sonarr já cobre com licença própria); não tem descoberta; API v4 avulsa paga. YAGNI |
| Canal + ação | **Telegram com botão inline "Adicionar"** | Fecha o ciclo sem sair do chat |
| Destino do botão | **`POST` na API do Jellyseerr**, não Sonarr/Radarr direto | Botão e portal fazem a mesma ação: mesmo profile/pasta/qualidade, config única, dedup e tracking de pedido de graça |
| Cadência | **Digest semanal (~5) + `/sugira` sob demanda** | Baixo ruído; sob demanda cobre a vontade do momento |
| Bot Telegram | **Bot novo no BotFather** (token próprio) | Token do Addarr não é reutilizável; um bot por serviço |

### Explicitly out of scope (YAGNI)

- **Personalização via Trakt** (histórico/scrobbling do usuário) — descartada;
  exigiria OAuth + scrobbling do Plex. Revisitar só se o digest genérico se
  provar fraco.
- **"Parecido com a sua biblioteca"** (TMDB similar sobre o acervo) — descartado
  na escolha de fontes.
- **TVDB** como fonte.
- **Auto-download sem aprovação** — toda sugestão passa pelo toque do usuário.
- **Múltiplos usuários** — bot é single-user (allowlist de 1), como o Addarr.

## Architecture

Duas peças novas no `compose.yaml`:

### A) Jellyseerr (imagem `fallenbagel/jellyseerr`)

- `servarr_network`, egress normal (TMDB, plex.tv, Plex no host Windows,
  Sonarr/Radarr internos). **Não** ride no gluetun — só fala APIs de metadados,
  como Radarr/Sonarr.
- Config/DB em `${CONFIG_ROOT}/jellyseerr` (ext4 — SQLite fora do 9p).
- Porta `5055`; card no Homepage; acesso remoto via Tailscale como os demais.
- Setup manual (uma vez): login Plex, apontar Sonarr + Radarr (profiles/pastas
  default), gerar API key para o suggest-bot.

### B) suggest-bot (container Python próprio, molde do `import-gate`)

- `python-telegram-bot` (long polling — sem webhook/porta exposta) +
  APScheduler para o job semanal.
- `servarr_network`, egress normal (api.telegram.org, Trakt, MDBList,
  Jellyseerr interno).
- Config + segredos + estado em `${CONFIG_ROOT}/suggest-bot` (fora do git):
  token do bot, chat id/allowlist, API keys (Jellyseerr, Trakt, MDBList),
  parâmetros (dia/hora do digest, tamanho, piso de nota).

## Data flow

```
Watchlist Plex ──► Jellyseerr ◄── TMDB (trending/catálogo/pôsteres)
                      │ API (trending, watchlist, disponibilidade, request)
                      ▼
Trakt trending ──► suggest-bot ◄── MDBList (notas IMDb/RT/Metacritic)
                      │ digest semanal / /sugira
                      ▼
                  Telegram (cards + botão ➕)
                      │ tap ➕ → POST /request no Jellyseerr
                      ▼
        Sonarr/Radarr → qBit → import-gate → F:\Media → Plex
```

O ciclo fecha na stack existente: pedido entra pelo Jellyseerr, download passa
pelo import-gate, aparece no Plex. Nada novo no caminho de download.

### Pipeline de recomendação (job semanal e `/sugira`, mesma função)

1. **Coleta**: trending TMDB (via Jellyseerr) + trending Trakt + watchlist
   Plex (via Jellyseerr).
2. **Normaliza** por `tmdbId` (Trakt devolve ids TMDB nos objetos de
   filme/série; verificar no spike de API).
3. **Filtra**: remove o que já está disponível/pedido (flag do Jellyseerr),
   o que já foi sugerido antes (estado local), conteúdo adulto, e — só para
   trending — nota IMDb abaixo do piso (default 6.5) via MDBList.
   **Watchlist fura o filtro de nota** (intenção explícita do usuário).
4. **Rankeia**: watchlist primeiro; depois trending, com boost para
   interseção TMDB∩Trakt; desempate por nota.
5. **Corta** no tamanho do digest (default 5) e envia cards.

### Estado local (dedup)

SQLite em `${CONFIG_ROOT}/suggest-bot/state.db`: `tmdbId`, tipo (movie/tv),
data da sugestão, status (sugerido/pedido/dispensado). Digest nunca repete
título já sugerido; botão "pedido" atualiza status.

### Telegram UX

- Card por título: pôster, título + ano, sinopse curta, notas (TMDB/IMDb/RT),
  origem (🔥 trending / 👁 watchlist), botões **➕ Adicionar** e **🙈 Dispensar**
  (marca dispensado no estado — não volta).
- `➕` → `POST /api/v1/request` no Jellyseerr → edita o card para
  "✅ Pedido" (ou "❌ falhou — tentar de novo" mantendo o botão).
- Allowlist: ignora silenciosamente qualquer chat fora da lista.

## Error handling

- Jellyseerr fora no horário do digest → pula, loga, alerta no ntfy existente.
- Trakt ou MDBList fora → **degrada**: segue só com TMDB / sem notas IMDb
  (loga o modo degradado no card do digest); nunca bloqueia o digest inteiro.
- `POST /request` falha → card mostra erro e mantém botão para retry; sem
  retry automático.
- Job semanal perdido (stack desligada — cenário real: a stack hiberna) →
  APScheduler com `misfire_grace_time` largo: dispara no próximo boot se a
  janela foi perdida há < 3 dias; mais velho que isso, espera o próximo ciclo.

## Testing

- **Unit**: pipeline de recomendação (merge/filtros/ranking/dedup) com
  fixtures das três APIs; cliente Jellyseerr/Trakt/MDBList mockados;
  parser de callback do botão.
- **E2E manual**: `/sugira` → cards chegam → tocar ➕ → pedido aparece no
  Jellyseerr e no Radarr/Sonarr → download → import-gate → Plex.
- Padrões do repo: pytest, mesmo layout do `import-gate/tests`.

## Verification points (não afirmados de cabeça — checar na implementação)

- Endpoints exatos do Jellyseerr (`/api/v1/discover/*`, `/request`,
  watchlist) contra a instância real.
- Termos de cadastro/rate-limit do Trakt app e do MDBList.
- Formato dos ids TMDB no payload do Trakt (passo 2 do pipeline).

## Setup manual do usuário (checklist)

1. Criar bot no BotFather → token.
2. Criar app no Trakt → client id (sem OAuth de usuário).
3. Criar key no MDBList.
4. Subir Jellyseerr → login Plex → conectar Sonarr/Radarr → gerar API key.
5. Preencher `${CONFIG_ROOT}/suggest-bot/` com segredos.
