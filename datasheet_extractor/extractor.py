"""Turn datasheet text into a dict of raw field values using an LLM.

This is the only module that talks to a language model, and it does so
through the ``LLMClient`` interface: one method, prompt in, text out. The
``Extractor`` owns everything provider-independent (building the prompt
from the schema, parsing the reply, enforcing the schema's keys and
retrying on garbage), so swapping providers means writing one small
``LLMClient`` subclass and nothing else changes.

The extractor does not judge values. It returns exactly the keys the schema
defines, with ``None`` where the model found nothing; deciding whether a
value is missing, malformed or implausible is the validator's job.
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Any

from datasheet_extractor.schema import FieldSpec, Schema

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

RETRY_NOTE = (
    "\n\nYour previous reply was not a valid JSON object. "
    "Reply with a single JSON object and nothing else."
)


class ExtractionError(Exception):
    """Raised when the model never produced a usable reply."""


class MissingAPIKeyError(Exception):
    """Raised when a provider client is created without a key available."""


class LLMClient(ABC):
    """Minimal interface a language-model provider must implement."""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Send ``prompt`` and return the model's reply as plain text."""


class GeminiClient(LLMClient):
    """``LLMClient`` backed by Google Gemini through the google-genai SDK.

    Args:
        api_key: Explicit key. Defaults to the ``GOOGLE_API_KEY``
            environment variable, which is the only place it should live.
        model: Model name. Defaults to ``GEMINI_MODEL`` from the environment,
            then to ``DEFAULT_GEMINI_MODEL``.

    Raises:
        MissingAPIKeyError: If no key is given and the variable is unset.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise MissingAPIKeyError(
                "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        # Imported here so the rest of the package, and the test suite, never
        # need the provider SDK to be importable.
        from google import genai
        from google.genai import types

        # Free-tier traffic regularly sees 429 (rate limit) and 503 (high
        # demand). Both are transient, so let the SDK retry with backoff.
        retry = types.HttpRetryOptions(
            attempts=5,
            initial_delay=2.0,
            max_delay=30.0,
            http_status_codes=[429, 500, 502, 503, 504],
        )
        self._client = genai.Client(
            api_key=key, http_options=types.HttpOptions(retry_options=retry)
        )

    def complete(self, prompt: str) -> str:
        """Call Gemini in JSON mode at temperature zero and return its text."""
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        return response.text or ""


class Extractor:
    """Text plus a schema in, dict of raw values out.

    Args:
        client: Any ``LLMClient``. Tests pass a fake; the pipeline passes
            a ``GeminiClient``.
        schema: Fields to extract.
        max_attempts: How many replies to accept before giving up. A reply
            that is not a JSON object counts as a failed attempt and the
            next attempt tells the model so.
    """

    def __init__(self, client: LLMClient, schema: Schema, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.schema = schema
        self.max_attempts = max_attempts

    def build_prompt(self, text: str) -> str:
        """Render the schema as instructions followed by the datasheet text."""
        lines = [
            f"You are extracting specifications from an industrial {self.schema.component_type} "
            "datasheet. Return a single JSON object with exactly the keys listed below. "
            "Use null for any value the datasheet does not state. Do not guess or infer.",
            "",
            "Keys:",
        ]
        for field in self.schema.fields:
            lines.append(f"- {field.name}: {_describe(field)}")
        lines += ["", "Datasheet text:", '"""', text, '"""']
        return "\n".join(lines)

    def extract(self, text: str) -> dict[str, Any]:
        """Ask the model for the schema's fields and return them as a dict.

        Every schema field is present in the result. Number fields are
        ``{"value": float | int, "unit": str | None}`` or ``None``; string
        and enum fields are ``str`` or ``None``. Keys the model invented are
        dropped.

        Raises:
            ExtractionError: If no attempt yields a JSON object.
        """
        prompt = self.build_prompt(text)
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            reply = self.client.complete(prompt)
            try:
                data = _parse_reply(reply)
            except ValueError as exc:
                last_error = exc
                logger.warning("Attempt %d/%d: %s", attempt, self.max_attempts, exc)
                prompt = self.build_prompt(text) + RETRY_NOTE
                continue
            return self._enforce_schema(data)
        raise ExtractionError(
            f"No usable reply after {self.max_attempts} attempt(s): {last_error}"
        )

    def _enforce_schema(self, data: dict[str, Any]) -> dict[str, Any]:
        """Keep only schema keys, fill gaps with None, normalise shapes."""
        extra = sorted(set(data) - set(self.schema.field_names))
        if extra:
            logger.debug("Dropping keys not in schema: %s", extra)
        return {field.name: _coerce(field, data.get(field.name)) for field in self.schema.fields}


def _describe(field: FieldSpec) -> str:
    """One-line instruction for a field, used inside the prompt."""
    if field.type == "number":
        shape = f'an object {{"value": <number>, "unit": "<unit exactly as printed>"}}'
        if field.unit:
            shape += f" (expected unit {field.unit})"
    elif field.type == "enum":
        shape = "one of " + ", ".join(f'"{v}"' for v in field.values)
    else:
        shape = "a string"
    return f"{shape}. {field.description}".rstrip()


def _parse_reply(reply: str) -> dict[str, Any]:
    """Decode the model's reply into a dict, tolerating markdown fences.

    Raises:
        ValueError: If the reply is not JSON or not a JSON object.
    """
    match = _FENCE.match(reply)
    body = match.group(1) if match else reply
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"reply is JSON but not an object: {type(data).__name__}")
    return data


def _coerce(field: FieldSpec, value: Any) -> Any:
    """Normalise one raw value to the shape the validator expects."""
    if value is None:
        return None
    if field.type == "number":
        if isinstance(value, dict):
            unit = value.get("unit")
            return {"value": value.get("value"), "unit": str(unit) if unit is not None else None}
        # A bare number is accepted; the validator will note the missing unit.
        return {"value": value, "unit": None}
    return value if isinstance(value, str) else str(value)
