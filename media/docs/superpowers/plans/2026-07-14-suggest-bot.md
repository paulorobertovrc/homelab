# suggest-bot + Jellyseerr Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portal de descoberta (Jellyseerr) + bot Telegram próprio (suggest-bot) que manda digest semanal e sob demanda de sugestões (TMDB+Trakt trending, watchlist Plex, notas IMDb/RT via MDBList) com botão ➕ que pede o download via API do Jellyseerr.

**Architecture:** Jellyseerr é imagem pronta adicionada ao compose (porta 5055) — agrega TMDB, watchlist Plex e request→Sonarr/Radarr. suggest-bot é um container Python no molde exato do `import-gate` (módulos flat, Settings por env, SQLite em `/config`, pytest com FakeSession): pipeline puro de recomendação + python-telegram-bot (long polling, JobQueue semanal + catch-up no boot).

**Tech Stack:** Docker Compose, Python 3.12-slim, `python-telegram-bot[job-queue]` (v21+), `requests`, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-14-suggest-bot-design.md`

## Global Constraints

- Seguir convenções do `import-gate`: módulos flat (sem package dir), `Settings` frozen dataclass via `from_env()`, SQLite em `${STATE_DIR}` (`/config` no container), testes com `FakeSession`/`FakeResp` injetada em `_session`, conftest com `sys.path.insert`.
- Segredos **somente** em `media/.env` (gitignored) — nunca em git. Placeholders vão em `.env.example`. *(Desvio consciente do spec, que dizia `${CONFIG_ROOT}/suggest-bot`: o padrão da stack para nossos containers é `.env`, como o import-gate; o estado SQLite continua em `${CONFIG_ROOT}/suggest-bot`.)*
- Rede: `servarr_network` com IP estático `${SET_IP_*}` (subnet real vem de `media/.env` — **ler `SERVARR_SUBNET` no `.env` local antes de escolher IPs**; `.env.example` usa `172.31.0.0/24`). IPs novos: Jellyseerr `.18`, suggest-bot `.19`.
- `TZ=${TZ:-America/Cuiaba}` em todo serviço novo.
- Commits: Conventional Commits + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Rodar testes: `cd suggest-bot && .venv/bin/python -m pytest tests/ -v` (criar venv na Task 3).
- Idioma de strings visíveis ao usuário (Telegram): **português**.

## Fatos de API verificados (2026-07-14, fontes primárias — NÃO re-inventar)

**Jellyseerr** (spec `seerr-api.yml` do repo fallenbagel/jellyseerr + código `develop`):
- Auth: header `X-Api-Key`; com API key a requisição roda como user id 1 (admin).
- `GET /api/v1/discover/trending?page=N&language=pt-BR` → `{page, totalPages, results[]}`; item: `id` (**= TMDB id**), `mediaType` (`movie|tv|person` — descartar `person`), `title`/`name`, `overview`, `posterPath`, `voteAverage`, `releaseDate`/`firstAirDate`, `adult` (só movie), `mediaInfo` (**presente só se já está no banco do Jellyseerr**) com `status`: 1=UNKNOWN 2=PENDING 3=PROCESSING 4=PARTIALLY_AVAILABLE 5=AVAILABLE 6=DELETED.
- `GET /api/v1/discover/watchlist?page=N` → watchlist Plex do admin; 20/página; item: `tmdbId`, `mediaType` (`movie|tv`), `title` (sem overview/poster — buscar detalhe).
- `GET /api/v1/movie/{tmdbId}` e `GET /api/v1/tv/{tmdbId}` → detalhe com `mediaInfo` embutido (mesmo enum). Path param é TMDB id.
- `POST /api/v1/request` body `{"mediaType": "movie|tv", "mediaId": <tmdbId>, "seasons": "all"}` (`seasons` só para tv; a string literal `"all"` pede todas exceto specials). Sucesso `201`. **Duplicado → `409`** ("Request for this media already exists."). **Todas as temporadas já pedidas/disponíveis → `202`** (não é erro HTTP). Com API key o request é auto-aprovado.
- Imagem: `fallenbagel/jellyseerr:latest`, porta 5055, config em `/app/config`.

**Trakt** (docs.trakt.tv): base `https://api.trakt.tv`; headers `Content-Type: application/json`, `trakt-api-version: 2`, `trakt-api-key: <client_id>` (GET trending **não** exige OAuth). `GET /movies/trending?limit=N` e `GET /shows/trending?limit=N` → `[{watchers, movie|show: {title, year, ids: {trakt, slug, imdb, tmdb, tvdb}}}]`; **`ids.tmdb` é nullable (sobretudo shows) — tolerar null**. Rate: 1000 GETs/5min. App grátis em `https://trakt.tv/oauth/applications/new`.

**MDBList** (docs.mdblist.com + OpenAPI api.mdblist.com/schema/): `GET https://api.mdblist.com/tmdb/{movie|show}/{tmdbId}/?apikey=KEY` (TV por TMDB id funciona). Resposta tem `ratings[]` com `{source, value, score, votes}`; sources relevantes: `imdb` (`value` 0–10), `tomatoes` (`value` 0–100). Free tier: 1000 req/dia; key em `https://mdblist.com/preferences/#api`. 429 quando estourar.

---

### Task 1: Jellyseerr no compose

**Files:**
- Modify: `compose.yaml` (novo serviço, depois do bloco `import-gate`, antes de `HOMEPAGE`)
- Modify: `.env.example` (novo `SET_IP_JELLYSEERR`)
- Modify: `.env` (idem, com IP na subnet real — ler `SERVARR_SUBNET` local)

**Interfaces:**
- Produces: Jellyseerr rodando em `http://<SET_IP_JELLYSEERR>:5055` / `http://localhost:5055`, config em `${CONFIG_ROOT}/jellyseerr`. Tasks seguintes consomem a API `/api/v1`.

- [ ] **Step 1: Adicionar o serviço ao `compose.yaml`**

Inserir entre o bloco `import-gate` e o comentário do `HOMEPAGE`:

```yaml
  ###############################################
  # JELLYSEERR — portal de descoberta e requests (fork do Overseerr)
  # Discover TMDB + watchlist Plex + botão "pedir" -> Sonarr/Radarr.
  # Egress normal (TMDB/plex.tv/host Plex) — não ride no gluetun, como radarr/sonarr.
  # API key (Settings -> General) é consumida pelo suggest-bot.
  ###############################################
  jellyseerr:
    image: fallenbagel/jellyseerr:latest
    container_name: jellyseerr
    restart: unless-stopped
    networks:
      servarr_network:
        ipv4_address: ${SET_IP_JELLYSEERR:-172.31.0.18}
    ports:
      - 5055:5055
    environment:
      - TZ=${TZ:-America/Cuiaba}
      - LOG_LEVEL=info
    volumes:
      - ${CONFIG_ROOT:-/docker/appdata}/jellyseerr:/app/config
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://localhost:5055/api/v1/status || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 60s
```

Nota: se a imagem não tiver `wget` (verificar no primeiro up com `docker exec jellyseerr which wget`), trocar o test por `["CMD-SHELL", "node -e \"fetch('http://localhost:5055/api/v1/status').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]`.

- [ ] **Step 2: Adicionar `SET_IP_JELLYSEERR` a `.env.example` e `.env`**

`.env.example`, após `SET_IP_IMPORT_GATE`:
```
SET_IP_JELLYSEERR=172.31.0.18
```
No `.env` local: mesmo nome, IP `.18` na subnet que o `SERVARR_SUBNET` local usar.

- [ ] **Step 3: Subir e verificar**

Run: `docker compose -f compose.yaml up -d jellyseerr && sleep 30 && curl -fsS http://localhost:5055/api/v1/status`
Expected: JSON com `"version"`; `docker ps` mostra jellyseerr `healthy` (aguardar até 2 min).

- [ ] **Step 4: Commit**

```bash
git add compose.yaml .env.example
git commit -m "feat(media): add Jellyseerr discovery portal (:5055)"
```

### Task 2: [GATE DO USUÁRIO] Setup manual — contas, keys e wizard

**Files:** nenhum código; só `media/.env` (local, fora do git).

