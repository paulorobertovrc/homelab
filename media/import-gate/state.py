"""Per-title attempt counter, persisted in SQLite so the loop guard survives restarts."""
import sqlite3


class AttemptStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(title_key TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )

    def _conn(self):
        return sqlite3.connect(self._db_path)

    def increment(self, title_key: str) -> int:
        with self._conn() as c:
            c.execute(
                "INSERT INTO attempts(title_key, count) VALUES(?, 1) "
                "ON CONFLICT(title_key) DO UPDATE SET count = count + 1",
                (title_key,),
            )
            row = c.execute(
                "SELECT count FROM attempts WHERE title_key = ?", (title_key,)
            ).fetchone()
            return row[0]

    def get(self, title_key: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT count FROM attempts WHERE title_key = ?", (title_key,)
            ).fetchone()
            return row[0] if row else 0

    def reset(self, title_key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM attempts WHERE title_key = ?", (title_key,))
