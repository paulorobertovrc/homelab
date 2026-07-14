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