**Interfaces:**
- Produces: valores reais em `media/.env` para `SUGGEST_BOT_TOKEN`, `SUGGEST_BOT_CHAT_ID`, `JELLYSEERR_API_KEY`, `TRAKT_CLIENT_ID`, `MDBLIST_API_KEY`. Jellyseerr conectado a Plex+Sonarr+Radarr.

Este task é do **usuário** (o executor apresenta o checklist e PARA até confirmação):

- [ ] **Step 1: Wizard do Jellyseerr** — abrir `http://localhost:5055` → login com conta Plex → apontar o servidor Plex (IP do host Windows, porta 32400) → conectar Radarr (`http://<SET_IP_RADARR>:7878`, API key do `.env`, profile/pasta default) e Sonarr (`http://<SET_IP_SONARR>:8989`, idem) → Settings→General→copiar API key.
- [ ] **Step 2: Bot novo no BotFather** — `/newbot`, guardar o token. Mandar `/start` pro bot e pegar o chat id (ex.: via `https://api.telegram.org/bot<TOKEN>/getUpdates`).
- [ ] **Step 3: App Trakt** — `https://trakt.tv/oauth/applications/new` (redirect URI pode ser `urn:ietf:wg:oauth:2.0:oob`); guardar o **Client ID** (secret não é usado).
- [ ] **Step 4: Key MDBList** — conta em mdblist.com → `https://mdblist.com/preferences/#api` → gerar key.
- [ ] **Step 5: Preencher `media/.env`** com os 5 valores (nomes exatos acima) e validar:

Run (do host):
```bash
set -a; source .env; set +a
curl -fsS -H "X-Api-Key: $JELLYSEERR_API_KEY" "http://localhost:5055/api/v1/discover/trending?page=1" | head -c 300; echo
curl -fsS -H "X-Api-Key: $JELLYSEERR_API_KEY" "http://localhost:5055/api/v1/discover/watchlist" | head -c 300; echo
curl -fsS -H "trakt-api-version: 2" -H "trakt-api-key: $TRAKT_CLIENT_ID" "https://api.trakt.tv/movies/trending?limit=1"; echo
curl -fsS "https://api.mdblist.com/tmdb/movie/278/?apikey=$MDBLIST_API_KEY" | head -c 300; echo
```
Expected: 4 respostas JSON sem erro (watchlist pode vir `results: []` se vazia — ok).

### Task 3: Scaffold do suggest-bot (config + venv + pytest)

**Files:**
- Create: `suggest-bot/config.py`, `suggest-bot/requirements.txt`, `suggest-bot/.dockerignore`, `suggest-bot/tests/conftest.py`
- Test: `suggest-bot/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` frozen dataclass com `from_env()` — campos: `telegram_token: str`, `telegram_chat_id: int`, `jellyseerr_url: str`, `jellyseerr_key: str`, `trakt_client_id: str`, `mdblist_key: str`, `state_dir: str`, `ntfy_url: str`, `digest_size: int`, `min_imdb: float`, `digest_weekday: int` (0=segunda…6=domingo), `digest_hour: int`, `catchup_grace_days: int`, `trending_pages: int`.

- [ ] **Step 1: venv + deps**

```bash
cd suggest-bot 2>/dev/null || mkdir -p suggest-bot/tests && cd suggest-bot
python3 -m venv .venv
.venv/bin/pip install 'python-telegram-bot[job-queue]' requests pytest
.venv/bin/pip freeze | grep -Ei '^(python-telegram-bot|requests|APScheduler|httpx)==' > requirements.txt
```
Pinar as versões que o pip resolver (não inventar números). Conferir que PTB ficou >= 21.

`.dockerignore`:
```
.venv
tests
__pycache__
.pytest_cache
```

`tests/conftest.py` (igual ao import-gate):
```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 2: Teste falhando de config**

`tests/test_config.py`:
```python
from config import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("JELLYSEERR_API_KEY", "jk")
    monkeypatch.setenv("TRAKT_CLIENT_ID", "tc")
    monkeypatch.setenv("MDBLIST_API_KEY", "mk")


def test_required_and_defaults(monkeypatch):
    _base_env(monkeypatch)
    s = Settings.from_env()
    assert s.telegram_chat_id == 123
    assert s.jellyseerr_url == "http://jellyseerr:5055"
    assert s.digest_size == 5
    assert s.min_imdb == 6.5
    assert s.digest_weekday == 4 and s.digest_hour == 18
    assert s.catchup_grace_days == 3 and s.trending_pages == 2


