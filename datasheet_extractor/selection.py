"""Pick the pages of a datasheet most likely to hold specifications.

Datasheets often run to ten or more pages, and most of that is marketing
copy, dimensional drawings and ordering tables. Sending the whole document
to the LLM is slow, costs tokens, and on a free tier burns through the rate
limit. This module trims the input before the extractor sees it.

The heuristic is deliberately simple:

1. Score each page by how many distinct specification keywords it contains.
2. Keep pages scoring at least ``min_score``. A single generic word such
   as "housing" in a marketing paragraph is not enough on its own.
3. Rank kept pages by score, highest first, ties broken by page order.
4. Take pages in rank order until the character cap is reached. The page
   that crosses the cap is cut off at the cap so something is always sent.
5. If no page qualified, fall back to the first ``fallback_pages`` pages.
6. Emit the chosen pages in document order, each under a page marker.

Known failure mode: the heuristic only sees words. A datasheet whose
specifications are a scanned table image, or one that uses unusual wording
("characteristics" instead of "technical data"), scores zero and falls back
to the first pages, which are usually the marketing ones. The extractor
will then report most fields as missing. Extend ``DEFAULT_KEYWORDS`` when
a family of datasheets misses consistently.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_KEYWORDS: tuple[str, ...] = (
    "technical data",
    "technical specifications",
    "specifications",
    "electrical data",
    "operating voltage",
    "supply voltage",
    "operating temperature",
    "ambient temperature",
    "measuring range",
    "output",
    "protection",
    "response time",
    "weight",
    "housing",
)

DEFAULT_MIN_SCORE = 2
DEFAULT_MAX_CHARS = 12_000
DEFAULT_FALLBACK_PAGES = 3


def score_page(text: str, keywords: Sequence[str] = DEFAULT_KEYWORDS) -> int:
    """Return how many distinct ``keywords`` appear in ``text``.

    Matching is case-insensitive. Distinct keywords are counted rather than
    total occurrences so a page that repeats one word forty times does not
    outrank a real specification table.
    """
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword.lower() in lowered)


def select_pages(
    pages: Sequence[str],
    *,
    keywords: Sequence[str] = DEFAULT_KEYWORDS,
    min_score: int = DEFAULT_MIN_SCORE,
    max_chars: int = DEFAULT_MAX_CHARS,
    fallback_pages: int = DEFAULT_FALLBACK_PAGES,
) -> str:
    """Return the text of the pages most likely to hold specifications.

    Args:
        pages: Page texts in document order, as produced by the loader.
        keywords: Phrases that mark a specification page.
        min_score: Distinct keywords a page needs before it qualifies.
        max_chars: Cap on the total page text returned. Page markers are
            not counted against it.
        fallback_pages: How many leading pages to use when no page qualifies.

    Returns:
        The selected pages in document order, each preceded by a
        ``--- page N ---`` marker (1-based). Empty when ``pages`` is empty.
    """
    if not pages:
        return ""

    scored = [(score_page(text, keywords), index) for index, text in enumerate(pages)]
    ranked = [
        index
        for score, index in sorted(scored, key=lambda s: (-s[0], s[1]))
        if score >= min_score
    ]
    if not ranked:
        ranked = list(range(min(fallback_pages, len(pages))))

    chosen: dict[int, str] = {}
    budget = max_chars
    for index in ranked:
        text = pages[index]
        if not text:
            continue
        if len(text) > budget:
            text = text[:budget]
        chosen[index] = text
        budget -= len(text)
        if budget <= 0:
            break

    return "\n\n".join(
        f"--- page {index + 1} ---\n{chosen[index]}" for index in sorted(chosen)
    )
