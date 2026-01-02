# persistent_conid_storage.py

import sqlite3
from datetime import datetime, timedelta
from typing import Optional


class PersistentConidStorage:
    def __init__(self, db_path: str = "conids.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conids (
                    symbol TEXT PRIMARY KEY,
                    conid TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def store_conid(self, symbol: str, conid: str) -> None:
        """
        Insert or update a conid for a given symbol.
        """
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO conids (symbol, conid, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    conid = excluded.conid,
                    updated_at = excluded.updated_at
                """,
                (symbol, conid, now),
            )
            conn.commit()

    def get_conid(self, symbol: str) -> Optional[str]:
        """
        Retrieve the conid for a given symbol.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT conid FROM conids WHERE symbol = ?",
                (symbol,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_last_update(self, symbol: str) -> Optional[datetime]:
        """
        Get last update datetime for a symbol.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT updated_at FROM conids WHERE symbol = ?",
                (symbol,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return datetime.fromisoformat(row[0])

    def is_fresh(self, symbol: str, days: int = 7) -> bool:
        """
        Returns True if the last update is newer than `days` (default: 7).
        """
        last_update = self.get_last_update(symbol)
        if not last_update:
            return False
        return datetime.utcnow() - last_update <= timedelta(days=days)


# Example usage:
storage = PersistentConidStorage()
# storage.store_conid("AAPL", "265598")
# conid = storage.get_conid("AAPL")
# is_recent = storage.is_fresh("AAPL")
