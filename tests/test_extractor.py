"""Tests for the Extractor and the provider client.

None of these touch the network. The Extractor is handed a FakeLLMClient
with scripted replies, so each test controls exactly what "the model" says
and can assert on how the Extractor reacts.
"""

from __future__ import annotations

import json

import pytest

from datasheet_extractor.extractor import (
    RETRY_NOTE,
    ExtractionError,
    Extractor,
    GeminiClient,
    MissingAPIKeyError,
)
from datasheet_extractor.schema import Schema
from tests.fakes import FakeLLMClient

SAMPLE_TEXT = "Technical data\nOrder number TN2405\nOperating voltage 18...32 V DC"


def test_good_reply_is_parsed_into_schema_fields(
    sensor_schema: Schema, good_reply: str
) -> None:
    client = FakeLLMClient([good_reply])
    extractor = Extractor(client, sensor_schema)

    result = extractor.extract(SAMPLE_TEXT)

    assert client.calls == 1
    assert list(result) == sensor_schema.field_names
    assert result["part_number"] == "TN2405"
    assert result["supply_voltage_min"] == {"value": 18, "unit": "V DC"}
    assert result["operating_temp_min"] == {"value": -25, "unit": "C"}
    # The prompt is built from the schema and carries the datasheet text.
    prompt = client.prompts[0]
    assert "operating_temp_min" in prompt
    assert "Order number TN2405" in prompt


def test_schema_is_enforced_on_the_reply(sensor_schema: Schema, good_reply: str) -> None:
    reply = json.loads(good_reply)
    reply["colour"] = "blue"          # invented key: must be dropped
    del reply["weight"]               # omitted key: must come back as None
    reply["part_number"] = 2405       # wrong type for a string: coerced
    reply["response_time"] = 3000     # bare number: wrapped, unit unknown
    client = FakeLLMClient([json.dumps(reply)])

    result = Extractor(client, sensor_schema).extract(SAMPLE_TEXT)

    assert "colour" not in result
    assert set(result) == set(sensor_schema.field_names)
    assert result["weight"] is None
    assert result["part_number"] == "2405"
    assert result["response_time"] == {"value": 3000, "unit": None}


def test_markdown_fenced_json_is_accepted(sensor_schema: Schema, good_reply: str) -> None:
    client = FakeLLMClient([f"```json\n{good_reply}\n```"])

    result = Extractor(client, sensor_schema).extract(SAMPLE_TEXT)

    assert client.calls == 1
    assert result["ip_rating"] == "IP67"


def test_malformed_json_triggers_a_retry(sensor_schema: Schema, good_reply: str) -> None:
    client = FakeLLMClient(['{"part_number": "TN2405",', good_reply])

    result = Extractor(client, sensor_schema).extract(SAMPLE_TEXT)

    assert client.calls == 2
    assert result["part_number"] == "TN2405"
    # The first prompt is clean; the retry tells the model what went wrong.
    assert RETRY_NOTE not in client.prompts[0]
    assert client.prompts[1].endswith(RETRY_NOTE)


def test_gives_up_after_max_attempts(sensor_schema: Schema) -> None:
    client = FakeLLMClient(["not json", "[1, 2, 3]", "still not json"])

    with pytest.raises(ExtractionError) as excinfo:
        Extractor(client, sensor_schema, max_attempts=2).extract(SAMPLE_TEXT)

    assert client.calls == 2
    assert "2 attempt(s)" in str(excinfo.value)
    assert "not an object" in str(excinfo.value)


def test_gemini_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Remove the variable for this test only; monkeypatch restores it after.
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(MissingAPIKeyError):
        GeminiClient()
