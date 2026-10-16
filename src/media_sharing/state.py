"""SQLite-backed state: idempotency ledger, job status, and rate-limit counters.

The idempotency key is ``(sha256, account, platform)`` so re-running the watcher
never double-posts a file to the same account.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .models import MediaItem, Platform, PostStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    sha256      TEXT NOT NULL,
    account     TEXT NOT NULL,
    platform    TEXT NOT NULL,
    backend     TEXT,
    status      TEXT NOT NULL,
    post_id     TEXT,
    post_url    TEXT,
    detail      TEXT,
    verified    INTEGER,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (sha256, account, platform)
);

CREATE TABLE IF NOT EXISTS rate_limits (
    account     TEXT NOT NULL,
    platform    TEXT NOT NULL,
    day         TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    last_at     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account, platform, day)
);
"""


class StateStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
        if "verified" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN verified INTEGER")

    # -- idempotency -------------------------------------------------------
    def already_published(self, media: MediaItem, account: str, platform: Platform) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT status FROM posts WHERE sha256=? AND account=? AND platform=?",
                (media.sha256, account, platform.value),
            ).fetchone()
        return bool(row and row["status"] == PostStatus.PUBLISHED.value)

    def record(self, media: MediaItem, account, platform, backend, status, *, post_id=None,
               post_url=None, detail=None, verified: bool | None = None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO posts (sha256, account, platform, backend, status, post_id,
                                   post_url, detail, verified, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256, account, platform) DO UPDATE SET
                    backend=excluded.backend, status=excluded.status, post_id=excluded.post_id,
                    post_url=excluded.post_url, detail=excluded.detail,
                    verified=excluded.verified,
                    updated_at=excluded.updated_at
                """,
                (media.sha256, account, platform.value, backend,
                 PostStatus(status).value if not isinstance(status, PostStatus) else status.value,
                 post_id, post_url, detail,
                 None if verified is None else int(bool(verified)), time.time()),
            )
            conn.commit()

    # -- rate limiting -----------------------------------------------------
    def check_rate_limit(self, account: str, platform: Platform, *,
                         daily_cap: int | None, min_interval_seconds: int) -> bool:
        """Return True if a post is allowed now; otherwise False (limit hit).

        Read-only: the counter is only advanced by :meth:`record_post`, so failed
        or repeated attempts never consume the limit.
        """
        day = time.strftime("%Y-%m-%d", time.gmtime())
        now = time.time()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT count, last_at FROM rate_limits WHERE account=? AND platform=? AND day=?",
                (account, platform.value, day),
            ).fetchone()
        count = row["count"] if row else 0
        last_at = row["last_at"] if row else 0
        if daily_cap is not None and count >= daily_cap:
            return False
        return not (last_at and now - last_at < min_interval_seconds)

    def record_post(self, account: str, platform: Platform) -> None:
        """Advance the rate-limit counter after a successful post."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (account, platform, day, count, last_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(account, platform, day) DO UPDATE SET
                    count = count + 1, last_at = excluded.last_at
                """,
                (account, platform.value, day, now),
            )
            conn.commit()
