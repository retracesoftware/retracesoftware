"""
Datasette server replay regression.

This is the application under Retrace.  It starts a Datasette/Uvicorn server
against a small SQLite database.  The companion client.py generates HTTP load
during record; replay should consume the recorded server-side behavior without
touching a live network.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from datasette.cli import cli


DB_FILE = Path(os.environ.get("DATASETTE_DB", "/tmp/datasette-demo.db"))
HOST = os.environ.get("DATASETTE_HOST", "0.0.0.0")
PORT = os.environ.get("SERVER_PORT", "5000")


def init_db() -> None:
    DB_FILE.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.executescript(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL
            );

            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                event TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO items (name, price, currency) VALUES (?, ?, ?)",
            [
                ("Acme Water", 68.46, "AUD"),
                ("Example Energy", 28.32, "USD"),
                ("Castle Water", 436.55, "GBP"),
            ],
        )
        conn.executemany(
            "INSERT INTO audit_log (event, created_at) VALUES (?, ?)",
            [
                ("created demo database", "2026-05-08T10:00:00"),
                ("loaded invoice rows", "2026-05-08T10:00:01"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Datasette database initialized at {DB_FILE}", flush=True)
    cli(
        args=[
            str(DB_FILE),
            "--host",
            HOST,
            "--port",
            PORT,
            "--setting",
            "default_page_size",
            "5",
        ],
        prog_name="datasette",
    )
