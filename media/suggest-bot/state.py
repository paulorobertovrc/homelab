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
