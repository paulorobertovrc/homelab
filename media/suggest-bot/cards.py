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
