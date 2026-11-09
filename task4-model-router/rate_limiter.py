import hashlib
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Optional, Tuple


class SQLiteRateLimiter:
    def __init__(
        self,
        database_path: str,
        limit: int = 50_000,
        window_seconds: int = 60,
    ) -> None:
        self.database_path = str(database_path)
        self.limit = limit
        self.window_seconds = window_seconds
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY,
                    tenant_hash TEXT NOT NULL,
                    tokens INTEGER NOT NULL CHECK (tokens > 0),
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS token_usage_tenant_time "
                "ON token_usage (tenant_hash, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def consume(
        self,
        tenant_key: str,
        tokens: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int]:
        if tokens < 1:
            raise ValueError("Token count must be positive")

        current_time = time.time() if now is None else now
        cutoff = current_time - self.window_seconds
        tenant_hash = hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM token_usage WHERE created_at <= ?",
                    (cutoff,),
                )
                row = connection.execute(
                    "SELECT COALESCE(SUM(tokens), 0) FROM token_usage "
                    "WHERE tenant_hash = ? AND created_at > ?",
                    (tenant_hash, cutoff),
                ).fetchone()
                used = int(row[0])

                if used + tokens > self.limit:
                    connection.execute("COMMIT")
                    return False, used

                connection.execute(
                    "INSERT INTO token_usage (tenant_hash, tokens, created_at) "
                    "VALUES (?, ?, ?)",
                    (tenant_hash, tokens, current_time),
                )
                connection.execute("COMMIT")
                return True, used + tokens
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def total_used(self, tenant_key: str, now: Optional[float] = None) -> int:
        current_time = time.time() if now is None else now
        tenant_hash = hashlib.sha256(tenant_key.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM token_usage "
                "WHERE tenant_hash = ? AND created_at > ?",
                (tenant_hash, current_time - self.window_seconds),
            ).fetchone()
        return int(row[0])
