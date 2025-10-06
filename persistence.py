import sqlite3
import json
import os
from contextlib import contextmanager
from typing import List, Dict

DB_FILE = "arctrigger.sqlite"

# ---------- low-level helpers ----------
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FILE, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL")                # crash-safe
    yield conn
    conn.close()

def init_db():
    with get_conn() as c:
        c.execute("""
          CREATE TABLE IF NOT EXISTS order_ticket (
            id                TEXT PRIMARY KEY,
            symbol            TEXT NOT NULL,
            expiry            TEXT NOT NULL,
            strike            REAL NOT NULL,
            right             TEXT NOT NULL,
            trigger_price     REAL,
            position_size     REAL,
            qty               INTEGER,
            sl_price          REAL,
            tp_price          REAL,
            order_type        TEXT DEFAULT 'LMT',
            action            TEXT DEFAULT 'BUY',
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
          )""")

# ---------- CRUD ----------
def save_ticket(t: Dict) -> None:
    with get_conn() as c:
        c.execute("REPLACE INTO order_ticket VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (t["id"], t["symbol"], t["expiry"], t["strike"],
                   t["right"], t.get("trigger_price"), t.get("position_size"),
                   t.get("qty"), t.get("sl_price"), t.get("tp_price"),
                   t.get("order_type", "LMT"), t.get("action", "BUY"),
                   t.get("created_at"), t.get("updated_at")))

def load_all_tickets() -> List[Dict]:
    with get_conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM order_ticket ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

def delete_ticket(ticket_id: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM order_ticket WHERE id=?", (ticket_id,))