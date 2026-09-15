"""Tests for the Validator.

Each test starts from the known-good reply for the sample sensor, changes
one or two fields, and checks both the cleaned value and the problem list.
Starting from a valid record keeps each test about one thing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from datasheet_extractor.schema import Schema
from datasheet_extractor.validator import Validator


@pytest.fixture
def good_record(good_reply: str) -> dict[str, Any]:
    """The canned reply as a dict, fresh for every test."""
    return json.loads(good_reply)


def codes(result, field: str) -> list[str]:
    """Problem codes recorded for one field, for compact assertions."""
    return [p.code for p in result.problems if p.field == field]


def test_missing_required_field_is_an_error(sensor_schema: Schema, good_record) -> None:
    good_record["operating_temp_min"] = None   # required
    good_record["weight"] = None               # optional
    del good_record["part_number"]             # required, key absent entirely

    result = Validator(sensor_schema).validate(good_record)

    assert result.status == "error"
    assert codes(result, "operating_temp_min") == ["missing"]
    assert codes(result, "part_number") == ["missing"]
    assert codes(result, "weight") == []
    assert result.data["operating_temp_min"] is None
    assert result.data["weight"] is None


def test_units_are_converted_to_canonical(sensor_schema: Schema, good_record) -> None:
    good_record["operating_temp_min"] = {"value": -13, "unit": "°F"}   # -25 C
    good_record["weight"] = {"value": 0.12, "unit": "kg"}             # 120 g
    good_record["response_time"] = {"value": 3, "unit": "s"}          # 3000 ms
    good_record["supply_voltage_min"] = {"value": 18, "unit": "V DC"}  # already V

    result = Validator(sensor_schema).validate(good_record)

    assert result.status == "ok"
    assert result.problems == []
    assert result.data["operating_temp_min"] == -25
    assert result.data["weight"] == 120
    assert result.data["response_time"] == 3000
    assert result.data["supply_voltage_min"] == 18


def test_unknown_and_missing_units_are_flagged(sensor_schema: Schema, good_record) -> None:
    good_record["weight"] = {"value": 5, "unit": "stone"}
    good_record["response_time"] = {"value": 3000, "unit": None}

    result = Validator(sensor_schema).validate(good_record)

    # Unknown unit: the number cannot be trusted, so it is dropped.
    assert codes(result, "weight") == ["unknown_unit"]
    assert result.data["weight"] is None
    # Missing unit: canonical unit assumed, value kept, but noted.
    assert codes(result, "response_time") == ["missing_unit"]
    assert result.data["response_time"] == 3000
    assert result.status == "error"


def test_out_of_range_is_a_warning_and_the_value_is_kept(sensor_schema: Schema, good_record) -> None:
    good_record["operating_temp_max"] = {"value": 250, "unit": "C"}   # range is 0..200

    result = Validator(sensor_schema).validate(good_record)

    assert result.status == "warning"
    assert codes(result, "operating_temp_max") == ["out_of_range"]
    assert result.data["operating_temp_max"] == 250
    assert "250" in result.warnings[0].message


def test_boundary_values_are_inside_the_range(sensor_schema: Schema, good_record) -> None:
    # operating_temp_min range is [-60, 50]; supply_voltage_max range is [0, 60].
    good_record["operating_temp_min"] = {"value": -60, "unit": "C"}
    good_record["supply_voltage_max"] = {"value": 60, "unit": "V"}
    assert Validator(sensor_schema).validate(good_record).problems == []

    # One step past either bound is flagged.
    good_record["operating_temp_min"] = {"value": -60.001, "unit": "C"}
    good_record["supply_voltage_max"] = {"value": 60.001, "unit": "V"}
    result = Validator(sensor_schema).validate(good_record)
    assert codes(result, "operating_temp_min") == ["out_of_range"]
    assert codes(result, "supply_voltage_max") == ["out_of_range"]


def test_enum_and_pattern_checks(sensor_schema: Schema, good_record) -> None:
    good_record["sensor_type"] = "Temperature"       # wrong case: normalised
    good_record["output_signal"] = "4...20 mA"       # not an allowed value
    good_record["ip_rating"] = "IP6"                 # one digit short of a rating

    result = Validator(sensor_schema).validate(good_record)

    assert result.data["sensor_type"] == "temperature"
    assert codes(result, "sensor_type") == []
    assert codes(result, "output_signal") == ["invalid_enum"]
    assert result.data["output_signal"] == "4...20 mA"
    assert codes(result, "ip_rating") == ["pattern_mismatch"]
    assert result.status == "warning"

    # Real datasheets print one rating, or several with a space or separator.
    for printed in ("IP69K", "IP 67", "IP65; IP67", "IP67, IP66"):
        good_record["ip_rating"] = printed
        assert codes(Validator(sensor_schema).validate(good_record), "ip_rating") == []
