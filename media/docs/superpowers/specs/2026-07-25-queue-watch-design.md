# Design: queue-watch — dois portões de fila (sem arquivo elegível + pre-air)

**Data:** 2026-07-25 · **Status:** especificado, não implementado

Dois portões independentes sobre a mesma varredura de fila:

- **Portão A — "no files eligible":** limpa o resíduo que o guard de extensão do qBit deixa.
  Reativo, age depois do download.
- **Portão B — pre-air:** mata grabs de episódios que ainda não foram exibidos. Preventivo,
  age antes do download terminar.

## Problema 1 — resíduo do guard de extensão do qBit

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

## Problema 2 — grabs de episódios que ainda não foram ao ar

O `Silo S03E05` foi agarrado **158 horas antes** de o episódio existir. O RSS sync do Sonarr não
compara a release com `airDateUtc`: qualquer release que case com um episódio monitorado é
agarrada na hora, exista o episódio ou não. Não há alavanca nativa — o Sonarr não tem
equivalente ao *Minimum Availability* do Radarr, e `delayProfile` só adia o grab da mesma
release falsa (o perfil vigente está em `torrentDelay: 0`).

Medição sobre os 230 grabs mais recentes do Sonarr (2026-07-25), cruzando `history` com
`airDateUtc`: **7 grabs pre-air, todos do LimeTorrents**, em dois grupos bem separados.

| Antecedência | Release | Desfecho |
|---|---|---|
| 158,0 h | Silo S03E05 MULTI ... HiggsBoson | `.scr` — malware |
| 142,5 h | House of the Dragon S03E05 ... MULTI SUBS | `.exe` — malware |
| 116,6 h | House of the Dragon S03E05 AMZN ... MULTI SUBS | `.exe` — malware |
| 2,1 h | Silo S03E04 1080p WEB H264 CAKES | legítima, importada |
| 2,1 h | Cape Fear S01E09 1080p WEB H264 CAKES | legítima |
| 1,6 h | Silo S03E04 ... 2160p ATVP WEB-DL | legítima, importada |
| 1,6 h | Cape Fear S01E09 ... 2160p ATVP WEB-DL | legítima |

**A margem é o coração da regra, não um parâmetro de tuning.** Uma regra ingênua
(`airDateUtc > agora` → bloqueia) rejeitaria 4 dos 7 — todas releases boas que estão na
biblioteca hoje. `airDateUtc` é o horário nominal de exibição na rede original e WEB-DL
legítimo aparece rotineiramente ~2h antes. Como a separação entre os grupos é de quase duas
ordens de grandeza (2,1h vs 116,6h), qualquer margem entre ~6h e ~100h classifica os 7
corretamente; **24h** deixa folga larga dos dois lados.

**Por que isso não é redundante com o guard do qBit:** o `excluded_file_names` só pega
executáveis. Uma fake pre-air empacotada como `.mkv` (tela preta, upscale de lixo, conteúdo
trocado) passa reto pelo guard, baixa inteira e só então depende do whisper do import-gate.
O portão de air-date cobre essa classe, e cobre antes de gastar a banda.

**Contra-argumento honesto:** os 7 vieram do LimeTorrents, hoje desabilitado — o vetor conhecido
já está fechado. O que justifica construir mesmo assim é que a defesa por indexer exige saber de
antemão qual indexer é ruim, enquanto a defesa por data é estrutural e vale para qualquer fonte,
inclusive uma que ainda não fez por merecer.

## Decisão

Um poller dentro do `import-gate`, em thread daemon, que varre a fila e aplica os dois portões:
remove os itens com blocklist e notifica via ntfy.

Mora no `import-gate` — e não em container próprio — porque o domínio é o mesmo (os dois
vigiam o portão de import), reusa `ArrClient`/`notify.push`/`Settings`/Dockerfile/healthcheck/
suíte de testes, e é da ordem de 150 linhas: o custo de manter um 18º serviço não se paga por um
`GET` a cada 10 minutos. `suggest-bot/scheduling.py` já estabelece o padrão de loop temporizado
no stack.

### Agrupamento por downloadId (pré-requisito dos dois portões)

A cada `QUEUE_WATCH_INTERVAL_MIN`, `GET /api/v3/queue?includeEpisode=true` no Sonarr **e**
`GET /api/v3/queue` no Radarr — `ArrClient` já é instanciado para ambos.