def test_overrides(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("DIGEST_SIZE", "8")
    monkeypatch.setenv("MIN_IMDB", "7.0")
    s = Settings.from_env()
    assert s.digest_size == 8 and s.min_imdb == 7.0
```

Run: `.venv/bin/python -m pytest tests/test_config.py -v` — Expected: FAIL (No module named 'config').

- [ ] **Step 3: Implementar `config.py`**

```python
"""Environment-driven settings. All knobs live here, nothing hardcoded elsewhere."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: int
    jellyseerr_url: str
    jellyseerr_key: str
    trakt_client_id: str
    mdblist_key: str
    state_dir: str
    ntfy_url: str
    digest_size: int
    min_imdb: float
    digest_weekday: int  # 0=segunda … 6=domingo (convenção datetime.weekday())
    digest_hour: int
    catchup_grace_days: int
    trending_pages: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=int(os.environ["TELEGRAM_CHAT_ID"]),
            jellyseerr_url=os.environ.get("JELLYSEERR_URL", "http://jellyseerr:5055"),
            jellyseerr_key=os.environ["JELLYSEERR_API_KEY"],
            trakt_client_id=os.environ["TRAKT_CLIENT_ID"],
            mdblist_key=os.environ["MDBLIST_API_KEY"],
            state_dir=os.environ.get("STATE_DIR", "/config"),
            ntfy_url=os.environ.get("NTFY_URL", "http://ntfy:80/arr-media"),
            digest_size=int(os.environ.get("DIGEST_SIZE", "5")),
            min_imdb=float(os.environ.get("MIN_IMDB", "6.5")),
            digest_weekday=int(os.environ.get("DIGEST_WEEKDAY", "4")),  # sexta
            digest_hour=int(os.environ.get("DIGEST_HOUR", "18")),
            catchup_grace_days=int(os.environ.get("CATCHUP_GRACE_DAYS", "3")),
            trending_pages=int(os.environ.get("TRENDING_PAGES", "2")),
        )
```

- [ ] **Step 4: Rodar testes** — Run: `.venv/bin/python -m pytest tests/ -v` — Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add suggest-bot/config.py suggest-bot/requirements.txt suggest-bot/.dockerignore suggest-bot/tests/
git commit -m "feat(suggest-bot): scaffold — env-driven Settings, venv, pytest"
```

### Task 4: SuggestionStore (SQLite)

**Files:**
- Create: `suggest-bot/state.py`
- Test: `suggest-bot/tests/test_state.py`

**Interfaces:**
- Produces: constantes `SUGGESTED = "suggested"`, `REQUESTED = "requested"`, `DISMISSED = "dismissed"`; classe `SuggestionStore(db_path: str)` com `status(media_type: str, tmdb_id: int) -> str | None`, `mark(media_type: str, tmdb_id: int, status: str, when_iso: str) -> None` (upsert), `last_digest_at() -> str | None`, `set_last_digest_at(when_iso: str) -> None`. Timestamps são strings ISO passadas de fora (funções determinísticas/testáveis).

- [ ] **Step 1: Teste falhando**

`tests/test_state.py`:
```python
import os
from state import SuggestionStore, SUGGESTED, REQUESTED, DISMISSED


def _store(tmp_path):
    return SuggestionStore(os.path.join(tmp_path, "s.db"))


def test_unknown_is_none(tmp_path):
    assert _store(tmp_path).status("movie", 550) is None


def test_mark_and_read(tmp_path):
    s = _store(tmp_path)
    s.mark("movie", 550, SUGGESTED, "2026-07-14T10:00:00-04:00")
    assert s.status("movie", 550) == SUGGESTED


def test_mark_upgrades_status(tmp_path):
    s = _store(tmp_path)
    s.mark("tv", 1399, SUGGESTED, "2026-07-14T10:00:00-04:00")
    s.mark("tv", 1399, REQUESTED, "2026-07-14T11:00:00-04:00")
    assert s.status("tv", 1399) == REQUESTED


def test_types_are_independent(tmp_path):
    s = _store(tmp_path)
    s.mark("movie", 100, DISMISSED, "2026-07-14T10:00:00-04:00")
    assert s.status("tv", 100) is None


def test_last_digest_roundtrip(tmp_path):
    s = _store(tmp_path)
    assert s.last_digest_at() is None
    s.set_last_digest_at("2026-07-14T18:00:00-04:00")
    assert s.last_digest_at() == "2026-07-14T18:00:00-04:00"


def test_persists_across_instances(tmp_path):
    db = os.path.join(tmp_path, "s.db")
    SuggestionStore(db).mark("movie", 550, SUGGESTED, "2026-07-14T10:00:00-04:00")
    assert SuggestionStore(db).status("movie", 550) == SUGGESTED
```

Run: `.venv/bin/python -m pytest tests/test_state.py -v` — Expected: FAIL (No module named 'state').

- [ ] **Step 2: Implementar `state.py`**

```python
"""Histórico de sugestões em SQLite: nunca repetir; registrar pedido/dispensa; carimbo do último digest."""
import sqlite3

SUGGESTED = "suggested"
REQUESTED = "requested"
DISMISSED = "dismissed"


class SuggestionStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS suggestions ("
                " media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL,"
                " status TEXT NOT NULL, updated_at TEXT NOT NULL,"
                " PRIMARY KEY (media_type, tmdb_id))"
            )
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _conn(self):
        return sqlite3.connect(self._db_path)

    def status(self, media_type: str, tmdb_id: int) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM suggestions WHERE media_type = ? AND tmdb_id = ?",
                (media_type, tmdb_id),
            ).fetchone()
            return row[0] if row else None

    def mark(self, media_type: str, tmdb_id: int, status: str, when_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO suggestions(media_type, tmdb_id, status, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(media_type, tmdb_id) DO UPDATE SET status = excluded.status, "
                "updated_at = excluded.updated_at",
                (media_type, tmdb_id, status, when_iso),
            )

    def last_digest_at(self) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key = 'last_digest_at'").fetchone()
            return row[0] if row else None

    def set_last_digest_at(self, when_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES('last_digest_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (when_iso,),
            )
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_state.py -v` — Expected: 6 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/state.py suggest-bot/tests/test_state.py && git commit -m "feat(suggest-bot): SQLite suggestion store (dedup + last-digest stamp)"`

### Task 5: Cliente Jellyseerr

**Files:**
- Create: `suggest-bot/jellyseerr.py`
- Test: `suggest-bot/tests/test_jellyseerr.py`

**Interfaces:**
- Consumes: fatos de API verificados (cabeçalho do plano).
- Produces: `JellyseerrError(Exception)`, `AlreadyRequested(JellyseerrError)`; `JellyseerrClient(base_url: str, api_key: str)` com `_session` injetável (padrão import-gate) e métodos:
  - `trending(pages: int = 1) -> list[dict]` — dicts normalizados `{media_type, tmdb_id, title, year, overview, poster_path, tmdb_score, adult, taken}` (`person` descartado; `taken` = `mediaInfo.status` ∈ {2,3,4,5}).
  - `watchlist() -> list[dict]` — todas as páginas; `{media_type, tmdb_id, title}`.
  - `detail(media_type: str, tmdb_id: int) -> dict` — mesmo shape do trending (com `adult=False`).
  - `request(media_type: str, tmdb_id: int) -> None` — levanta `AlreadyRequested` em 409/202; `raise_for_status()` no resto.

- [ ] **Step 1: Teste falhando**

`tests/test_jellyseerr.py`:
```python
import pytest
from jellyseerr import JellyseerrClient, AlreadyRequested


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.get((method, url), FakeResp())


def _client(session):
    c = JellyseerrClient("http://jellyseerr:5055", "KEY")
    c._session = session
    return c


TRENDING = {
    "page": 1, "totalPages": 1,
    "results": [
        {"id": 550, "mediaType": "movie", "title": "Fight Club", "overview": "o",
         "posterPath": "/p.jpg", "voteAverage": 8.4, "releaseDate": "1999-10-15", "adult": False},
        {"id": 1399, "mediaType": "tv", "name": "GoT", "overview": "x",
         "posterPath": "/g.jpg", "voteAverage": 8.3, "firstAirDate": "2011-04-17",
         "mediaInfo": {"status": 5}},
        {"id": 7, "mediaType": "person", "name": "Someone"},
    ],
}


def test_trending_normalizes_and_drops_people():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/discover/trending")] = FakeResp(200, TRENDING)
    items = _client(s).trending()
    assert [i["tmdb_id"] for i in items] == [550, 1399]
    movie, tv = items
    assert movie["title"] == "Fight Club" and movie["year"] == "1999" and movie["taken"] is False
    assert tv["title"] == "GoT" and tv["media_type"] == "tv" and tv["taken"] is True
    assert s.calls[0][2]["headers"]["X-Api-Key"] == "KEY"


def test_watchlist_paginates():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/discover/watchlist")] = FakeResp(200, {
        "page": 1, "totalPages": 1, "totalResults": 1,
        "results": [{"tmdbId": 603, "mediaType": "movie", "title": "The Matrix"}],
    })
    items = _client(s).watchlist()
    assert items == [{"media_type": "movie", "tmdb_id": 603, "title": "The Matrix"}]


def test_detail_movie_taken_flag():
    s = FakeSession()
    s.responses[("GET", "http://jellyseerr:5055/api/v1/movie/603")] = FakeResp(200, {
        "title": "The Matrix", "releaseDate": "1999-03-31", "overview": "neo",
        "posterPath": "/m.jpg", "voteAverage": 8.2, "mediaInfo": {"status": 3},
    })
    d = _client(s).detail("movie", 603)
    assert d["taken"] is True and d["year"] == "1999" and d["media_type"] == "movie"


def test_request_movie_body():
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(201, {"id": 1})
    _client(s).request("movie", 550)
    assert s.calls[0][2]["json"] == {"mediaType": "movie", "mediaId": 550}


def test_request_tv_asks_all_seasons():
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(201, {"id": 2})
    _client(s).request("tv", 1399)
    assert s.calls[0][2]["json"] == {"mediaType": "tv", "mediaId": 1399, "seasons": "all"}


@pytest.mark.parametrize("code", [409, 202])
def test_request_duplicate_and_noseasons_raise_already(code):
    s = FakeSession()
    s.responses[("POST", "http://jellyseerr:5055/api/v1/request")] = FakeResp(code, {"message": "dup"})
    with pytest.raises(AlreadyRequested):
        _client(s).request("movie", 550)
```

Run: `.venv/bin/python -m pytest tests/test_jellyseerr.py -v` — Expected: FAIL (No module named 'jellyseerr').

- [ ] **Step 2: Implementar `jellyseerr.py`**

```python
"""Cliente da API do Jellyseerr (X-Api-Key). Único caminho de escrita do bot (/request)."""
import requests

# MediaInfo.status (seerr-api.yml): 1=UNKNOWN 2=PENDING 3=PROCESSING
# 4=PARTIALLY_AVAILABLE 5=AVAILABLE 6=DELETED. mediaInfo ausente = fora do Jellyseerr.
_TAKEN = {2, 3, 4, 5}


class JellyseerrError(Exception):
    pass


class AlreadyRequested(JellyseerrError):
    """409 (request duplicado) ou 202 (todas as temporadas já pedidas/disponíveis)."""


def _normalize(media_type: str, tmdb_id: int, raw: dict) -> dict:
    return {
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "title": raw.get("title") or raw.get("name") or "?",
        "year": (raw.get("releaseDate") or raw.get("firstAirDate") or "")[:4],
        "overview": raw.get("overview") or "",
        "poster_path": raw.get("posterPath"),
        "tmdb_score": raw.get("voteAverage"),
        "adult": bool(raw.get("adult", False)),
        "taken": (raw.get("mediaInfo") or {}).get("status") in _TAKEN,
    }


class JellyseerrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._timeout = timeout
        self._session = requests.Session()

    def _call(self, method: str, path: str, **kw):
        return self._session.request(
            method, f"{self._base}/api/v1{path}",
            headers=self._headers, timeout=self._timeout, **kw,
        )

    def _get_json(self, path: str, **params) -> dict:
        r = self._call("GET", path, params=params or None)
        r.raise_for_status()
        return r.json()

    def trending(self, pages: int = 1) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            data = self._get_json("/discover/trending", page=page, language="pt-BR",
                                  timeWindow="week")  # spec: janela semanal, não diária
            for item in data.get("results", []):
                if item.get("mediaType") not in ("movie", "tv"):
                    continue
                out.append(_normalize(item["mediaType"], item["id"], item))
        return out

    def watchlist(self) -> list[dict]:
        out, page, total = [], 1, 1
        while page <= total:
            data = self._get_json("/discover/watchlist", page=page)
            total = data.get("totalPages", 1)
            for item in data.get("results", []):
                out.append({
                    "media_type": item["mediaType"],
                    "tmdb_id": item["tmdbId"],
                    "title": item.get("title", "?"),
                })
            page += 1
        return out

    def detail(self, media_type: str, tmdb_id: int) -> dict:
        path = f"/movie/{tmdb_id}" if media_type == "movie" else f"/tv/{tmdb_id}"
        return _normalize(media_type, tmdb_id, self._get_json(path))

    def request(self, media_type: str, tmdb_id: int) -> None:
        body = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            body["seasons"] = "all"  # string literal — pede todas menos specials
        r = self._call("POST", "/request", json=body)
        if r.status_code in (409, 202):  # 202 = NoSeasonsAvailableError (não é erro HTTP)
            raise AlreadyRequested((r.json() or {}).get("message", f"HTTP {r.status_code}"))
        r.raise_for_status()
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_jellyseerr.py -v` — Expected: 7 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/jellyseerr.py suggest-bot/tests/test_jellyseerr.py && git commit -m "feat(suggest-bot): Jellyseerr client (trending/watchlist/detail/request)"`

### Task 6: Cliente Trakt

**Files:**
- Create: `suggest-bot/trakt.py`
- Test: `suggest-bot/tests/test_trakt.py`

**Interfaces:**
- Produces: `TraktClient(client_id: str)` com `_session` injetável e `trending_tmdb_ids(limit: int = 40) -> set[tuple[str, int]]` — conjunto `{("movie", tmdbId), ("tv", tmdbId)}`; itens com `ids.tmdb` null são ignorados. Exceções de rede/HTTP **propagam** (o pipeline degrada).

- [ ] **Step 1: Teste falhando**

`tests/test_trakt.py`:
```python
from trakt import TraktClient


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.get((method, url), FakeResp(200, []))


def test_trending_merges_movies_and_shows_and_skips_null_tmdb():
    s = FakeSession()
    s.responses[("GET", "https://api.trakt.tv/movies/trending")] = FakeResp(200, [
        {"watchers": 100, "movie": {"title": "A", "year": 2026, "ids": {"trakt": 1, "tmdb": 550}}},
    ])
    s.responses[("GET", "https://api.trakt.tv/shows/trending")] = FakeResp(200, [
        {"watchers": 90, "show": {"title": "B", "year": 2026, "ids": {"trakt": 2, "tmdb": 1399}}},
        {"watchers": 80, "show": {"title": "C", "year": 2026, "ids": {"trakt": 3, "tmdb": None}}},
    ])
    c = TraktClient("CID")
    c._session = s
    assert c.trending_tmdb_ids() == {("movie", 550), ("tv", 1399)}
    headers = s.calls[0][2]["headers"]
    assert headers["trakt-api-key"] == "CID" and headers["trakt-api-version"] == "2"
    assert s.calls[0][2]["params"] == {"limit": 40}
```

Run: `.venv/bin/python -m pytest tests/test_trakt.py -v` — Expected: FAIL.

- [ ] **Step 2: Implementar `trakt.py`**

```python
"""Cliente Trakt v2 — só trending, auth por client id (sem OAuth)."""
import requests

_BASE = "https://api.trakt.tv"


class TraktClient:
    def __init__(self, client_id: str, timeout: int = 15):
        self._headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
            "User-Agent": "suggest-bot/1.0",
        }
        self._timeout = timeout
        self._session = requests.Session()

    def trending_tmdb_ids(self, limit: int = 40) -> set[tuple[str, int]]:
        """{(media_type, tmdb_id)} dos trendings; itens sem ids.tmdb são ignorados."""
        out: set[tuple[str, int]] = set()
        for kind, key, media_type in (("movies", "movie", "movie"), ("shows", "show", "tv")):
            r = self._session.request(
                "GET", f"{_BASE}/{kind}/trending",
                headers=self._headers, params={"limit": limit}, timeout=self._timeout,
            )
            r.raise_for_status()
            for item in r.json():
                tmdb = ((item.get(key) or {}).get("ids") or {}).get("tmdb")
                if tmdb:
                    out.add((media_type, tmdb))
        return out
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_trakt.py -v` — Expected: 1 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/trakt.py suggest-bot/tests/test_trakt.py && git commit -m "feat(suggest-bot): Trakt trending client (client-id only)"`

### Task 7: Cliente MDBList

**Files:**
- Create: `suggest-bot/mdblist.py`
- Test: `suggest-bot/tests/test_mdblist.py`

**Interfaces:**
- Produces: `MdblistClient(api_key: str)` com `_session` injetável e `ratings(media_type: str, tmdb_id: int) -> dict` — ex. `{"imdb": 8.1, "tomatoes": 97}`; **nunca lança** (falha → `{}`): enriquecimento é opcional por design.

- [ ] **Step 1: Teste falhando**

`tests/test_mdblist.py`:
```python
import requests
from mdblist import MdblistClient


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.get((method, url), FakeResp(500))


def _client(session):
    c = MdblistClient("MK")
    c._session = session
    return c


def test_ratings_extracts_imdb_and_tomatoes():
    s = FakeSession()
    s.responses[("GET", "https://api.mdblist.com/tmdb/movie/578/")] = FakeResp(200, {
        "title": "Jaws",
        "ratings": [
            {"source": "imdb", "value": 8.1, "score": 81},
            {"source": "tomatoes", "value": 97, "score": 97},
            {"source": "metacritic", "value": 87, "score": 87},
            {"source": "letterboxd", "value": None, "score": None},
        ],
    })
    assert _client(s).ratings("movie", 578) == {"imdb": 8.1, "tomatoes": 97}
    assert s.calls[0][2]["params"] == {"apikey": "MK"}


def test_tv_uses_show_path():
    s = FakeSession()
    s.responses[("GET", "https://api.mdblist.com/tmdb/show/1399/")] = FakeResp(200, {"ratings": []})
    assert _client(s).ratings("tv", 1399) == {}


def test_http_error_returns_empty():
    assert _client(FakeSession()).ratings("movie", 1) == {}
```

Run: `.venv/bin/python -m pytest tests/test_mdblist.py -v` — Expected: FAIL.

- [ ] **Step 2: Implementar `mdblist.py`**

```python
"""Cliente MDBList — notas IMDb/RT por TMDB id. Best-effort: falha vira dict vazio."""
import logging

import requests

log = logging.getLogger(__name__)
_BASE = "https://api.mdblist.com"
_SOURCES = ("imdb", "tomatoes")  # value: imdb 0-10, tomatoes 0-100


class MdblistClient:
    def __init__(self, api_key: str, timeout: int = 15):
        self._key = api_key
        self._timeout = timeout
        self._session = requests.Session()

    def ratings(self, media_type: str, tmdb_id: int) -> dict:
        mtype = "movie" if media_type == "movie" else "show"
        try:
            r = self._session.request(
                "GET", f"{_BASE}/tmdb/{mtype}/{tmdb_id}/",
                params={"apikey": self._key}, timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("mdblist falhou para %s/%s: %s", media_type, tmdb_id, exc)
            return {}
        return {
            rt["source"]: rt["value"]
            for rt in data.get("ratings", [])
            if rt.get("source") in _SOURCES and rt.get("value") is not None
        }
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_mdblist.py -v` — Expected: 3 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/mdblist.py suggest-bot/tests/test_mdblist.py && git commit -m "feat(suggest-bot): MDBList ratings client (best-effort)"`

### Task 8: Pipeline de recomendação

**Files:**
- Create: `suggest-bot/pipeline.py`
- Test: `suggest-bot/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `JellyseerrClient` (`watchlist`, `detail`, `trending`), `TraktClient.trending_tmdb_ids`, `MdblistClient.ratings`, `SuggestionStore` (`status`, `mark`, `SUGGESTED`).
- Produces: `@dataclass Suggestion` (`media_type: str`, `tmdb_id: int`, `title: str`, `year: str`, `overview: str`, `poster_path: str | None`, `source: str` — `"watchlist"|"trending"`, `tmdb_score: float | None`, `in_trakt: bool = False`, `ratings: dict = {}`); função `build_digest(jelly, trakt, mdb, store, cfg, now_iso: str) -> tuple[list[Suggestion], list[str]]` — retorna (sugestões já marcadas SUGGESTED no store, notas de degradação p/ o header).

Regras (do spec): watchlist primeiro e fura filtro de nota; trending filtra adulto/taken/já-visto e nota IMDb < `cfg.min_imdb` (quando nota existe); boost por interseção com Trakt; Trakt/MDBList fora → degrada com nota no header; Jellyseerr fora → exceção propaga.

- [ ] **Step 1: Teste falhando**

`tests/test_pipeline.py`:
```python
from types import SimpleNamespace

from pipeline import build_digest
from state import SuggestionStore, SUGGESTED, DISMISSED

CFG = SimpleNamespace(digest_size=3, min_imdb=6.5, trending_pages=1)
NOW = "2026-07-14T18:00:00-04:00"


def _t(tmdb_id, title, score=7.0, adult=False, taken=False, media_type="movie"):
    return {"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": "2026",
            "overview": "o", "poster_path": "/p.jpg", "tmdb_score": score,
            "adult": adult, "taken": taken}


class FakeJelly:
    def __init__(self, watchlist=(), trending=(), details=None):
        self._w, self._t, self._d = list(watchlist), list(trending), details or {}

    def watchlist(self):
        return self._w

    def trending(self, pages=1):
        return self._t

    def detail(self, media_type, tmdb_id):
        return self._d[(media_type, tmdb_id)]


class FakeTrakt:
    def __init__(self, ids=frozenset(), boom=False):
        self._ids, self._boom = set(ids), boom

    def trending_tmdb_ids(self, limit=40):
        if self._boom:
            raise RuntimeError("trakt down")
        return self._ids


class FakeMdb:
    def __init__(self, table=None):
        self._table = table or {}

    def ratings(self, media_type, tmdb_id):
        return self._table.get((media_type, tmdb_id), {})


def _store(tmp_path):
    return SuggestionStore(str(tmp_path / "s.db"))


def test_watchlist_first_and_skips_taken(tmp_path):
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"},
                   {"media_type": "movie", "tmdb_id": 604, "title": "Owned"}],
        trending=[_t(550, "Fight Club", score=8.4)],
        details={("movie", 603): _t(603, "Matrix", taken=False),
                 ("movie", 604): _t(604, "Owned", taken=True)},
    )
    got, notes = build_digest(jelly, FakeTrakt(), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert [(s.source, s.tmdb_id) for s in got] == [("watchlist", 603), ("trending", 550)]
    assert notes == []


def test_watchlist_ignores_imdb_floor_but_trending_respects_it(tmp_path):
    mdb = FakeMdb({("movie", 603): {"imdb": 4.0}, ("movie", 550): {"imdb": 5.0},
                   ("movie", 551): {"imdb": 8.0}})
    jelly = FakeJelly(
        watchlist=[{"media_type": "movie", "tmdb_id": 603, "title": "Matrix"}],
        trending=[_t(550, "Low"), _t(551, "High")],
        details={("movie", 603): _t(603, "Matrix")},
    )
    got, _ = build_digest(jelly, FakeTrakt(), mdb, _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [603, 551]


def test_trakt_boost_wins_over_score(tmp_path):
    jelly = FakeJelly(trending=[_t(550, "HighScore", score=9.0), _t(551, "InTrakt", score=7.0)])
    got, _ = build_digest(jelly, FakeTrakt({("movie", 551)}), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert got[0].tmdb_id == 551 and got[0].in_trakt is True


def test_filters_adult_taken_and_already_seen(tmp_path):
    store = _store(tmp_path)
    store.mark("movie", 552, DISMISSED, NOW)
    jelly = FakeJelly(trending=[_t(550, "Ok"), _t(551, "Adult", adult=True),
                                _t(552, "Seen"), _t(553, "Taken", taken=True)])
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert [s.tmdb_id for s in got] == [550]


def test_marks_final_picks_as_suggested(tmp_path):
    store = _store(tmp_path)
    jelly = FakeJelly(trending=[_t(550, "A")])
    build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert store.status("movie", 550) == SUGGESTED
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), store, CFG, NOW)
    assert got == []


def test_trakt_down_degrades_with_note(tmp_path):
    jelly = FakeJelly(trending=[_t(550, "A")])
    got, notes = build_digest(jelly, FakeTrakt(boom=True), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert [s.tmdb_id for s in got] == [550]
    assert len(notes) == 1 and "Trakt" in notes[0]


def test_cut_to_digest_size(tmp_path):
    jelly = FakeJelly(trending=[_t(500 + i, f"T{i}") for i in range(10)])
    got, _ = build_digest(jelly, FakeTrakt(), FakeMdb(), _store(tmp_path), CFG, NOW)
    assert len(got) == CFG.digest_size
```

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v` — Expected: FAIL.

- [ ] **Step 2: Implementar `pipeline.py`**

```python
"""Pipeline do digest: coleta (Jellyseerr/Trakt) -> filtros -> notas -> ranking -> corte."""
import logging
from dataclasses import dataclass, field

from state import SUGGESTED

log = logging.getLogger(__name__)


@dataclass
class Suggestion:
    media_type: str
    tmdb_id: int
    title: str
    year: str
    overview: str
    poster_path: str | None
    source: str  # "watchlist" | "trending"
    tmdb_score: float | None
    in_trakt: bool = False
    ratings: dict = field(default_factory=dict)


def _from_dict(d: dict, source: str, in_trakt: bool = False) -> Suggestion:
    return Suggestion(
        media_type=d["media_type"], tmdb_id=d["tmdb_id"], title=d["title"],
        year=d["year"], overview=d["overview"], poster_path=d["poster_path"],
        source=source, tmdb_score=d["tmdb_score"], in_trakt=in_trakt,
    )


def build_digest(jelly, trakt, mdb, store, cfg, now_iso: str):
    """-> (sugestões marcadas SUGGESTED, notas de degradação). Jellyseerr fora: propaga."""
    notes: list[str] = []
    seen: set[tuple[str, int]] = set()

    # 1. Watchlist — intenção explícita: primeiro no rank, fura o filtro de nota.
    picks: list[Suggestion] = []
    for item in jelly.watchlist():
        key = (item["media_type"], item["tmdb_id"])
        if key in seen or store.status(*key) is not None:
            continue
        seen.add(key)
        d = jelly.detail(*key)
        if d["taken"]:
            continue
        picks.append(_from_dict(d, "watchlist"))

    # 2. Trakt — degrada para vazio se fora.
    try:
        trakt_ids = trakt.trending_tmdb_ids()
    except Exception as exc:  # noqa: BLE001 — qualquer falha de rede/HTTP degrada
        log.warning("Trakt indisponível: %s", exc)
        trakt_ids = set()
        notes.append("Trakt fora do ar — trending só do TMDB")

    # 3. Candidatos trending (filtros baratos primeiro).
    candidates: list[Suggestion] = []
    for t in jelly.trending(pages=cfg.trending_pages):
        key = (t["media_type"], t["tmdb_id"])
        if key in seen or t["adult"] or t["taken"] or store.status(*key) is not None:
            continue
        seen.add(key)
        candidates.append(_from_dict(t, "trending", in_trakt=key in trakt_ids))

    # 4. Pré-rank sem notas; shortlist p/ economizar quota do MDBList (1000/dia).
    candidates.sort(key=lambda s: (s.in_trakt, s.tmdb_score or 0), reverse=True)
    slots = max(0, cfg.digest_size - len(picks))
    shortlist = candidates[: slots * 3]

    # 5. Notas: watchlist só enriquece; trending também filtra pelo piso IMDb.
    # MDBList sem nota = sem filtro para aquele título (o cliente já loga o warning).
    for s in picks:
        s.ratings = mdb.ratings(s.media_type, s.tmdb_id)
    kept: list[Suggestion] = []
    for s in shortlist:
        s.ratings = mdb.ratings(s.media_type, s.tmdb_id)
        imdb = s.ratings.get("imdb")
        if imdb is not None and imdb < cfg.min_imdb:
            continue
        kept.append(s)

    # 6. Rank final e corte.
    kept.sort(key=lambda s: (s.in_trakt, s.ratings.get("imdb") or 0, s.tmdb_score or 0),
              reverse=True)
    final = (picks + kept)[: cfg.digest_size]

    # 7. Só os que saem no digest viram "suggested".
    for s in final:
        store.mark(s.media_type, s.tmdb_id, SUGGESTED, now_iso)
    return final, notes
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_pipeline.py -v` — Expected: 7 PASS.

- [ ] **Step 4: Commit** — `git add suggest-bot/pipeline.py suggest-bot/tests/test_pipeline.py && git commit -m "feat(suggest-bot): recommendation pipeline (merge/filter/rank/dedup)"`

### Task 9: Cards e callbacks

**Files:**
- Create: `suggest-bot/cards.py`
- Test: `suggest-bot/tests/test_cards.py`

**Interfaces:**
- Consumes: `Suggestion` (Task 8).
- Produces: `poster_url(s) -> str | None`; `caption(s) -> str` (HTML, título escapado, notas TMDB/IMDb/🍅, sinopse truncada em 400 chars); `callback_add(s) -> str` / `callback_dismiss(s) -> str` (`"add:movie:550"` / `"dis:tv:1399"` — cabe nos 64 bytes do Telegram); `parse_callback(data: str) -> tuple[str, str, int]` (levanta `ValueError` se malformado).

- [ ] **Step 1: Teste falhando**

`tests/test_cards.py`:
```python
import pytest
from cards import callback_add, callback_dismiss, caption, parse_callback, poster_url
from pipeline import Suggestion


def _s(**kw):
    base = dict(media_type="movie", tmdb_id=550, title="Fight Club", year="1999",
                overview="Um cara cansado.", poster_path="/p.jpg", source="trending",
                tmdb_score=8.4, in_trakt=True, ratings={"imdb": 8.8, "tomatoes": 79})
    base.update(kw)
    return Suggestion(**base)


def test_poster_url():
    assert poster_url(_s()) == "https://image.tmdb.org/t/p/w500/p.jpg"
    assert poster_url(_s(poster_path=None)) is None


def test_caption_has_title_ratings_and_escapes_html():
    c = caption(_s(title="Tom & Jerry <3"))
    assert "Tom &amp; Jerry &lt;3" in c and "(1999)" in c
    assert "TMDB 8.4" in c and "IMDb 8.8" in c and "🍅 79%" in c and "🔥" in c


def test_caption_watchlist_badge_and_missing_ratings():
    c = caption(_s(source="watchlist", ratings={}, tmdb_score=None))
    assert "👁" in c and "IMDb" not in c


def test_caption_truncates_overview():
    c = caption(_s(overview="x" * 500))
    assert "x" * 400 + "…" in c and "x" * 401 not in c


def test_callback_roundtrip():
    s = _s(media_type="tv", tmdb_id=1399)
    assert parse_callback(callback_add(s)) == ("add", "tv", 1399)
    assert parse_callback(callback_dismiss(s)) == ("dis", "tv", 1399)
    assert len(callback_add(s).encode()) <= 64


@pytest.mark.parametrize("bad", ["", "add:movie", "zap:movie:1", "add:song:1", "add:movie:x"])
def test_parse_callback_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_callback(bad)
```

Run: `.venv/bin/python -m pytest tests/test_cards.py -v` — Expected: FAIL.

- [ ] **Step 2: Implementar `cards.py`**

```python
"""Renderização dos cards (HTML do Telegram) e callback data dos botões (<=64 bytes)."""
from html import escape

POSTER_BASE = "https://image.tmdb.org/t/p/w500"
_TYPE = {"movie": "🎬", "tv": "📺"}
_SOURCE = {"watchlist": "👁 Watchlist", "trending": "🔥 Em alta"}
_OVERVIEW_MAX = 400


def poster_url(s) -> str | None:
    return f"{POSTER_BASE}{s.poster_path}" if s.poster_path else None


def caption(s) -> str:
    lines = [f"{_TYPE[s.media_type]} <b>{escape(s.title)}</b> ({s.year or '?'}) — {_SOURCE[s.source]}"]
    scores = []
    if s.tmdb_score:
        scores.append(f"⭐ TMDB {s.tmdb_score:.1f}")
    if s.ratings.get("imdb") is not None:
        scores.append(f"IMDb {s.ratings['imdb']:.1f}")
    if s.ratings.get("tomatoes") is not None:
        scores.append(f"🍅 {s.ratings['tomatoes']:.0f}%")
    if scores:
        lines.append(" · ".join(scores))
    if s.overview:
        text = s.overview
        if len(text) > _OVERVIEW_MAX:
            text = text[:_OVERVIEW_MAX] + "…"
        lines.append(escape(text))
    return "\n".join(lines)


def callback_add(s) -> str:
    return f"add:{s.media_type}:{s.tmdb_id}"


def callback_dismiss(s) -> str:
    return f"dis:{s.media_type}:{s.tmdb_id}"


def parse_callback(data: str) -> tuple[str, str, int]:
    """'add:movie:550' -> ('add','movie',550). ValueError se malformado."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in ("add", "dis") or parts[1] not in ("movie", "tv"):
        raise ValueError(f"callback inválido: {data!r}")
    return parts[0], parts[1], int(parts[2])
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_cards.py -v` — Expected: 6 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/cards.py suggest-bot/tests/test_cards.py && git commit -m "feat(suggest-bot): telegram cards + inline-button callbacks"`

### Task 10: Catch-up do digest (scheduling)

**Files:**
- Create: `suggest-bot/scheduling.py`
- Test: `suggest-bot/tests/test_scheduling.py`

**Interfaces:**
- Produces: `last_slot(now: datetime, weekday: int, hour: int) -> datetime` (slot agendado mais recente ≤ now); `should_catch_up(now: datetime, last_sent: datetime | None, weekday: int, hour: int, grace_days: int) -> bool`. Regra do spec: dispara no boot se a última janela foi perdida há < `grace_days`; mais velho, espera o ciclo. Funciona com datetimes naive ou aware (consistentes entre si).

- [ ] **Step 1: Teste falhando**

`tests/test_scheduling.py`:
```python
from datetime import datetime

from scheduling import last_slot, should_catch_up

# 2026-07-14 é terça (weekday 1). Digest: sexta (4) 18h.
FRI, HR, GRACE = 4, 18, 3


def test_last_slot_goes_back_to_friday():
    now = datetime(2026, 7, 14, 10, 0)  # terça
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 10, 18, 0)


def test_last_slot_same_day_before_hour_goes_to_prev_week():
    now = datetime(2026, 7, 10, 17, 0)  # sexta 17h, antes das 18h
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 3, 18, 0)


def test_last_slot_same_day_after_hour_is_today():
    now = datetime(2026, 7, 10, 19, 0)
    assert last_slot(now, FRI, HR) == datetime(2026, 7, 10, 18, 0)


def test_catch_up_when_missed_within_grace():
    now = datetime(2026, 7, 12, 9, 0)  # domingo; slot sexta 18h há <3d
    assert should_catch_up(now, datetime(2026, 7, 3, 18, 5), FRI, HR, GRACE) is True


def test_no_catch_up_when_already_sent():
    now = datetime(2026, 7, 12, 9, 0)
    assert should_catch_up(now, datetime(2026, 7, 10, 18, 5), FRI, HR, GRACE) is False


def test_no_catch_up_when_too_old():
    now = datetime(2026, 7, 14, 10, 0)  # terça; slot sexta 18h há >3d
    assert should_catch_up(now, datetime(2026, 7, 3, 18, 5), FRI, HR, GRACE) is False


def test_never_sent_within_grace_catches_up():
    now = datetime(2026, 7, 12, 9, 0)
    assert should_catch_up(now, None, FRI, HR, GRACE) is True
```

Run: `.venv/bin/python -m pytest tests/test_scheduling.py -v` — Expected: FAIL.

- [ ] **Step 2: Implementar `scheduling.py`**

```python
"""Catch-up do digest semanal: se a stack estava desligada na janela, dispara no boot
se a janela perdida tem menos de grace_days; mais velha que isso, espera o próximo ciclo."""
from datetime import datetime, timedelta


def last_slot(now: datetime, weekday: int, hour: int) -> datetime:
    """O horário agendado (weekday/hour semanal) mais recente <= now."""
    slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    slot -= timedelta(days=(now.weekday() - weekday) % 7)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def should_catch_up(now: datetime, last_sent: datetime | None,
                    weekday: int, hour: int, grace_days: int) -> bool:
    slot = last_slot(now, weekday, hour)
    if last_sent is not None and last_sent >= slot:
        return False
    return (now - slot) <= timedelta(days=grace_days)
```

- [ ] **Step 3: Rodar** — `.venv/bin/python -m pytest tests/test_scheduling.py -v` — Expected: 7 PASS.
- [ ] **Step 4: Commit** — `git add suggest-bot/scheduling.py suggest-bot/tests/test_scheduling.py && git commit -m "feat(suggest-bot): weekly digest catch-up logic"`

### Task 11: Bot (wiring PTB) + notify

**Files:**
- Create: `suggest-bot/bot.py`, `suggest-bot/notify.py`
- Test: `suggest-bot/tests/test_bot.py`

**Interfaces:**
- Consumes: tudo das tasks 3–10.
- Produces: entrypoint `python bot.py` — long polling; `/start`, `/sugira`, callbacks `add:`/`dis:`; job semanal (`JobQueue.run_daily`) + catch-up no `post_init`; falha do digest → ntfy + mensagem no chat. `notify.push(ntfy_url, title, message)` best-effort (mesma convenção do `import-gate/notify.py` — conferir e espelhar o arquivo real).

- [ ] **Step 1: `notify.py`** (espelhar import-gate; ajustar se o real divergir)

```python
"""Push ntfy best-effort (mesma convenção do import-gate/notify.py)."""
import logging

import requests

log = logging.getLogger(__name__)


def push(ntfy_url: str, title: str, message: str) -> None:
    try:
        requests.post(ntfy_url, data=message.encode("utf-8"),
                      headers={"Title": title, "Tags": "robot"}, timeout=10)
    except requests.RequestException as exc:
        log.warning("ntfy push falhou: %s", exc)
```

- [ ] **Step 2: Teste falhando (partes testáveis do bot: montagem de teclado e edição de status)**

`tests/test_bot.py`:
```python
from bot import keyboard_for, status_suffix
from pipeline import Suggestion


def _s():
    return Suggestion(media_type="movie", tmdb_id=550, title="F", year="1999",
                      overview="", poster_path=None, source="trending", tmdb_score=8.0)


def test_keyboard_has_add_and_dismiss():
    kb = keyboard_for(_s())
    row = kb.inline_keyboard[0]
    assert row[0].callback_data == "add:movie:550"
    assert row[1].callback_data == "dis:movie:550"
    assert "Adicionar" in row[0].text and "Dispensar" in row[1].text


def test_status_suffix():
    assert status_suffix("requested") == "\n\n✅ Pedido"
    assert status_suffix("already") == "\n\n✅ Já pedido/disponível"
    assert status_suffix("dismissed") == "\n\n🙈 Dispensado"
```

Run: `.venv/bin/python -m pytest tests/test_bot.py -v` — Expected: FAIL.

- [ ] **Step 3: Implementar `bot.py`**

```python
"""suggest-bot — digest semanal + /sugira; botões ➕/🙈 fecham o ciclo via Jellyseerr."""
import asyncio
import logging
import os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, Defaults, filters)

import cards
import notify
import scheduling
from config import Settings
from jellyseerr import AlreadyRequested, JellyseerrClient
from mdblist import MdblistClient
from pipeline import build_digest
from state import DISMISSED, REQUESTED, SuggestionStore
from trakt import TraktClient

log = logging.getLogger("suggest-bot")

_STATUS = {"requested": "✅ Pedido", "already": "✅ Já pedido/disponível",
           "dismissed": "🙈 Dispensado"}


def status_suffix(kind: str) -> str:
    return f"\n\n{_STATUS[kind]}"


def keyboard_for(s) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Adicionar", callback_data=cards.callback_add(s)),
        InlineKeyboardButton("🙈 Dispensar", callback_data=cards.callback_dismiss(s)),
    ]])


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


async def send_digest(app: Application, trigger: str) -> None:
    cfg, deps = app.bot_data["cfg"], app.bot_data["deps"]
    try:
        suggestions, notes = await asyncio.to_thread(
            build_digest, deps["jelly"], deps["trakt"], deps["mdb"],
            deps["store"], cfg, _now_iso())
    except Exception:
        log.exception("digest falhou (%s)", trigger)
        notify.push(cfg.ntfy_url, "suggest-bot", f"Digest falhou ({trigger}) — ver logs")
        await app.bot.send_message(cfg.telegram_chat_id,
                                   "❌ Não consegui montar as sugestões (Jellyseerr fora?).")
        return
    if not suggestions:
        await app.bot.send_message(cfg.telegram_chat_id, "📭 Sem sugestões novas desta vez.")
    else:
        header = f"📬 Sugestões ({len(suggestions)})"
        if notes:
            header += "\n⚠️ " + "; ".join(notes)
        await app.bot.send_message(cfg.telegram_chat_id, header)
        for s in suggestions:
            url = cards.poster_url(s)
            kwargs = dict(caption=cards.caption(s), parse_mode="HTML",
                          reply_markup=keyboard_for(s))
            if url:
                await app.bot.send_photo(cfg.telegram_chat_id, url, **kwargs)
            else:
                await app.bot.send_message(cfg.telegram_chat_id, kwargs.pop("caption"),
                                           parse_mode="HTML", reply_markup=keyboard_for(s))
    deps["store"].set_last_digest_at(_now_iso())
    log.info("digest enviado (%s): %d sugestões", trigger, len(suggestions))


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Sou o suggest-bot. Mando sugestões toda semana; /sugira pede na hora.")


async def on_sugira(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔎 Buscando sugestões…")
    await send_digest(context.application, "sob demanda")


async def _edit_status(query, kind: str) -> None:
    msg = query.message
    if msg.caption is not None:
        await msg.edit_caption(msg.caption + status_suffix(kind), reply_markup=None)
    else:
        await msg.edit_text(msg.text + status_suffix(kind), reply_markup=None)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, deps = context.bot_data["cfg"], context.bot_data["deps"]
    q = update.callback_query
    if q.message is None or q.message.chat_id != cfg.telegram_chat_id:
        await q.answer()
        return
    try:
        action, media_type, tmdb_id = cards.parse_callback(q.data)
    except ValueError:
        await q.answer("Botão inválido")
        return
    if action == "add":
        try:
            await asyncio.to_thread(deps["jelly"].request, media_type, tmdb_id)
            deps["store"].mark(media_type, tmdb_id, REQUESTED, _now_iso())
            await q.answer("Pedido!")
            await _edit_status(q, "requested")
        except AlreadyRequested:
            deps["store"].mark(media_type, tmdb_id, REQUESTED, _now_iso())
            await q.answer("Já estava pedido/disponível")
            await _edit_status(q, "already")
        except Exception:
            log.exception("request %s/%s falhou", media_type, tmdb_id)
            await q.answer("❌ Falhou — tenta de novo", show_alert=True)  # mantém botões
    else:
        deps["store"].mark(media_type, tmdb_id, DISMISSED, _now_iso())
        await q.answer("Dispensado")
        await _edit_status(q, "dismissed")


async def _weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_digest(context.application, "semanal")


async def _post_init(app: Application) -> None:
    cfg, deps = app.bot_data["cfg"], app.bot_data["deps"]
    last = deps["store"].last_digest_at()
    last_dt = datetime.fromisoformat(last) if last else None
    if scheduling.should_catch_up(datetime.now().astimezone(), last_dt,
                                  cfg.digest_weekday, cfg.digest_hour,
                                  cfg.catchup_grace_days):
        log.info("janela do digest perdida — catch-up no boot")
        await send_digest(app, "catch-up")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Settings.from_env()
    tz = ZoneInfo(os.environ.get("TZ", "America/Cuiaba"))
    deps = {
        "jelly": JellyseerrClient(cfg.jellyseerr_url, cfg.jellyseerr_key),
        "trakt": TraktClient(cfg.trakt_client_id),
        "mdb": MdblistClient(cfg.mdblist_key),
        "store": SuggestionStore(os.path.join(cfg.state_dir, "state.db")),
    }
    app = (Application.builder().token(cfg.telegram_token)
           .defaults(Defaults(tzinfo=tz)).post_init(_post_init).build())
    app.bot_data.update({"cfg": cfg, "deps": deps})
    only_owner = filters.Chat(chat_id=cfg.telegram_chat_id)
    app.add_handler(CommandHandler("start", on_start, filters=only_owner))
    app.add_handler(CommandHandler("sugira", on_sugira, filters=only_owner))
    app.add_handler(CallbackQueryHandler(on_button))
    app.job_queue.run_daily(_weekly_job, time=dtime(hour=cfg.digest_hour, tzinfo=tz),
                            days=(cfg.digest_weekday,))
    log.info("suggest-bot no ar — digest weekday=%d %dh", cfg.digest_weekday, cfg.digest_hour)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

**⚠️ Verificar na doc do PTB instalado** (não assumir): a semântica do parâmetro `days` de `run_daily` — se 0=segunda (convenção `datetime.weekday()`) ou 0=domingo. Checar `.venv/lib/python3.12/site-packages/telegram/ext/_jobqueue.py` (docstring de `run_daily`). Se for 0=domingo, converter: `days=((cfg.digest_weekday + 1) % 7,)`. Documentar a conversão num comentário.

- [ ] **Step 4: Rodar toda a suíte** — `.venv/bin/python -m pytest tests/ -v` — Expected: todos PASS.

- [ ] **Step 5: Smoke local (fora do Docker)** — com o `.env` preenchido (Task 2):

```bash
cd suggest-bot
set -a; source ../.env; set +a
mkdir -p /tmp/suggest-bot-smoke
STATE_DIR=/tmp/suggest-bot-smoke TELEGRAM_BOT_TOKEN=$SUGGEST_BOT_TOKEN \
  TELEGRAM_CHAT_ID=$SUGGEST_BOT_CHAT_ID JELLYSEERR_URL=http://localhost:5055 \
  .venv/bin/python bot.py
```
Expected: loga "suggest-bot no ar"; no Telegram, `/start` responde; `/sugira` entrega cards com botões. Ctrl-C encerra. (Se a janela semanal foi "perdida", o catch-up dispara já no boot — esperado.)

- [ ] **Step 6: Commit** — `git add suggest-bot/bot.py suggest-bot/notify.py suggest-bot/tests/test_bot.py && git commit -m "feat(suggest-bot): PTB wiring — weekly digest, /sugira, add/dismiss buttons"`

### Task 12: Containerizar + deploy + e2e + docs

**Files:**
- Create: `suggest-bot/Dockerfile`
- Modify: `compose.yaml` (serviço suggest-bot), `.env.example` (SET_IP + placeholders dos segredos), `.env` (local), `README.md` (tabela de serviços/acessos)

**Interfaces:**
- Consumes: imagem build de `./suggest-bot`; segredos da Task 2.
- Produces: suggest-bot rodando 24/7 na stack; docs atualizados.

- [ ] **Step 1: `suggest-bot/Dockerfile`** (molde do import-gate; sem ffmpeg, sem porta)

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

CMD ["python", "bot.py"]
```

- [ ] **Step 2: Serviço no `compose.yaml`** (após o bloco jellyseerr)

```yaml
  ###############################################
  # SUGGEST-BOT — digest semanal + /sugira no Telegram com botão ➕ -> Jellyseerr
  # Fontes: trending TMDB (via Jellyseerr) + Trakt + watchlist Plex; notas via MDBList.
  # Long polling (sem porta exposta); estado SQLite em ${CONFIG_ROOT}/suggest-bot.
  # Sem healthcheck: processo de long-polling sem endpoint; PTB re-tenta rede
  # sozinho e restart:unless-stopped cobre crash.
  ###############################################
  suggest-bot:
    build: ./suggest-bot
    container_name: suggest-bot
    restart: unless-stopped
    user: "${PUID:-1000}:${PGID:-1000}"
    networks:
      servarr_network:
        ipv4_address: ${SET_IP_SUGGEST_BOT:-172.31.0.19}
    environment:
      - TZ=${TZ:-America/Cuiaba}
      - TELEGRAM_BOT_TOKEN=${SUGGEST_BOT_TOKEN:?Set SUGGEST_BOT_TOKEN in media/.env}
      - TELEGRAM_CHAT_ID=${SUGGEST_BOT_CHAT_ID:?Set SUGGEST_BOT_CHAT_ID in media/.env}
      - JELLYSEERR_URL=http://${SET_IP_JELLYSEERR:-172.31.0.18}:5055
      - JELLYSEERR_API_KEY=${JELLYSEERR_API_KEY:?Set JELLYSEERR_API_KEY in media/.env}
      - TRAKT_CLIENT_ID=${TRAKT_CLIENT_ID:?Set TRAKT_CLIENT_ID in media/.env}
      - MDBLIST_API_KEY=${MDBLIST_API_KEY:?Set MDBLIST_API_KEY in media/.env}
      - NTFY_URL=http://${SET_IP_NTFY:-172.39.0.10}:80/arr-media
    volumes:
      - ${CONFIG_ROOT:-/docker/appdata}/suggest-bot:/config
```

(Conferir no compose o nome real da env do IP do ntfy usado pelo import-gate — `SET_IP_NTFY` — e replicar exatamente.)

- [ ] **Step 3: `.env.example`** — adicionar após `SET_IP_JELLYSEERR`:

```
SET_IP_SUGGEST_BOT=172.31.0.19
# suggest-bot (Telegram digest) — ver docs/superpowers/specs/2026-07-14-suggest-bot-design.md
SUGGEST_BOT_TOKEN=123456:telegram-bot-token
SUGGEST_BOT_CHAT_ID=123456789
JELLYSEERR_API_KEY=from-jellyseerr-settings-general
TRAKT_CLIENT_ID=from-trakt-oauth-applications
MDBLIST_API_KEY=from-mdblist-preferences
```
No `.env` local: `SET_IP_SUGGEST_BOT` na subnet real (os segredos já entraram na Task 2).

- [ ] **Step 4: Criar dir de estado, build e subir**

```bash
mkdir -p /docker/appdata/suggest-bot
docker compose -f compose.yaml up -d --build suggest-bot
docker logs -f suggest-bot   # esperar "suggest-bot no ar"
```
Expected: sem tracebacks; se a janela semanal estava perdida, digest de catch-up chega no Telegram.

- [ ] **Step 5: E2E manual (evidência antes de asserção)**

1. `/sugira` no Telegram → header + até 5 cards com pôster/notas/botões.
2. Tocar **➕** num filme → card vira "✅ Pedido" → request aparece no Jellyseerr (`http://localhost:5055/requests`) **e** o filme aparece no Radarr (Activity/queue ou Wanted).
3. Tocar **🙈** noutro card → "🙈 Dispensado"; rodar `/sugira` de novo → título dispensado não reaparece.
4. Tocar **➕** num título já pedido (repetir o mesmo) — não há botão novo, então validar via segundo request no Jellyseerr UI: deve acusar duplicado. (O caminho 409 do bot fica coberto pelos testes unitários.)
5. `docker exec suggest-bot python -c "import sqlite3; print(sqlite3.connect('/config/state.db').execute('select * from suggestions').fetchall())"` → linhas com statuses corretos.

- [ ] **Step 6: README** — na seção de acesso, adicionar `Jellyseerr :5055` e citar o suggest-bot (sem porta) na lista de serviços; parágrafo curto de 3-4 linhas descrevendo o fluxo (digest semanal, /sugira, botão → Jellyseerr → Radarr/Sonarr → import-gate).

- [ ] **Step 7: Commit final**

```bash
git add suggest-bot/Dockerfile compose.yaml .env.example README.md
git commit -m "feat(media): suggest-bot container — weekly Telegram digest wired to Jellyseerr"
```

### Task 13 (opcional, se o usuário quiser): card no Homepage

Config do Homepage vive em `${CONFIG_ROOT}/homepage` (fora do git) — adicionar o widget do Jellyseerr lá é mudança manual de config, não de repo. Apresentar ao usuário como follow-up, não bloquear o plano.
