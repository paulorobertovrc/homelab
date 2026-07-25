# Design: queue-watch — auto-limpeza de itens de fila sem arquivo elegível

**Data:** 2026-07-25 · **Status:** especificado, não implementado

## Problema

Em 2026-07-24 o Sonarr agarrou `Silo S03E05 MULTI 1080p WEB H264-HiggsBoson` (LimeTorrents via
Prowlarr). O torrent continha **um único arquivo**: `Silo S03E05 ... .scr`, 1,32 GB — screensaver
executável do Windows disfarçado de vídeo. O episódio sequer havia ido ao ar (`airDateUtc` de
S03E05 = 2026-07-31), então a release era falsa por construção.

O guard `excluded_file_names` do qBittorrent (`*.exe`, `*.scr`, `*.bat`, …), posto depois do
incidente anterior de fake `.exe` do **mesmo indexer**, barrou o arquivo: `priority 0`,
`progress 0`, zero bytes em disco. A trava funcionou — nenhum byte de malware foi gravado.

O resíduo é o problema. Com todos os arquivos filtrados, o qBit considera o torrent completo
(`stalledUP`, `size 0`); o Sonarr vê `status: completed`, tenta importar e trava
indefinidamente:

```text
state: importPending | trackedDownloadStatus: warning
statusMessages: ["No files found are eligible for import in
                 /data/torrents/complete/Silo S03E05 MULTI 1080p WEB H264-HiggsBoson.scr"]
```

**O guard atua em silêncio.** Não há notificação, o item fica preso na fila para sempre, e o
episódio nunca é rebuscado — descoberto só porque o usuário reparou no pending. Segundo caso do
tipo; o primeiro passou pelo mesmo caminho sem alarme.

Mitigação já aplicada em 2026-07-25 (fora desta spec): LimeTorrents desabilitado no Prowlarr
(28 grabs / 5 blocklists = 18%, único indexer com malware executável no histórico; TPB 127/0,
1337x 22/0, Knaben 53/3). A propagação para o Sonarr foi verificada: `rss=False`,
`auto=False`, `interactive=False`. Isso fecha *esta* fonte, não a classe de falha.

## Decisão

Um poller dentro do `import-gate`, em thread daemon, que detecta itens de fila sem arquivo
elegível, remove-os com blocklist e notifica via ntfy.

Mora no `import-gate` — e não em container próprio — porque o domínio é o mesmo (os dois
vigiam o portão de import), reusa `ArrClient`/`notify.push`/`Settings`/Dockerfile/healthcheck/
suíte de testes, e são ~80 linhas: o custo de manter um 18º serviço não se paga por um `GET`
a cada 10 minutos. `suggest-bot/scheduling.py` já estabelece o padrão de loop temporizado no
stack.

### Detecção

A cada `QUEUE_WATCH_INTERVAL_MIN`, `GET /api/v3/queue` no Sonarr **e** no Radarr — a API é
idêntica nos dois e `ArrClient` já é instanciado para ambos. Um registro é candidato quando:

- `status == "completed"`, **e**
- alguma entrada de `statusMessages[].messages[]` contém a substring
  `"No files found are eligible for import"`

O casamento é **pela mensagem**, não pelo `trackedDownloadState`: o Sonarr alterna entre
`importPending` e `importBlocked` conforme a versão, enquanto a mensagem é estável e
inequívoca — o download terminou e não há nada para importar.

### Travas

Três, todas configuráveis por env:

**Idade mínima** (`QUEUE_WATCH_MIN_AGE_MIN`, default 15). O watcher registra em memória o
instante do primeiro avistamento de cada `downloadId` nesse estado e só age depois do limiar.
Usa primeiro-avistamento, não o campo `added` do registro, porque o que importa é há quanto
tempo o item está *travado*, não há quanto tempo está na fila. Estado em memória por decisão:
restart do container zera o relógio e faz o watcher esperar de novo — a direção segura de
falhar. Entradas de `downloadId` que sumiram da fila são descartadas a cada ciclo, senão o
dicionário cresce sem limite.

**Teto por ciclo** (`QUEUE_WATCH_MAX_PER_CYCLE`, default 3). Se o número de candidatos maduros
exceder o teto, o watcher **não age em nenhum** e dispara um ntfy de anomalia com prioridade
alta. É a defesa contra falha sistêmica: com o disco cheio ou desmontado (cenário real de
2026-07-24), muitos itens ficariam sem arquivo elegível ao mesmo tempo e a auto-limpeza
blocklistaria releases boas em massa. A notificação de anomalia é deduplicada por flag em
memória, que reseta quando a contagem volta a ficar dentro do teto — senão vira spam de 10 em
10 minutos.

O teto é **global ao ciclo**, contando Sonarr e Radarr somados, e a flag de dedupe é uma só.
Uma falha sistêmica (disco, permissão, montagem) atinge os dois apps ao mesmo tempo; um teto
por app deixaria passar o dobro justamente no cenário que a trava existe para conter. Como
consequência, as duas filas são coletadas primeiro e só então avaliadas — se um dos dois apps
estiver fora do ar, o ciclo inteiro é abortado com log, sem agir com visão parcial.

**Kill switch** (`QUEUE_WATCH_ENABLED`, default `true`). Desliga o poller sem rebuild.

### Ação

Por item maduro, dentro do teto:

```text
DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=true
                         &skipRedownload=false&changeCategory=false
```

Exatamente a chamada validada à mão em 2026-07-25 no item do Silo, verificada ponta a ponta:
item sai da fila, torrent sai do qBit (42 → 41), release entra na blocklist, re-busca dispara.

