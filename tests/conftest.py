"""Shared pytest fixtures.

pytest imports this file automatically before any test in the ``tests``
folder runs. Anything defined here with ``@pytest.fixture`` can be used by
any test simply by naming it as a parameter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasheet_extractor.schema import Schema, load_schema

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "sensor.json"


@pytest.fixture
def fixtures_dir() -> Path:
    """Folder holding the sample PDFs and canned LLM replies."""
    return FIXTURES_DIR


@pytest.fixture
def sensor_schema() -> Schema:
    """The real sensor schema, so tests exercise the file the pipeline uses."""
    return load_schema(SCHEMA_PATH)


@pytest.fixture
def good_reply() -> str:
    """A well-formed model reply matching ``sample_sensor.pdf``."""
    return (FIXTURES_DIR / "llm_good.json").read_text(encoding="utf-8")
