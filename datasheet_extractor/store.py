"""Persist validated records in SQLite using only the standard library.

Two tables:

* ``records``: one row per component. Fixed columns hold what every
  record has (manufacturer, part number, status, where it came from); the
  full validated field dict and the problem list are stored as JSON text.
  SQLite's built-in ``json_extract`` makes the JSON queryable, so adding a
  field to the schema needs no migration.
* ``files``: one row per PDF the pipeline has looked at, keyed by the
  file's SHA-256. This is how a datasheet already processed, or already
  skipped as unreadable, is recognised on the next run without spending
  another LLM call.

Duplicate handling: a record is identified by manufacturer plus part
number. Storing a record with the same identity again replaces the old
one and keeps its id, so a revised datasheet updates rather than
duplicates. A record whose part number could not be extracted is
identified by its file hash instead.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasheet_extractor.validator import Problem, ValidationResult

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS records (
    id             INTEGER PRIMARY KEY,
    component_type TEXT NOT NULL,
    manufacturer   TEXT,
    part_number    TEXT,
    source_file    TEXT NOT NULL,
    source_hash    TEXT NOT NULL,
    status         TEXT NOT NULL,
    data           TEXT NOT NULL,
    problems       TEXT NOT NULL,
    extracted_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_records_identity ON records (manufacturer, part_number);

CREATE TABLE IF NOT EXISTS files (
    source_hash  TEXT PRIMARY KEY,
    source_file  TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    reason       TEXT,
    record_id    INTEGER,
    processed_at TEXT NOT NULL
);
"""

# Comparison operators allowed in Store.find. Whitelisted so a caller can
# never smuggle SQL into the query.
_OPERATORS = frozenset({"=", "!=", "<", "<=", ">", ">="})


@dataclass(frozen=True)
class Record:
    """One stored component."""

    component_type: str
    manufacturer: str | None
    part_number: str | None
    source_file: str
    source_hash: str
    status: str
    data: dict[str, Any]
    problems: list[Problem] = field(default_factory=list)
    extracted_at: str = ""
    id: int | None = None

    @classmethod
    def from_validation(
        cls,
        result: ValidationResult,
        *,
        component_type: str,
        source_file: str | Path,
        source_hash: str,
    ) -> Record:
        """Build a record from a validator result plus its provenance."""
        return cls(
            component_type=component_type,
            manufacturer=result.data.get("manufacturer"),
            part_number=result.data.get("part_number"),
            source_file=str(source_file),
            source_hash=source_hash,
            status=result.status,
            data=dict(result.data),
            problems=list(result.problems),
        )


@dataclass(frozen=True)
class FileEntry:
    """What happened the last time a given PDF was processed."""

    source_hash: str
    source_file: str
    outcome: str
    reason: str | None
    record_id: int | None
    processed_at: str


class Store:
    """SQLite-backed storage for records and processed files.

    Args:
        path: Database file. Created if missing. ``":memory:"`` works too.
    """

    def __init__(self, path: str | Path = "datasheets.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- records -----------------------------------------------------------

    def upsert(self, record: Record) -> tuple[int, bool]:
        """Insert ``record`` or replace the one with the same identity.

        Returns:
            ``(id, created)`` where ``created`` is False when an existing
            row was updated.
        """
        existing = self._find_identity(record)
        columns = {
            "component_type": record.component_type,
            "manufacturer": record.manufacturer,
            "part_number": record.part_number,
            "source_file": record.source_file,
            "source_hash": record.source_hash,
            "status": record.status,
            "data": json.dumps(record.data),
            "problems": json.dumps([vars(p) for p in record.problems]),
            "extracted_at": record.extracted_at or _now(),
        }
        with self._conn:
            if existing is None:
                placeholders = ", ".join("?" for _ in columns)
                cursor = self._conn.execute(
                    f"INSERT INTO records ({', '.join(columns)}) VALUES ({placeholders})",
                    tuple(columns.values()),
                )
                return int(cursor.lastrowid), True
            assignments = ", ".join(f"{name} = ?" for name in columns)
            self._conn.execute(
                f"UPDATE records SET {assignments} WHERE id = ?",
                (*columns.values(), existing),
            )
            return existing, False

    def get(self, record_id: int) -> Record | None:
        row = self._conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return _row_to_record(row) if row else None

    def all_records(self) -> list[Record]:
        """Every record, ordered by manufacturer then part number."""
        rows = self._conn.execute(
            "SELECT * FROM records ORDER BY manufacturer, part_number, id"
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def find(self, field_name: str, operator: str, value: Any) -> list[Record]:
        """Records whose validated ``field_name`` satisfies ``operator value``.

        Example: ``store.find("operating_temp_min", "<", -20)``. The value
        is compared with SQLite's JSON functions, so numeric fields compare
        as numbers. Records where the field is null never match.

        Raises:
            ValueError: For an operator outside the whitelist or a field
                name that is not a plain identifier.
        """
        if operator not in _OPERATORS:
            raise ValueError(f"Unsupported operator {operator!r}")
        if not field_name.isidentifier():
            raise ValueError(f"Bad field name {field_name!r}")
        json_path = f"$.{field_name}"
        rows = self._conn.execute(
            "SELECT * FROM records "
            f"WHERE json_extract(data, ?) IS NOT NULL AND json_extract(data, ?) {operator} ? "
            "ORDER BY manufacturer, part_number, id",
            (json_path, json_path, value),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    # -- files -------------------------------------------------------------

    def file_seen(self, source_hash: str) -> FileEntry | None:
        """Return what happened to this file before, or ``None`` if new."""
        row = self._conn.execute(
            "SELECT * FROM files WHERE source_hash = ?", (source_hash,)
        ).fetchone()
        return FileEntry(**dict(row)) if row else None

    def mark_file(
        self,
        source_hash: str,
        source_file: str | Path,
        outcome: str,
        *,
        reason: str | None = None,
        record_id: int | None = None,
    ) -> None:
        """Record the outcome for a file: ``stored``, ``skipped`` or ``failed``."""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO files "
                "(source_hash, source_file, outcome, reason, record_id, processed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_hash, str(source_file), outcome, reason, record_id, _now()),
            )

    def all_files(self) -> list[FileEntry]:
        rows = self._conn.execute("SELECT * FROM files ORDER BY processed_at, source_file").fetchall()
        return [FileEntry(**dict(r)) for r in rows]

    # -- internals ---------------------------------------------------------

    def _find_identity(self, record: Record) -> int | None:
        """Id of the row this record would replace, if any."""
        if record.part_number:
            row = self._conn.execute(
                "SELECT id FROM records WHERE manufacturer IS ? AND part_number = ?",
                (record.manufacturer, record.part_number),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id FROM records WHERE source_hash = ?", (record.source_hash,)
            ).fetchone()
        return int(row["id"]) if row else None


def _row_to_record(row: sqlite3.Row) -> Record:
    return Record(
        id=int(row["id"]),
        component_type=row["component_type"],
        manufacturer=row["manufacturer"],
        part_number=row["part_number"],
        source_file=row["source_file"],
        source_hash=row["source_hash"],
        status=row["status"],
        data=json.loads(row["data"]),
        problems=[Problem(**p) for p in json.loads(row["problems"])],
        extracted_at=row["extracted_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