`blocklist=true` é o que impede o Sonarr de reagarrar a mesma release no ciclo seguinte —
sem ele o watcher entraria em loop com o RSS.

### Notificação

Um ntfy por item removido, contendo título da release **e indexer de origem**. O indexer é o
campo que faz o padrão emergir: foi a concentração de falhas num único indexer que denunciou o
LimeTorrents. O indexer não vem no registro da fila; obtém-se pelo `GET /api/v3/history` com
`downloadId`, o mesmo caminho que `find_grab_history_id` já percorre. Se a busca falhar, a
notificação sai sem o indexer — nunca bloqueia a ação.

### Estrutura

Dois métodos novos em `ArrClient`:

- `get_queue()` → `GET /api/v3/queue?pageSize=200`
- `delete_queue_item(queue_id, blocklist=True)` → o `DELETE` acima

Módulo novo `queue_watch.py`, com a lógica de decisão isolada numa função pura:

```python
def find_candidates(records, now, first_seen, min_age_min) -> tuple[list[dict], dict]
```

Sem rede e sem relógio implícito: `now` e `first_seen` entram como parâmetro, o que torna idade
mínima e expiração testáveis sem `sleep` nem mock de tempo. A função **não muta** o `first_seen`
recebido — devolve um dicionário novo, já contendo os avistamentos desta rodada e sem as chaves
cujo `downloadId` saiu da fila. Quem chama é que reatribui. Isso mantém a função verdadeiramente
pura e deixa a poda de chaves órfãs observável num teste, em vez de ser efeito colateral.

Em volta dela, um `run_forever()` fino que faz I/O: coleta as duas filas, chama
`find_candidates`, aplica o teto e executa as remoções.

Ligado em `app.py` como thread daemon, com `try/except` cobrindo o ciclo inteiro: qualquer
exceção vira log e o loop continua. O `/health` segue medindo apenas o Flask — falha do poller
nunca marca o container unhealthy nem toca o caminho de validação de imports, que é a defesa
mais valiosa do stack.

### Configuração (compose)

Quatro variáveis novas no serviço `import-gate`, todas com default embutido em `Settings`:

```yaml
- QUEUE_WATCH_ENABLED=${QUEUE_WATCH_ENABLED:-true}
- QUEUE_WATCH_INTERVAL_MIN=${QUEUE_WATCH_INTERVAL_MIN:-10}
- QUEUE_WATCH_MIN_AGE_MIN=${QUEUE_WATCH_MIN_AGE_MIN:-15}
- QUEUE_WATCH_MAX_PER_CYCLE=${QUEUE_WATCH_MAX_PER_CYCLE:-3}
```

## Matriz de falhas

| Cenário | Comportamento |
|---|---|
| Payload barrado pelo `excluded_file_names` (o incidente) | Detectado; após 15 min, removido + blocklistado + ntfy com indexer |
| Sonarr ainda processando um import legítimo | Não casa a mensagem; ignorado |
| Item recém-travado (< 15 min) | Visto e cronometrado, nenhuma ação |
| Disco cheio/desmontado → muitos itens travados | Acima do teto: **zero ações**, um ntfy de anomalia (não repetido) |
| Sonarr ou Radarr fora do ar | Exceção logada, ciclo seguinte tenta de novo; thread sobrevive |
| Restart do container | Relógio zera; itens travados esperam mais 15 min |
| Lookup de indexer falha | Notifica sem o indexer; remoção acontece do mesmo jeito |
| `QUEUE_WATCH_ENABLED=false` | Thread não sobe |

## Testes (TDD, antes do código)

Sobre `find_candidates`, pura:

1. Registro com a mensagem e `status=completed` → candidato
2. Registro sem a mensagem → ignorado
3. Registro com a mensagem mas `status != completed` → ignorado
4. Primeiro avistamento → cronometrado, não retornado
5. Avistado há mais que `min_age_min` → retornado
6. `downloadId` que sumiu da fila → removido de `first_seen`

Sobre o ciclo, com `ArrClient` e `notify` dublados:

1. Candidatos acima do teto → nenhum `delete_queue_item`, exatamente uma notificação
2. Anomalia persistente → notificação **não** repetida no ciclo seguinte
3. Contagem volta ao normal → flag reseta, próxima anomalia notifica de novo
4. `delete_queue_item` chamado com `removeFromClient`, `blocklist`, `skipRedownload` corretos
5. Exceção no `get_queue` não propaga para fora do ciclo
6. Um dos apps fora do ar → ciclo abortado, nenhuma remoção com visão parcial
7. `QUEUE_WATCH_ENABLED=false` → nenhuma chamada

## Fora de escopo (YAGNI)

- **Outros modos de travamento de fila** — `stalled with no connections`, `importPending` antigo,
  torrent sem seeders. Decisão explícita do usuário: sinal ambíguo, limiar por tipo, e risco de
  matar download lento porém saudável. Há um item nesse estado na fila hoje (`Agent Kim
  Reactivated S01E04`), tratado à mão.
- **Consultar o qBit para confirmar a extensão barrada.** Daria evidência mais forte e mensagem
  melhor (`barrado: .scr`), ao custo de credencial do qBit e travessia do firewall do gluetun.
  A mensagem do Sonarr já é sinal suficiente.
- **Persistir `first_seen` em disco.** O sqlite de `state.py` está ali, mas perder o relógio num
  restart apenas adia a ação — não a impede.
- **Bloquear grabs de episódios ainda não exibidos.** É a causa a montante (o RSS do Sonarr não
  valida `airDateUtc`), mas exige interceptar o grab, não a fila. Merece spec própria se
  reincidir.