No `QueueResource` da v3/v4 (confirmado no schema oficial; nosso Sonarr é 4.0.19) cada registro
representa **um episódio** — `episodeId` é singular, e vem acompanhado de `episode`
(`EpisodeResource`, com `airDateUtc`) quando se pede `includeEpisode=true`. Um season pack
aparece portanto como **N registros compartilhando o mesmo `downloadId`**.

Antes de qualquer avaliação, os registros são **agrupados por `downloadId`**. Isso não é
otimização, é correção — sem o agrupamento:

- um pack de 10 episódios contaria 10 contra o teto de 3, estourando-o sozinho e travando o
  watcher em modo "anomalia" **permanente**, ou seja, a trava de segurança se auto-sabotaria;
- seriam emitidos 10 `DELETE` para o mesmo torrent, 9 deles em 404.

O grupo é a unidade de decisão, de contagem contra o teto e de ação.

**Invariante: nunca julgar um grupo parcial.** O portão B decide por `all()` sobre os
episódios do grupo, então ver só parte dele inverte o veredicto — um pack legítimo cujos
episódios já exibidos ficaram de fora parece inteiramente pre-air e é destruído. Verificado em
protótipo: um pack de 4 registros com 2 episódios exibidos é corretamente poupado inteiro e
**erradamente removido** quando fatiado. Duas consequências obrigatórias:

- `get_queue` **pagina até o fim** em vez de usar um `pageSize` fixo. Abortar quando a fila
  excede a página seria a alternativa simples, mas desligaria o watcher em silêncio sempre que
  alguém enfileirasse uma temporada grande.
- Um registro sem `id` (inacionável, já que a ação usa `min(id)` do grupo) **descarta o grupo
  inteiro**, não apenas aquele registro. Descartar só o registro reintroduziria exatamente o
  mesmo defeito em escala menor.

O campo **`indexer` já vem no próprio `QueueResource`** — não é preciso consultar o `/history`
para descobrir a origem.

### Portão A — sem arquivo elegível

Um grupo é candidato quando **algum** de seus registros tem:

- `status == "completed"`, **e**
- alguma entrada de `statusMessages[].messages[]` contendo a substring
  `"No files found are eligible for import"`

O casamento é **pela mensagem**, não pelo `trackedDownloadState`: o Sonarr alterna entre
`importPending` e `importBlocked` conforme a versão, enquanto a mensagem é estável e
inequívoca — o download terminou e não há nada para importar.

### Portão B — pre-air

Um grupo é candidato quando **todos** os seus registros têm
`episode.airDateUtc > agora + QUEUE_WATCH_PREAIR_MARGIN_H` (default 24).

O quantificador é **todos**, e é o que protege season packs: um pack de temporada em andamento
mistura episódios já exibidos com futuros, então basta um episódio já no ar para o grupo
sobreviver. Um grab genuinamente pre-air não tem nenhum.

Registro sem `episode`, sem `airDateUtc`, com data ilegível **ou com data sem fuso horário**
não conta como pre-air e faz o grupo inteiro ser poupado — a ausência de dado nunca autoriza a
ação. O caso do fuso não é hipotético: `datetime.fromisoformat` aceita um timestamp sem offset
e devolve um objeto *naive*, que ao ser comparado com um `now` *aware* levanta `TypeError` —
verificado em protótipo — escapando da função e abortando o ciclo inteiro. Tratá-lo como
ilegível troca um crash por um grupo poupado.

Só se aplica ao **Sonarr**. Filmes têm o *Minimum Availability* nativo do Radarr, que já cobre o
caso a montante.

Diferente do portão A, este **não** espera a idade mínima: o sinal é uma data, determinístico e
não-transitório, e agir cedo é justamente o ganho (mata o download antes de gastar a banda). A
margem de 24h já absorve metadado torto do TVDB.

### Travas

Três, todas configuráveis por env:

**Idade mínima** (`QUEUE_WATCH_MIN_AGE_MIN`, default 15) — **só no portão A**. O watcher
registra em memória o instante do primeiro avistamento de cada `downloadId` nesse estado e só
age depois do limiar. Usa primeiro-avistamento, não o campo `added` do registro, porque o que
importa é há quanto tempo o item está *travado*, não há quanto tempo está na fila. Estado em
memória por decisão: restart do container zera o relógio e faz o watcher esperar de novo — a
direção segura de falhar. Entradas de `downloadId` que sumiram da fila são descartadas a cada
ciclo, senão o dicionário cresce sem limite. O portão B dispensa a espera (ver acima); sua
proteção equivalente é a margem de 24h.

