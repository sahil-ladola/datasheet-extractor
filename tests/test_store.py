"""Tests for the SQLite Store.

Every test gets its own database file from pytest's ``tmp_path`` fixture,
a fresh temporary directory per test that pytest deletes later. Tests
therefore never share state, and nothing is written into the repo.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from datasheet_extractor.schema import Schema
from datasheet_extractor.store import Record, Store
from datasheet_extractor.validator import Validator


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "test.db") as s:
        yield s


def make_record(sensor_schema: Schema, good_reply: str, **overrides: Any) -> Record:
    """A validated record for the sample sensor, with optional field changes.

    Each distinct part number gets its own file hash, as distinct PDFs would.
    """
    raw = json.loads(good_reply)
    for key, value in overrides.items():
        raw[key] = value
    result = Validator(sensor_schema).validate(raw)
    return Record.from_validation(
        result,
        component_type=sensor_schema.component_type,
        source_file=f"datasheets/{raw['part_number']}.pdf",
        source_hash=hashlib.sha256(raw["part_number"].encode()).hexdigest(),
    )


def test_write_then_read_round_trip(store: Store, sensor_schema, good_reply) -> None:
    record = make_record(sensor_schema, good_reply, operating_temp_max={"value": 250, "unit": "C"})

    record_id, created = store.upsert(record)
    loaded = store.get(record_id)

    assert created is True
    assert loaded is not None
    assert loaded.part_number == "TN2405"
    assert loaded.manufacturer == "ifm electronic gmbh"
    assert loaded.data == record.data          # JSON round trip keeps types
    assert loaded.data["operating_temp_min"] == -25
    assert loaded.status == "warning"
    assert [p.code for p in loaded.problems] == ["out_of_range"]
    assert loaded.extracted_at                  # filled in by the store
    assert store.count() == 1
    assert store.all_records() == [loaded]


def test_same_part_number_updates_instead_of_duplicating(store, sensor_schema, good_reply) -> None:
    first = make_record(sensor_schema, good_reply)
    first_id, _ = store.upsert(first)

    # A revised datasheet for the same part: different file, new weight.
    revised = Record(
        **{**vars(first), "source_hash": "b" * 64, "data": {**first.data, "weight": 125}}
    )
    second_id, created = store.upsert(revised)

    assert created is False
    assert second_id == first_id
    assert store.count() == 1
    assert store.get(first_id).data["weight"] == 125
    assert store.get(first_id).source_hash == "b" * 64

    # The same file reprocessed with a differently worded part number must
    # update that file's row, not create a second one.
    reworded = Record(**{**vars(revised), "part_number": "Omnigrad TN2405"})
    assert store.upsert(reworded) == (first_id, False)
    assert store.count() == 1

    # Files are remembered by hash so the pipeline can skip them next run.
    store.mark_file("c" * 64, "datasheets/scan.pdf", "skipped", reason="no text")
    assert store.file_seen("c" * 64).outcome == "skipped"
    assert store.file_seen("d" * 64) is None


def test_find_filters_on_a_validated_field(store, sensor_schema, good_reply) -> None:
    for part, t_min in (("COLD", -40), ("MILD", -25), ("WARM", 0)):
        store.upsert(
            make_record(
                sensor_schema, good_reply,
                part_number=part, operating_temp_min={"value": t_min, "unit": "C"},
            )
        )

    below_minus_20 = store.find("operating_temp_min", "<", -20)

    assert [r.part_number for r in below_minus_20] == ["COLD", "MILD"]
    assert [r.part_number for r in store.find("part_number", "=", "WARM")] == ["WARM"]
    with pytest.raises(ValueError):
        store.find("operating_temp_min", "LIKE", "%")
    with pytest.raises(ValueError):
        store.find("data); DROP TABLE records; --", "=", 1)
