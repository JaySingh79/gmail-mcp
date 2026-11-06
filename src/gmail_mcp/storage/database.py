"""SQLite connection manager and non-destructive schema migration runner."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from gmail_mcp.storage.migrations import v001_initial


class DatabaseManager:
    """Thread-safe SQLite database connection and transaction manager with WAL mode."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        self._is_memory = str(db_path) == ":memory:"
        self._lock = threading.RLock()
        self._persistent_conn: sqlite3.Connection | None = None
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_migrations()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            ":memory:" if self._is_memory else str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Acquire a transactional connection that commits on exit or rolls back on exception."""
        with self._lock:
            if self._is_memory:
                if self._persistent_conn is None:
                    self._persistent_conn = self._create_connection()
                conn = self._persistent_conn
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            else:
                conn = self._create_connection()
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    def initialize_migrations(self) -> list[int]:
        """Apply any pending migrations non-destructively without dropping existing tables."""
        applied_versions: list[int] = []
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                """
            )
            rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
            existing = {int(r["version"]) for r in rows}

            migrations = [
                (v001_initial.MIGRATION_VERSION, v001_initial.MIGRATION_NAME, v001_initial.UP_SQL),
            ]
            now_iso = datetime.now(timezone.utc).isoformat()
            for version, name, sql in migrations:
                if version not in existing:
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, now_iso),
                    )
                    applied_versions.append(version)
        return applied_versions

    def check_health(self) -> dict[str, object]:
        """Verify SQLite connectivity and schema migration state."""
        with self.transaction() as conn:
            rows = conn.execute("SELECT version, name, applied_at FROM schema_migrations").fetchall()
            return {
                "healthy": True,
                "db_path": str(self.db_path),
                "migrations_applied": [dict(r) for r in rows],
            }

    def close(self) -> None:
        with self._lock:
            if self._persistent_conn is not None:
                self._persistent_conn.close()
                self._persistent_conn = None
