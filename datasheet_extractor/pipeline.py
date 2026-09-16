"""Wire loader, page selection, extractor, validator and store together.

Run it as a script over a folder or a list of PDFs::

    python -m datasheet_extractor.pipeline datasheets/

Each file goes through exactly one of these outcomes:

* ``unchanged``: the file's hash is already in the store, nothing is done.
  This is what makes re-running over the same folder free.
* ``skipped``: the PDF has no extractable text (a scanned image). The
  hash is remembered with the reason so it is not retried next time.
* ``failed``: the LLM never produced usable output, or the call itself
  errored. Remembered with the reason, and retried automatically on the
  next run because such failures are usually transient.
* ``stored``: extracted, validated and written. The validation status
  (ok, warning, error) is reported alongside.
* ``deferred``: not attempted, because an earlier file hit the API's
  quota limit. Nothing is recorded, so the next run picks it up.

The pipeline never raises for a single bad file; it records the outcome
and moves on, so one unreadable datasheet cannot stop a batch.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from datasheet_extractor.extractor import (
    ExtractionError,
    Extractor,
    GeminiClient,
    LLMClient,
    MissingAPIKeyError,
)
from datasheet_extractor.loader import DocumentLoader, NoTextError, hash_file
from datasheet_extractor.schema import load_schema
from datasheet_extractor.selection import DEFAULT_MAX_CHARS, select_pages
from datasheet_extractor.store import Record, Store
from datasheet_extractor.validator import Validator

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = Path("schemas") / "sensor.json"
DEFAULT_DB = Path("datasheets.db")


@dataclass(frozen=True)
class FileOutcome:
    """What happened to one PDF."""

    path: Path
    outcome: str
    reason: str | None = None
    record_id: int | None = None
    status: str | None = None
    part_number: str | None = None
    problem_count: int = 0


class Pipeline:
    """Process PDFs end to end.

    Args:
        loader, extractor, validator, store: The four components.
        max_chars: Cap on text sent to the LLM per datasheet.
        delay: Seconds to wait after each LLM call. Free tiers are
            rate-limited per minute, so a few seconds avoids 429 errors.
    """

    def __init__(
        self,
        loader: DocumentLoader,
        extractor: Extractor,
        validator: Validator,
        store: Store,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        delay: float = 0.0,
    ) -> None:
        self.loader = loader
        self.extractor = extractor
        self.validator = validator
        self.store = store
        self.max_chars = max_chars
        self.delay = delay
        self.quota_exhausted = False

    def process_file(self, path: str | Path, *, force: bool = False) -> FileOutcome:
        """Run one PDF through every stage and record the result."""
        path = Path(path)
        sha256 = hash_file(path)

        seen = self.store.file_seen(sha256)
        if seen is not None and seen.outcome != "failed" and not force:
            return FileOutcome(path, "unchanged", reason=f"already {seen.outcome}", record_id=seen.record_id)

        try:
            document = self.loader.load(path)
        except NoTextError as exc:
            self.store.mark_file(sha256, path, "skipped", reason=str(exc))
            return FileOutcome(path, "skipped", reason=str(exc))

        text = select_pages(document.pages, max_chars=self.max_chars)
        logger.info("%s: %d pages, sending %d characters", path.name, document.page_count, len(text))

        try:
            raw = self.extractor.extract(text)
        except ExtractionError as exc:
            self.store.mark_file(sha256, path, "failed", reason=str(exc))
            return FileOutcome(path, "failed", reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - provider errors must not stop the batch
            reason = f"{type(exc).__name__}: {exc}"
            logger.debug("LLM call failed for %s", path, exc_info=True)
            if _is_quota_error(exc):
                self.quota_exhausted = True
                reason = "API quota exhausted (HTTP 429); run again later"
            self.store.mark_file(sha256, path, "failed", reason=reason)
            return FileOutcome(path, "failed", reason=reason)
        finally:
            if self.delay:
                time.sleep(self.delay)

        try:
            result = self.validator.validate(raw)
            record = Record.from_validation(
                result,
                component_type=self.extractor.schema.component_type,
                source_file=path,
                source_hash=sha256,
            )
            record_id, _ = self.store.upsert(record)
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the batch
            reason = f"{type(exc).__name__}: {exc}"
            logger.debug("Validation or storage failed for %s", path, exc_info=True)
            self.store.mark_file(sha256, path, "failed", reason=reason)
            return FileOutcome(path, "failed", reason=reason)

        self.store.mark_file(sha256, path, "stored", record_id=record_id)
        return FileOutcome(
            path,
            "stored",
            record_id=record_id,
            status=result.status,
            part_number=record.part_number,
            problem_count=len(result.problems),
        )

    def process_paths(self, paths: Iterable[str | Path], *, force: bool = False) -> list[FileOutcome]:
        """Process files and folders. Folders are searched for ``*.pdf``.

        Once a quota error has been seen, the remaining files are deferred
        rather than each spending retries on a limit that will not lift
        within the run.
        """
        outcomes: list[FileOutcome] = []
        for pdf in _expand(paths):
            if self.quota_exhausted:
                outcomes.append(FileOutcome(pdf, "deferred", reason="API quota exhausted; run again later"))
            else:
                outcomes.append(self.process_file(pdf, force=force))
        return outcomes


def build_pipeline(
    *,
    schema_path: str | Path = DEFAULT_SCHEMA,
    db_path: str | Path = DEFAULT_DB,
    client: LLMClient | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    delay: float = 0.0,
) -> Pipeline:
    """Assemble a pipeline with default components.

    ``client`` defaults to ``GeminiClient``; tests pass a fake instead.
    """
    schema = load_schema(schema_path)
    return Pipeline(
        DocumentLoader(),
        Extractor(client or GeminiClient(), schema),
        Validator(schema),
        Store(db_path),
        max_chars=max_chars,
        delay=delay,
    )


def load_dotenv(path: str | Path = ".env") -> None:
    """Load ``KEY=VALUE`` lines from ``path`` into the environment.

    Existing variables win, comments and blank lines are ignored, and a
    missing file is not an error. Eight lines of stdlib instead of a
    dependency.
    """
    dotenv = Path(path)
    if not dotenv.is_file():
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _is_quota_error(exc: Exception) -> bool:
    """True for an HTTP 429 from the provider, whatever SDK type wraps it."""
    if getattr(exc, "code", None) == 429:
        return True
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _expand(paths: Iterable[str | Path]) -> list[Path]:
    pdfs: list[Path] = []
    for item in paths:
        p = Path(item)
        if p.is_dir():
            pdfs.extend(sorted(p.glob("*.pdf")))
        else:
            pdfs.append(p)
    return pdfs


def _format_outcome(o: FileOutcome) -> str:
    detail = o.reason or ""
    if o.outcome == "stored":
        detail = f"{o.status:<8} {o.part_number or '?':<18} {o.problem_count} problem(s)"
    return f"{o.path.name:<36} {o.outcome:<10} {detail}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m datasheet_extractor.pipeline",
        description="Extract specifications from PDF datasheets into a SQLite database.",
    )
    parser.add_argument("paths", nargs="+", help="PDF files or folders containing PDFs")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="field schema JSON")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database file")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="text cap per datasheet")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds to pause after each LLM call")
    parser.add_argument("--force", action="store_true", help="reprocess files already in the database")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("google_genai").setLevel(logging.ERROR)  # hides an SDK advisory
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    load_dotenv()

    try:
        pipeline = build_pipeline(
            schema_path=args.schema, db_path=args.db, max_chars=args.max_chars, delay=args.delay
        )
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with pipeline.store:
        outcomes = pipeline.process_paths(args.paths, force=args.force)

    for outcome in outcomes:
        print(_format_outcome(outcome))
    kinds = ("stored", "skipped", "failed", "deferred", "unchanged")
    counts = {k: sum(1 for o in outcomes if o.outcome == k) for k in kinds}
    print(f"\n{len(outcomes)} file(s): " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print(f"database: {args.db}")
    if counts["deferred"]:
        print("API quota exhausted. Failed and deferred files are retried on the next run.")
    return 1 if counts["failed"] or counts["deferred"] else 0


if __name__ == "__main__":
    sys.exit(main())
