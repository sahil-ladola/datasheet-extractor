"""Tests for the page-selection heuristic.

These work on plain strings rather than PDFs: the function's contract is
pages in, text out, and the loader tests already prove PDFs become pages.
"""

from __future__ import annotations

from datasheet_extractor.selection import select_pages

MARKETING = (
    "Compact transmitter for hygienic applications. Robust housing design,\n"
    "four-digit display and intuitive setup. Ideal for food and beverage."
)
SPECS = (
    "Technical data\n"
    "Operating voltage 18...32 V DC\n"
    "Ambient temperature -25...80 C\n"
    "Output 4...20 mA\n"
    "Protection IP67"
)
DIMENSIONS = "Dimensions\nLength 82 mm\nHousing stainless steel\nWeight 120 g"


def test_spec_pages_are_chosen_in_document_order_under_the_cap() -> None:
    pages = [MARKETING, SPECS, DIMENSIONS]

    # A generous cap keeps every qualifying page, in document order. The
    # marketing page mentions "housing" once, which is below the minimum
    # score, so it is left out.
    result = select_pages(pages, max_chars=10_000)
    assert "Technical data" in result
    assert "Weight 120 g" in result
    assert "hygienic applications" not in result
    assert result.index("--- page 2 ---") < result.index("--- page 3 ---")

    # A tight cap keeps only the best-scoring page, cut exactly at the cap.
    result = select_pages(pages, max_chars=len(SPECS) - 5)
    assert result == f"--- page 2 ---\n{SPECS[:-5]}"
    assert "Weight 120 g" not in result


def test_falls_back_to_first_pages_when_nothing_scores() -> None:
    pages = [f"Page {n} says nothing useful." for n in range(1, 6)]

    result = select_pages(pages, fallback_pages=2)

    assert "Page 1 says" in result
    assert "Page 2 says" in result
    assert "Page 3 says" not in result
    assert select_pages([]) == ""
