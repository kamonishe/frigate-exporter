from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reviews_state_updated "
                "ON reviews (state, updated_at)"
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
            if state in {"completed", "unavailable", "pending", "queued", "processing"}:
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

    def fail(
        self,
        review_id: str,
        error: str,
        retry_after: float = 60,
        *,
        max_attempts: int | None = None,
        expire_before: float | None = None,
    ) -> bool:
        """Record a failed attempt and return whether it became terminal.

        A review is made unavailable when it has exceeded the configured
        attempt limit or its event has left the configured retention window.
        """
        now = time.time()
        with closing(sqlite3.connect(self._path)) as connection, connection:
            row = connection.execute(
                "SELECT attempts, payload FROM reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if row is None:
                return False

            attempts, payload = row
            terminal_reasons: list[str] = []
            if max_attempts is not None and attempts >= max_attempts:
                terminal_reasons.append(
                    f"maximum attempts reached ({max_attempts})"
                )

            if expire_before is not None:
                try:
                    event_time = Review.model_validate_json(payload).end_time
                except ValueError:
                    event_time = None
                if event_time is not None and event_time < expire_before:
                    terminal_reasons.append("review is outside retention window")

            terminal = bool(terminal_reasons)
            stored_error = (
                f"{error} ({'; '.join(terminal_reasons)})"
                if terminal
                else error
            )[:1000]
            connection.execute(
                "UPDATE reviews SET state = ?, last_error = ?, next_attempt_at = ?, updated_at = ? WHERE review_id = ?",
                (
                    "unavailable" if terminal else "failed",
                    stored_error,
                    0 if terminal else now + retry_after,
                    now,
                    review_id,
                ),
            )
            connection.commit()
        return terminal

    def mark_unavailable(self, review_id: str, error: str) -> None:
        """Permanently stop retrying a review with no source recording."""
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                "UPDATE reviews SET state = 'unavailable', last_error = ?, next_attempt_at = 0, updated_at = ? WHERE review_id = ?",
                (error[:1000], time.time(), review_id),
            )
            connection.commit()

    def queue_status(self) -> dict[str, object]:
        """Return durable backlog size and the oldest active review."""
        with closing(sqlite3.connect(self._path)) as connection, connection:
            count_row = connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE state NOT IN ('completed', 'unavailable')"
            ).fetchone()
            oldest = connection.execute(
                "SELECT review_id, payload, state, updated_at "
                "FROM reviews WHERE state NOT IN ('completed', 'unavailable') "
                "ORDER BY updated_at LIMIT 1"
            ).fetchone()
        pending = int(count_row[0]) if count_row else 0
        result: dict[str, object] = {"pending": pending, "oldest": None}
        if oldest is None:
            return result
        review_id, payload, state, updated_at = oldest
        try:
            review = Review.model_validate_json(payload)
            camera = review.camera
        except ValueError:
            camera = None
        result["oldest"] = {
            "review_id": review_id,
            "camera": camera,
            "state": state,
            "queued_at": datetime.fromtimestamp(updated_at, UTC).isoformat(),
            "age_seconds": max(0.0, time.time() - updated_at),
        }
        return result

    def retryable_count(self) -> int:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE state NOT IN ('completed', 'unavailable')"
            ).fetchone()
        return int(row[0]) if row else 0

    def unavailable_count(self) -> int:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE state = 'unavailable'"
            ).fetchone()
        return int(row[0]) if row else 0

    def purge_unavailable(self, before: float) -> int:
        """Forget unavailable reviews whose event has left the retention window."""
        with closing(sqlite3.connect(self._path)) as connection, connection:
            rows = connection.execute(
                "SELECT review_id, payload, updated_at FROM reviews WHERE state = 'unavailable'"
            ).fetchall()
            expired: list[str] = []
            for review_id, payload, updated_at in rows:
                try:
                    event_time = Review.model_validate_json(payload).end_time
                except ValueError:
                    event_time = updated_at
                if event_time < before:
                    expired.append(review_id)
            if expired:
                connection.executemany(
                    "DELETE FROM reviews WHERE review_id = ?",
                    ((review_id,) for review_id in expired),
                )
            connection.commit()
        return len(expired)

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
