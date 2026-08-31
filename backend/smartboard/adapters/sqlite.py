"""
SQLite adapter.

Opened read-only via URI so a rogue statement cannot write even in principle,
with an interrupt handler enforcing the manifest's statement timeout. The
Postgres adapter alongside this one has the same shape; swapping between them is
a one-line manifest change.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


class SQLiteAdapter:
    dialect = "sqlite"

    def __init__(self, path: str, timeout_ms: int = 5000, read_only: bool = True):
        self.path = str(Path(path).resolve())
        self.timeout_ms = timeout_ms
        self.read_only = read_only

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.path}?mode={'ro' if self.read_only else 'rw'}"
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        # Belt and braces: even a read-only handle gets an explicit authorizer.
        if self.read_only:
            conn.set_authorizer(_deny_writes)
        return conn

    def run(self, sql: str, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
        started = time.perf_counter()
        conn = self._connect()
        try:
            deadline = time.perf_counter() + self.timeout_ms / 1000
            conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 2000)
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        return rows, int((time.perf_counter() - started) * 1000)


_WRITE_OPS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}


def _deny_writes(action, arg1, arg2, db_name, trigger):  # noqa: ANN001
    if action in _WRITE_OPS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