**Teto por ciclo** (`QUEUE_WATCH_MAX_PER_CYCLE`, default 3). Se o número de **grupos**
candidatos — somando os dois portões — exceder o teto, o watcher **não age em nenhum** e
dispara um ntfy de anomalia com prioridade alta. É a defesa contra falha sistêmica: com o disco
cheio ou desmontado (cenário real de 2026-07-24), muitos itens ficariam sem arquivo elegível ao
mesmo tempo e a auto-limpeza blocklistaria releases boas em massa. A notificação de anomalia é
deduplicada por flag em memória, que reseta quando a contagem volta a ficar dentro do teto —
senão vira spam de 10 em 10 minutos.

Conta **grupos, não registros** — a razão está em "Agrupamento por downloadId": contar registros
faria um único season pack estourar o teto sozinho e desativar o watcher em definitivo.

O teto é **global ao ciclo**, contando Sonarr e Radarr somados, e a flag de dedupe é uma só.
Uma falha sistêmica (disco, permissão, montagem) atinge os dois apps ao mesmo tempo; um teto
por app deixaria passar o dobro justamente no cenário que a trava existe para conter. Como
consequência, as duas filas são coletadas primeiro e só então avaliadas — se um dos dois apps
estiver fora do ar, o ciclo inteiro é abortado com log, sem agir com visão parcial.

**Kill switch** (`QUEUE_WATCH_ENABLED`, default `true`). Desliga o poller sem rebuild.

**Dry-run** (`QUEUE_WATCH_DRY_RUN`, default **`true`**). O watcher nasce simulando: decide,
loga e notifica exatamente o que faria, sem apagar nada. Armar é ato deliberado
(`QUEUE_WATCH_DRY_RUN=false` no `.env`), nunca efeito colateral de um deploy — a primeira
execução de uma automação destrutiva não deveria ser a primeira vez que alguém vê o que ela
decide.

O risco do dry-run é o inverso: virar falsa sensação de segurança se alguém esquecer que está
simulando. Mitigado com rótulo em todo lugar — a linha de startup diz `DRY-RUN (no deletions)`
ou `ARMED`, e cada notificação vai prefixada com `[SIMULAÇÃO]` e o aviso de que nada foi
removido.

Em simulação o item **permanece na fila**, logo continua candidato em todo ciclo. Sem
deduplicação, o mesmo item notificaria de 10 em 10 minutos e o modo seria inutilizável — então
cada grupo é reportado uma vez, e a marca é esquecida quando o grupo sai da fila (item que
volta é reportado de novo).

### Ação

Por grupo candidato, dentro do teto — **um único** `DELETE`, no menor `id` do grupo:

```text
DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=true
                         &skipRedownload=false&changeCategory=false
```

Exatamente a chamada validada à mão em 2026-07-25 no item do Silo, verificada ponta a ponta:
item sai da fila, torrent sai do qBit (42 → 41), release entra na blocklist, re-busca dispara.
Como `removeFromClient=true` remove o torrent inteiro, os demais registros do mesmo `downloadId`
desaparecem junto — daí um `DELETE` por grupo, não por registro.

`blocklist=true` é o que impede o Sonarr de reagarrar a mesma release no ciclo seguinte —
sem ele o watcher entraria em loop com o RSS.

`skipRedownload` difere por portão: **`false` no A, `true` no B**. No portão A a re-busca é o
objetivo — existe um arquivo bom para aquele episódio em algum lugar. No portão B o episódio
ainda não existe, então forçar busca imediata só pode trazer outra fake e realimentar o ciclo;
melhor deixar o RSS agendado pegar naturalmente depois da estreia.

### Notificação

Um ntfy por grupo removido, dizendo qual portão disparou e contendo título da release **e
indexer de origem** (campo `indexer`, já presente no `QueueResource` — sem consulta extra ao
`/history`). O indexer é o dado que faz o padrão emergir: foi a concentração de falhas num único
indexer que denunciou o LimeTorrents. No portão B a mensagem inclui também a antecedência em
horas, que é a evidência da decisão.

### Estrutura

Dois métodos novos em `ArrClient`:

- `get_queue(include_episode=False)` → `GET /api/v3/queue`, paginado até esgotar `totalRecords`
- `delete_queue_item(queue_id, blocklist=True, skip_redownload=False)` → o `DELETE` acima

Módulo novo `queue_watch.py`, com a lógica de decisão isolada em funções puras:

```python
def group_by_download_id(records) -> dict[str, list[dict]]
def find_stuck(groups, now, first_seen, min_age_min) -> tuple[list[dict], dict]
def find_preair(groups, now, margin_h) -> list[dict]
```

Sem rede e sem relógio implícito: `now` e `first_seen` entram como parâmetro, o que torna idade
mínima, margem e expiração testáveis sem `sleep` nem mock de tempo. `find_stuck` **não muta** o
`first_seen` recebido — devolve um dicionário novo, já contendo os avistamentos desta rodada e
sem as chaves cujo `downloadId` saiu da fila. Quem chama é que reatribui. Isso mantém a função
verdadeiramente pura e deixa a poda de chaves órfãs observável num teste, em vez de ser efeito
colateral.

Em volta delas, um `run_forever()` fino que faz I/O: coleta as duas filas, agrupa, chama os dois
detectores, une os candidatos (um mesmo `downloadId` que dispare os dois portões conta e age uma
vez só), aplica o teto e executa as remoções.

Ligado em `app.py` como thread daemon, com `try/except` cobrindo o ciclo inteiro: qualquer
exceção vira log e o loop continua. O `/health` segue medindo apenas o Flask — falha do poller
nunca marca o container unhealthy nem toca o caminho de validação de imports, que é a defesa
mais valiosa do stack.

A thread recebe **instâncias próprias** de `ArrClient`, não as que o Flask usa. Cada `ArrClient`
carrega um `requests.Session`, e a documentação do requests não afirma que `Session` seja
thread-safe — a própria base de código mostra thread-safety sendo adicionada deliberadamente
onde importava (`HTTPDigestAuth` guardando estado em `threading.local`), o que indica não ser
propriedade geral do objeto. Um segundo par de clientes custa nada e elimina a dúvida.

### Configuração (compose)

Sete variáveis novas no serviço `import-gate`, todas com default embutido em `Settings`:

```yaml
- QUEUE_WATCH_ENABLED=${QUEUE_WATCH_ENABLED:-true}
- QUEUE_WATCH_INTERVAL_MIN=${QUEUE_WATCH_INTERVAL_MIN:-10}
- QUEUE_WATCH_MIN_AGE_MIN=${QUEUE_WATCH_MIN_AGE_MIN:-15}
- QUEUE_WATCH_MAX_PER_CYCLE=${QUEUE_WATCH_MAX_PER_CYCLE:-3}
- QUEUE_WATCH_PREAIR_ENABLED=${QUEUE_WATCH_PREAIR_ENABLED:-true}
- QUEUE_WATCH_PREAIR_MARGIN_H=${QUEUE_WATCH_PREAIR_MARGIN_H:-24}
- QUEUE_WATCH_DRY_RUN=${QUEUE_WATCH_DRY_RUN:-true}
```

O portão B tem flag **própria** (`QUEUE_WATCH_PREAIR_ENABLED`) em vez de ser desligado com
`MARGIN_H=0`. A sobrecarga seria ambígua num knob de segurança: `0` lê tanto como "desliga"
quanto como "margem zero", e margem zero é exatamente o modo agressivo que rejeitaria as quatro
releases legítimas da medição. Desligar e afrouxar precisam ser controles distintos.

Defensivo por consequência: `Settings.from_env` **rejeita** `QUEUE_WATCH_PREAIR_MARGIN_H < 1`
com erro na subida do container. Um zero digitado por engano derruba o serviço de forma visível
em vez de silenciosamente ativar o modo que blocklista releases boas.

## Matriz de falhas

