"""SQLite-backed verification history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional

from .verifier import VerificationResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_path TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    archive_size INTEGER NOT NULL,
    started_utc TEXT NOT NULL,
    finished_utc TEXT NOT NULL,
    ok INTEGER NOT NULL,
    restore_verified INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    errors_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verifications_archive
    ON verifications(archive_path, finished_utc);
"""


class VerificationHistory:
    """Append-only log of verification runs, keyed by archive path."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- Writes ----------------------------------------------------------

    def record(self, result: VerificationResult) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO verifications (
                    archive_path, archive_sha256, archive_size,
                    started_utc, finished_utc, ok, restore_verified,
                    summary_json, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.archive_path,
                    result.archive_sha256,
                    result.archive_size,
                    result.started_utc,
                    result.finished_utc,
                    1 if result.ok else 0,
                    1 if result.restore_verified else 0,
                    json.dumps(result.summary),
                    json.dumps(result.errors),
                ),
            )
            return int(cur.lastrowid)

    # ---- Reads -----------------------------------------------------------

    def latest_for(self, archive_path: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM verifications
                WHERE archive_path = ?
                ORDER BY finished_utc DESC LIMIT 1
                """,
                (archive_path,),
            ).fetchone()
            return dict(row) if row else None

    def list_all(self, limit: int = 100) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM verifications ORDER BY finished_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM verifications"
            ).fetchone()["c"]
            passed = conn.execute(
                "SELECT COUNT(*) AS c FROM verifications WHERE ok = 1"
            ).fetchone()["c"]
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT archive_path) AS c FROM verifications"
            ).fetchone()["c"]
        return {
            "total_runs": int(total),
            "passed": int(passed),
            "failed": int(total - passed),
            "distinct_archives": int(distinct),
        }
