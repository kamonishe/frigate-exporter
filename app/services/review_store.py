from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.models.review import Review


class ReviewStore:
    """Durable state for reviews discovered through MQTT or reconciliation."""

    def __init__(self, output_directory: Path) -> None:
        self._path = output_directory / ".frigate-exporter-reviews.sqlite3"

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
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
                "UPDATE reviews SET state = 'pending' WHERE state = 'processing'"
            )
            connection.commit()

    def queue_if_needed(self, review: Review) -> bool:
        now = time.time()
        payload = review.model_dump_json()
        with sqlite3.connect(self._path) as connection:
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
        with sqlite3.connect(self._path) as connection:
            result = connection.execute(
                "UPDATE reviews SET state = 'queued', updated_at = ? WHERE review_id = ? AND state IN ('pending', 'failed') AND next_attempt_at <= ?",
                (time.time(), review_id, time.time()),
            )
            connection.commit()
            return result.rowcount == 1

    def claim(self, review_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE reviews SET state = 'processing', attempts = attempts + 1, updated_at = ? WHERE review_id = ? AND state = 'queued'",
                (time.time(), review_id),
            )
            connection.commit()

    def complete(self, review_id: str) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE reviews SET state = 'completed', last_error = NULL, updated_at = ? WHERE review_id = ?",
                (time.time(), review_id),
            )
            connection.commit()

    def fail(self, review_id: str, error: str, retry_after: float = 60) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "UPDATE reviews SET state = 'failed', last_error = ?, next_attempt_at = ?, updated_at = ? WHERE review_id = ?",
                (error[:1000], time.time() + retry_after, time.time(), review_id),
            )
            connection.commit()

    def due_reviews(self, limit: int = 100) -> list[Review]:
        now = time.time()
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT payload FROM reviews WHERE state IN ('pending', 'failed') AND next_attempt_at <= ? ORDER BY updated_at LIMIT ?",
                (now, limit),
            ).fetchall()
        return [Review.model_validate_json(payload) for (payload,) in rows]

    def summary(self) -> dict[str, int]:
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) FROM reviews GROUP BY state"
            ).fetchall()
        return {state: count for state, count in rows}
