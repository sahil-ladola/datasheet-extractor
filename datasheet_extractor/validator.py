"""Check an extracted dict against the schema and report problems.

The validator is the only place that decides whether a value is good. It
takes the raw dict the extractor produced and returns a cleaned dict plus
a list of ``Problem`` entries. Nothing is silently dropped: every change
of mind about a value is recorded with a field name, a code and a
plain-language message so the dashboard can show it.

Severity rules:

* ``error``: the value is unusable. A required field is missing, a number
  came with a unit nobody recognises, or the "number" was not numeric.
* ``warning``: the value is kept but looks suspicious. Outside the
  schema's plausible range, not one of the enum's values, or a string that
  does not match its pattern. Missing unit on a number is also a warning:
  the schema's canonical unit is assumed.

Numbers are converted to the schema's canonical unit so that records from
different manufacturers can be compared and filtered.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from datasheet_extractor.schema import FieldSpec, Schema

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with one field."""

    field: str
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Cleaned values plus everything the validator objected to.

    Attributes:
        data: One entry per schema field. Numbers are in canonical units;
            strings are stripped; anything unusable is ``None``.
        problems: In schema order, then in the order they were found.
    """

    data: dict[str, Any]
    problems: list[Problem] = field(default_factory=list)

    @property
    def status(self) -> str:
        """``error`` if any error, else ``warning`` if any warning, else ``ok``."""
        severities = {p.severity for p in self.problems}
        if ERROR in severities:
            return ERROR
        if WARNING in severities:
            return WARNING
        return "ok"

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == ERROR]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == WARNING]


# Unit conversion table. Outer key: canonical unit as written in the schema.
# Inner key: a unit as the LLM might report it, normalised by _normalise_unit.
# Inner value: function taking the reported number and returning it in the
# canonical unit.
_identity: Callable[[float], float] = lambda x: x  # noqa: E731

UNIT_CONVERSIONS: dict[str, dict[str, Callable[[float], float]]] = {
    "C": {
        "c": _identity,
        "degc": _identity,
        "℃": _identity,
        "f": lambda f: (f - 32) * 5 / 9,
        "degf": lambda f: (f - 32) * 5 / 9,
        "k": lambda k: k - 273.15,
    },
    "V": {
        "v": _identity,
        "vdc": _identity,
        "vac": _identity,
        "mv": lambda mv: mv / 1000,
        "kv": lambda kv: kv * 1000,
    },
    "mA": {
        "ma": _identity,
        "a": lambda a: a * 1000,
    },
    "ms": {
        "ms": _identity,
        "s": lambda s: s * 1000,
        "sec": lambda s: s * 1000,
        "us": lambda us: us / 1000,
        "µs": lambda us: us / 1000,
        "min": lambda m: m * 60_000,
    },
    "g": {
        "g": _identity,
        "kg": lambda kg: kg * 1000,
        "mg": lambda mg: mg / 1000,
        "lb": lambda lb: lb * 453.592,
        "lbs": lambda lb: lb * 453.592,
        "oz": lambda oz: oz * 28.3495,
    },
    "mm": {
        "mm": _identity,
        "cm": lambda cm: cm * 10,
        "m": lambda m: m * 1000,
        "in": lambda i: i * 25.4,
        "inch": lambda i: i * 25.4,
    },
}


def _normalise_unit(unit: str) -> str:
    """Lower-case, drop whitespace and degree signs: ``"° F"`` becomes ``"f"``."""
    return re.sub(r"[\s°º]", "", unit).lower()


class Validator:
    """Raw extracted dict in, ``ValidationResult`` out.

    Args:
        schema: The schema the dict was extracted against.

    Raises:
        ValueError: If a number field's canonical unit has no conversion
            table entry. That is a schema mistake and should fail early.
    """

    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        for spec in schema.fields:
            if spec.type == "number" and spec.unit and spec.unit not in UNIT_CONVERSIONS:
                raise ValueError(
                    f"Field {spec.name!r}: no unit conversions defined for {spec.unit!r}"
                )

    def validate(self, raw: dict[str, Any]) -> ValidationResult:
        """Clean every schema field in ``raw`` and collect problems.

        Keys missing from ``raw`` are treated as ``None``.
        """
        data: dict[str, Any] = {}
        problems: list[Problem] = []
        for spec in self.schema.fields:
            value, found = self._check_field(spec, raw.get(spec.name))
            data[spec.name] = value
            problems.extend(found)
        return ValidationResult(data=data, problems=problems)

    def _check_field(self, spec: FieldSpec, value: Any) -> tuple[Any, list[Problem]]:
        """Dispatch on field type. Returns the cleaned value and its problems."""
        if _is_missing(value):
            if spec.required:
                return None, [Problem(spec.name, "missing", ERROR, "Required field not found")]
            return None, []
        if spec.type == "number":
            return self._check_number(spec, value)
        if spec.type == "enum":
            return self._check_enum(spec, value)
        return self._check_string(spec, value)

    def _check_number(self, spec: FieldSpec, value: Any) -> tuple[Any, list[Problem]]:
        problems: list[Problem] = []
        raw_value = value.get("value") if isinstance(value, dict) else value
        raw_unit = value.get("unit") if isinstance(value, dict) else None

        if _is_missing(raw_value):
            if spec.required:
                problems.append(Problem(spec.name, "missing", ERROR, "Required field not found"))
            return None, problems

        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            problems.append(
                Problem(spec.name, "not_a_number", ERROR, f"Expected a number, got {raw_value!r}")
            )
            return None, problems

        if spec.unit:
            if raw_unit is None:
                problems.append(
                    Problem(spec.name, "missing_unit", WARNING, f"No unit given, assumed {spec.unit}")
                )
            else:
                convert = UNIT_CONVERSIONS[spec.unit].get(_normalise_unit(raw_unit))
                if convert is None:
                    problems.append(
                        Problem(
                            spec.name,
                            "unknown_unit",
                            ERROR,
                            f"Cannot convert unit {raw_unit!r} to {spec.unit}",
                        )
                    )
                    return None, problems
                number = convert(number)

        number = _tidy(number)
        if spec.range is not None:
            low, high = spec.range
            if not low <= number <= high:
                problems.append(
                    Problem(
                        spec.name,
                        "out_of_range",
                        WARNING,
                        f"{number} {spec.unit or ''} is outside the expected {low} to {high}".strip(),
                    )
                )
        return number, problems

    def _check_enum(self, spec: FieldSpec, value: Any) -> tuple[Any, list[Problem]]:
        text = str(value).strip()
        for allowed in spec.values:
            if text.lower() == allowed.lower():
                return allowed, []
        return text, [
            Problem(
                spec.name,
                "invalid_enum",
                WARNING,
                f"{text!r} is not one of: {', '.join(spec.values)}",
            )
        ]

    def _check_string(self, spec: FieldSpec, value: Any) -> tuple[Any, list[Problem]]:
        text = str(value).strip()
        if spec.pattern and not re.fullmatch(spec.pattern, text):
            return text, [
                Problem(
                    spec.name,
                    "pattern_mismatch",
                    WARNING,
                    f"{text!r} does not match the expected pattern {spec.pattern}",
                )
            ]
        return text, []


def _is_missing(value: Any) -> bool:
    """``None`` and blank strings both count as "not found"."""
    return value is None or (isinstance(value, str) and not value.strip())


def _tidy(number: float) -> float | int:
    """Round away float noise from conversion and show whole numbers as ints."""
    rounded = round(number, 3)
    return int(rounded) if rounded.is_integer() else rounded