| Cenário | Comportamento |
|---|---|
| Payload barrado pelo `excluded_file_names` (o incidente) | Portão A: após 15 min, removido + blocklistado + ntfy com indexer |
| Grab pre-air tipo `Silo S03E05` (158h antes) | Portão B: removido no primeiro ciclo, antes de o download terminar |
| Release legítima ~2h antes do `airDateUtc` | Dentro da margem de 24h; **não** é tocada |
| Season pack de temporada em andamento | Tem episódio já exibido → o "todos" falha → poupado |
| Season pack de 10 episódios travado | Um grupo, não 10: conta 1 contra o teto, sofre 1 `DELETE` |
| Episódio sem `airDateUtc` (special, metadado faltando) | Não conta como pre-air; grupo poupado |
| `airDateUtc` sem fuso horário ou ilegível | Tratado como ausente; grupo poupado (sem `TypeError`) |
| Fila maior que uma página | Paginada até o fim; nenhum grupo é julgado pela metade |
| Registro do grupo sem `id` | Grupo inteiro descartado, não só o registro |
| Sonarr ainda processando um import legítimo | Não casa a mensagem; ignorado |
| Item recém-travado (< 15 min) | Visto e cronometrado, nenhuma ação |
| Disco cheio/desmontado → muitos itens travados | Acima do teto: **zero ações**, um ntfy de anomalia (não repetido) |
| Sonarr ou Radarr fora do ar | Ciclo abortado com log, sem agir com visão parcial; thread sobrevive |
| Restart do container | Relógio zera; itens do portão A esperam mais 15 min |
| `QUEUE_WATCH_PREAIR_MARGIN_H` < 1 | Container **não sobe** (erro explícito em `from_env`) |
| `QUEUE_WATCH_PREAIR_ENABLED=false` | Só o portão A opera |
| `QUEUE_WATCH_DRY_RUN=true` (default) | Decide e notifica com `[SIMULAÇÃO]`, remove nada; cada grupo reportado uma vez |
| `QUEUE_WATCH_ENABLED=false` | Thread não sobe |

## Testes (TDD, antes do código)

Sobre `group_by_download_id`, pura:

1. N registros com o mesmo `downloadId` → um grupo de N
2. Registros com `downloadId` distintos → grupos distintos

Sobre `find_stuck`, pura:

1. Registro com a mensagem e `status=completed` → candidato
2. Registro sem a mensagem → ignorado
3. Registro com a mensagem mas `status != completed` → ignorado
4. Primeiro avistamento → cronometrado, não retornado
5. Avistado há mais que `min_age_min` → retornado
6. `downloadId` que sumiu da fila → removido de `first_seen`
7. `first_seen` recebido **não** é mutado (o original segue intacto após a chamada)

Sobre `find_preair`, pura — a bateria decisiva, alimentada pelos dados reais medidos:

1. Grupo com `airDateUtc` 158h à frente → candidato (caso `Silo S03E05`)
2. Grupo com `airDateUtc` 2,1h à frente → **não** candidato (caso `Silo S03E04` CAKES,
   release legítima que está na biblioteca) — o teste que impede a regressão para a regra ingênua
3. Grupo com `airDateUtc` exatamente na margem → não candidato (fronteira fechada)
4. Grupo misto (um episódio pre-air, um já exibido) → **não** candidato (season pack legítimo)
5. Grupo em que todos os episódios são pre-air → candidato
6. Registro sem `episode` → grupo poupado
7. Registro com `episode` mas sem `airDateUtc` → grupo poupado

Sobre o ciclo, com `ArrClient` e `notify` dublados:

1. Candidatos acima do teto → nenhum `delete_queue_item`, exatamente uma notificação
2. Anomalia persistente → notificação **não** repetida no ciclo seguinte
3. Contagem volta ao normal → flag reseta, próxima anomalia notifica de novo
4. `delete_queue_item` chamado com `removeFromClient`, `blocklist`, `skipRedownload` corretos
5. Grupo de N registros → exatamente **um** `delete_queue_item`
6. Grupo que dispara os dois portões → age uma vez, conta 1 contra o teto
7. Exceção no `get_queue` não propaga para fora do ciclo
8. Um dos apps fora do ar → ciclo abortado, nenhuma remoção com visão parcial
9. Portão B não é aplicado ao Radarr
10. `QUEUE_WATCH_PREAIR_ENABLED=false` → portão A ainda age, portão B não
11. `QUEUE_WATCH_ENABLED=false` → nenhuma chamada

Sobre `Settings.from_env`:

1. `QUEUE_WATCH_PREAIR_MARGIN_H=0` → levanta erro
2. Defaults ausentes → valores documentados acima

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
- **Interceptar o grab em vez da fila.** O portão B age segundos depois do grab, o que já mata o
  download antes de ele consumir banda relevante. Impedir o grab em si exigiria um proxy entre
  Sonarr e Prowlarr — muitíssimo mais peça móvel para um ganho de segundos.
- **Portão de air-date no Radarr.** O *Minimum Availability* nativo já cobre filmes.
- **Reputação automática de indexer** (desabilitar sozinho quem passar de X% de blocklist). O
  dado que embasaria isso — indexer na notificação — passa a existir com esta spec; a decisão de
  desligar um indexer continua humana, e essa fica em aberto de propósito.
