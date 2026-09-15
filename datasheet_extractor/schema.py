"""Load a field schema from JSON into typed objects.

The schema is the single source of truth for what gets extracted. The
extractor reads names and descriptions from it to build the prompt and to
enforce the shape of the reply; the validator reads units, ranges, enum
values and patterns from it to check the result. Keeping both on one
definition means adding a field is a one-line change to a JSON file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIELD_TYPES = ("string", "number", "enum")


@dataclass(frozen=True)
class FieldSpec:
    """One extractable field and how to check it.

    Attributes:
        name: Key used in the extracted dict and the database.
        type: One of ``string``, ``number`` or ``enum``.
        required: Whether a missing value is an error rather than a gap.
        description: Plain-language hint shown to the LLM.
        unit: Canonical unit for ``number`` fields, e.g. ``C`` or ``V``.
        range: Plausible ``(low, high)`` bounds for ``number`` fields.
            Values outside are flagged, not rejected.
        values: Allowed strings for ``enum`` fields.
        pattern: Regular expression a ``string`` field must fully match.
    """

    name: str
    type: str
    required: bool = False
    description: str = ""
    unit: str | None = None
    range: tuple[float, float] | None = None
    values: tuple[str, ...] = ()
    pattern: str | None = None


@dataclass(frozen=True)
class Schema:
    """A named set of fields for one component type."""

    component_type: str
    fields: tuple[FieldSpec, ...]

    @property
    def field_names(self) -> list[str]:
        """Field names in schema order."""
        return [f.name for f in self.fields]

    def get(self, name: str) -> FieldSpec | None:
        """Return the field called ``name``, or ``None`` if there is none."""
        return next((f for f in self.fields if f.name == name), None)


def _parse_field(raw: dict[str, Any]) -> FieldSpec:
    """Turn one JSON object into a FieldSpec, rejecting inconsistent entries."""
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"Schema field without a name: {raw!r}")

    field_type = raw.get("type")
    if field_type not in FIELD_TYPES:
        raise ValueError(f"Field {name!r}: type must be one of {FIELD_TYPES}, got {field_type!r}")

    values = tuple(raw.get("values", ()))
    if field_type == "enum" and not values:
        raise ValueError(f"Field {name!r}: enum fields need a non-empty 'values' list")

    range_raw = raw.get("range")
    field_range: tuple[float, float] | None = None
    if range_raw is not None:
        if len(range_raw) != 2 or range_raw[0] > range_raw[1]:
            raise ValueError(f"Field {name!r}: range must be [low, high], got {range_raw!r}")
        field_range = (float(range_raw[0]), float(range_raw[1]))

    return FieldSpec(
        name=name,
        type=field_type,
        required=bool(raw.get("required", False)),
        description=str(raw.get("description", "")),
        unit=raw.get("unit"),
        range=field_range,
        values=values,
        pattern=raw.get("pattern"),
    )


def load_schema(path: str | Path) -> Schema:
    """Read a schema JSON file such as ``schemas/sensor.json``.

    Raises:
        ValueError: If the file is missing required keys or a field is
            inconsistent, for example an enum with no values.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    component_type = raw.get("component_type")
    if not isinstance(component_type, str) or not component_type:
        raise ValueError(f"{path}: 'component_type' is required")
    fields = tuple(_parse_field(entry) for entry in raw.get("fields", []))
    if not fields:
        raise ValueError(f"{path}: 'fields' must contain at least one field")
    return Schema(component_type=component_type, fields=fields)
