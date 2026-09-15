"""Tests for DocumentLoader.

These use two tiny PDFs committed under ``tests/fixtures`` and built by
``make_fixture.py``, so they run offline and take milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasheet_extractor.loader import Document, DocumentLoader, NoTextError


def test_load_returns_expected_text_fragments(fixtures_dir: Path) -> None:
    doc = DocumentLoader().load(fixtures_dir / "sample_sensor.pdf")

    assert isinstance(doc, Document)
    assert doc.page_count == 2
    # Page indices line up with page numbers: specs are on page two.
    assert "Technical data" in doc.pages[1]
    assert "TN2405" in doc.pages[1]
    assert "Ambient temperature -25...80 C" in doc.pages[1]
    # The joined view contains text from both pages.
    assert "Product description" in doc.text
    assert "Protection IP67" in doc.text
    assert len(doc.sha256) == 64


def test_missing_file_raises_file_not_found(fixtures_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DocumentLoader().load(fixtures_dir / "does_not_exist.pdf")


def test_pdf_without_text_raises_no_text_error(fixtures_dir: Path) -> None:
    with pytest.raises(NoTextError) as excinfo:
        DocumentLoader().load(fixtures_dir / "scanned_no_text.pdf")

    # The message should help a user understand what to do about the file.
    assert "scanned" in str(excinfo.value)
