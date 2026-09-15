"""Load a PDF datasheet into plain text, one string per page.

This is the only module that touches PDF parsing. Everything downstream
works on the ``Document`` it returns and never sees the PDF library.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


class NoTextError(Exception):
    """Raised when a PDF contains no extractable text.

    The usual cause is a scanned datasheet: the pages are images, so there
    is no text layer for pdfplumber to read. OCR is out of scope for this
    project, so callers should record the file as skipped.
    """


@dataclass(frozen=True)
class Document:
    """Plain-text view of one PDF file.

    Attributes:
        path: Where the PDF was read from.
        pages: Extracted text, one entry per page, in page order. A page
            with no text is an empty string so indices still line up with
            page numbers.
        sha256: Hex digest of the file bytes. Used by the store to notice
            when the same PDF is processed twice.
    """

    path: Path
    pages: list[str]
    sha256: str

    @property
    def text(self) -> str:
        """All pages joined into one string, separated by blank lines."""
        return "\n\n".join(self.pages)

    @property
    def page_count(self) -> int:
        """Number of pages in the PDF, including empty ones."""
        return len(self.pages)


class DocumentLoader:
    """PDF path in, ``Document`` out.

    Args:
        min_chars: Smallest total character count that counts as "has text".
            Scanned PDFs often yield a handful of stray characters rather
            than nothing at all, so a small threshold is safer than zero.
    """

    def __init__(self, min_chars: int = 20) -> None:
        self.min_chars = min_chars

    def load(self, path: str | Path) -> Document:
        """Read every page of ``path`` and return its text.

        Raises:
            FileNotFoundError: If ``path`` does not point at a file.
            NoTextError: If the PDF yields fewer than ``min_chars``
                characters in total, which almost always means it is scanned.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"No such file: {path}")

        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

        pages: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append((page.extract_text() or "").strip())

        total_chars = sum(len(p) for p in pages)
        if total_chars < self.min_chars:
            raise NoTextError(
                f"{path.name}: only {total_chars} characters of text across "
                f"{len(pages)} page(s). Is this a scanned image?"
            )

        return Document(path=path, pages=pages, sha256=sha256)
