from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.models.review import Review


class ReviewStore:
    """Durable state for reviews discovered through MQTT or reconciliation."""

    def __init__(self, output_directory: Path) -> None:
        self._directory = output_directory
        self._path = output_directory / ".frigate-exporter-reviews.sqlite3"
        self._reconciliation_cutoff = 0.0

    def initialize(self) -> None:
        database_exists = self._path.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'reconciliation_cutoff'"
            ).fetchone()
            if row is None:
                # A brand-new state database alongside existing exports must not
                # re-export the entire historical Frigate review window.
                cutoff = time.time() if not database_exists and any(self._directory.glob("*.mp4")) else 0.0
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('reconciliation_cutoff', ?)",
                    (str(cutoff),),
                )
                self._reconciliation_cutoff = cutoff
            else:
                self._reconciliation_cutoff = float(row[0])
            # Queued items have not started exporting. Requeue them after a
            # restart so a crashed process cannot strand the durable backlog.
            connection.execute(
                "UPDATE reviews SET state = 'pending' WHERE state IN ('processing', 'queued')"
            )
            connection.commit()

    @property
    def reconciliation_cutoff(self) -> float:
        return self._reconciliation_cutoff

    def queue_if_needed(self, review: Review) -> bool:
        now = time.time()
        payload = review.model_dump_json()
        with closing(sqlite3.connect(self._path)) as connection, connection:
            row = connection.execute(
                "SELECT state, next_attempt_at FROM reviews WHERE review_id = ?",
                (review.id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO reviews (review_id, payload, state, updated_at) VALUES (?, ?, 'pending', ?)",
                    (review.id, payload, now),
                )
                connection.commit()
                return True
            state, next_attempt_at = row
            if state in {"completed", "pending", "queued", "processing"}:
                return False
            if state == "failed" and next_attempt_at > now:
                return False
            connection.execute(
                "UPDATE reviews SET payload = ?, state = 'pending', next_attempt_at = 0, updated_at = ? WHERE review_id = ?",
                (payload, now, review.id),
            )
            connection.commit()
            return True

    def mark_queued(self, review_id: str) -> bool:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            result = connection.execute(
                "UPDATE reviews SET state = 'queued', updated_at = ? WHERE review_id = ? AND state IN ('pending', 'failed') AND next_attempt_at <= ?",
                (time.time(), review_id, time.time()),
            )
            connection.commit()
            return result.rowcount == 1

    def claim(self, review_id: str) -> None:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                "UPDATE reviews SET state = 'processing', attempts = attempts + 1, updated_at = ? WHERE review_id = ? AND state = 'queued'",
                (time.time(), review_id),
            )
            connection.commit()

    def complete(self, review_id: str) -> None:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                "UPDATE reviews SET state = 'completed', last_error = NULL, updated_at = ? WHERE review_id = ?",
                (time.time(), review_id),
            )
            connection.commit()

    def fail(self, review_id: str, error: str, retry_after: float = 60) -> None:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                "UPDATE reviews SET state = 'failed', last_error = ?, next_attempt_at = ?, updated_at = ? WHERE review_id = ?",
                (error[:1000], time.time() + retry_after, time.time(), review_id),
            )
            connection.commit()

    def due_reviews(self, limit: int = 100) -> list[Review]:
        now = time.time()
        with closing(sqlite3.connect(self._path)) as connection, connection:
            rows = connection.execute(
                "SELECT payload FROM reviews WHERE state IN ('pending', 'failed') AND next_attempt_at <= ? ORDER BY updated_at LIMIT ?",
                (now, limit),
            ).fetchall()
        return [Review.model_validate_json(payload) for (payload,) in rows]

    def summary(self) -> dict[str, int]:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) FROM reviews GROUP BY state"
            ).fetchall()
        return {state: count for state, count in rows}
