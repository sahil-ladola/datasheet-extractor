"""Shared pytest fixtures.

pytest imports this file automatically before any test in the ``tests``
folder runs. Anything defined here with ``@pytest.fixture`` can be used by
any test simply by naming it as a parameter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Folder holding the sample PDFs and canned LLM replies."""
    return FIXTURES_DIR
