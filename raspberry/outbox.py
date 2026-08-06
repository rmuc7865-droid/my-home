from __future__ import annotations

import sqlite3
from pathlib import Path

from shared.models import MeasurementRecord


class Outbox:
    def __init__(self, path: str):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    record_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def add(self, record: MeasurementRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO outbox(record_id, payload) VALUES (?, ?)",
                (str(record.record_id), record.model_dump_json()),
            )

    def pending(self, limit: int = 500) -> list[MeasurementRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM outbox ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
        return [MeasurementRecord.model_validate_json(row["payload"]) for row in rows]

    def delete(self, record_ids: list[str]) -> None:
        if not record_ids:
            return
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM outbox WHERE record_id = ?",
                [(record_id,) for record_id in record_ids],
            )
