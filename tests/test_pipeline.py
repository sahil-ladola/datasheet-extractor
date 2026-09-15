"""End-to-end tests for the pipeline, with the LLM faked.

These run the real loader, page selection, validator and store over the
fixture PDFs. Only the model reply is scripted, so the tests prove the
components fit together without needing a key or the network.
"""

from __future__ import annotations

from pathlib import Path

from datasheet_extractor.extractor import LLMClient
from datasheet_extractor.loader import hash_file
from datasheet_extractor.pipeline import build_pipeline
from tests.conftest import SCHEMA_PATH
from tests.fakes import FakeLLMClient
from tests.fixtures.make_fixture import build_pdf


class QuotaExhaustedClient(LLMClient):
    """Stands in for a provider whose daily free quota is used up."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for free tier")


def test_folder_run_stores_readable_pdfs_and_skips_scanned_ones(
    tmp_path: Path, fixtures_dir: Path, good_reply: str
) -> None:
    client = FakeLLMClient([good_reply])
    pipeline = build_pipeline(schema_path=SCHEMA_PATH, db_path=tmp_path / "db.sqlite", client=client)

    with pipeline.store:
        outcomes = {o.path.name: o for o in pipeline.process_paths([fixtures_dir])}

        stored = outcomes["sample_sensor.pdf"]
        assert stored.outcome == "stored"
        assert stored.status == "ok"
        assert stored.part_number == "TN2405"
        assert pipeline.store.get(stored.record_id).data["operating_temp_min"] == -25

        skipped = outcomes["scanned_no_text.pdf"]
        assert skipped.outcome == "skipped"
        assert "scanned" in skipped.reason
        assert pipeline.store.file_seen(hash_file(skipped.path)).outcome == "skipped"

    assert client.calls == 1  # the scanned PDF never reached the model


def test_second_run_over_the_same_files_makes_no_llm_calls(
    tmp_path: Path, fixtures_dir: Path, good_reply: str
) -> None:
    client = FakeLLMClient([good_reply])  # one reply: a second call would fail loudly
    pipeline = build_pipeline(schema_path=SCHEMA_PATH, db_path=tmp_path / "db.sqlite", client=client)

    with pipeline.store:
        pipeline.process_paths([fixtures_dir])
        again = pipeline.process_paths([fixtures_dir])

        assert {o.outcome for o in again} == {"unchanged"}
        assert client.calls == 1
        assert pipeline.store.count() == 1


def test_failed_files_are_retried_on_the_next_run(
    tmp_path: Path, fixtures_dir: Path, good_reply: str
) -> None:
    # Three bad replies exhaust the extractor's attempts; the fourth is good.
    client = FakeLLMClient(["nope", "nope", "nope", good_reply])
    pipeline = build_pipeline(schema_path=SCHEMA_PATH, db_path=tmp_path / "db.sqlite", client=client)
    pdf = fixtures_dir / "sample_sensor.pdf"

    with pipeline.store:
        first = pipeline.process_paths([pdf])[0]
        second = pipeline.process_paths([pdf])[0]

    assert first.outcome == "failed"
    assert "3 attempt(s)" in first.reason
    assert second.outcome == "stored"
    assert client.calls == 4


def test_quota_exhaustion_defers_the_remaining_files(tmp_path: Path, fixtures_dir: Path) -> None:
    client = QuotaExhaustedClient()
    pipeline = build_pipeline(schema_path=SCHEMA_PATH, db_path=tmp_path / "db.sqlite", client=client)
    first_pdf = fixtures_dir / "sample_sensor.pdf"
    second_pdf = tmp_path / "another.pdf"
    second_pdf.write_bytes(build_pdf([["Technical data", "Operating voltage 10...30 V DC"]]))

    with pipeline.store:
        outcomes = pipeline.process_paths([first_pdf, second_pdf])
        assert [o.outcome for o in outcomes] == ["failed", "deferred"]
        assert "quota" in outcomes[0].reason
        # The deferred file was never touched, so the next run will try it.
        assert pipeline.store.file_seen(hash_file(second_pdf)) is None

    assert client.calls == 1
