"""Tests for the dashboard's filter logic.

The browser side of Dash is not tested here. The filtering is a plain
function over plain dicts, so it can be checked directly and fast.
"""

from __future__ import annotations

from datasheet_extractor.dashboard import build_figure, filter_rows

ROWS = [
    {"part_number": "COLD-1", "manufacturer": "SICK", "sensor_type": "proximity",
     "status": "ok", "operating_temp_min": -40, "operating_temp_max": 60},
    {"part_number": "MILD-2", "manufacturer": "ifm", "sensor_type": "pressure",
     "status": "warning", "operating_temp_min": -25, "operating_temp_max": 80},
    {"part_number": "WARM-3", "manufacturer": "ifm", "sensor_type": "temperature",
     "status": "error", "operating_temp_min": 0, "operating_temp_max": 70},
    {"part_number": "UNKNOWN-4", "manufacturer": "Balluff", "sensor_type": "proximity",
     "status": "error", "operating_temp_min": None, "operating_temp_max": None},
]


def parts(rows) -> list[str]:
    return [r["part_number"] for r in rows]


def test_filters_combine_and_cold_limit_excludes_unknown_minimums() -> None:
    assert parts(filter_rows(ROWS)) == parts(ROWS)                     # no filter
    assert parts(filter_rows(ROWS, cold_limit=-20)) == ["COLD-1", "MILD-2"]
    assert parts(filter_rows(ROWS, cold_limit=-40)) == ["COLD-1"]      # boundary inclusive
    assert parts(filter_rows(ROWS, manufacturers=["ifm"], statuses=["error"])) == ["WARM-3"]
    assert parts(filter_rows(ROWS, search="cold")) == ["COLD-1"]       # case-insensitive
    assert parts(filter_rows(ROWS, sensor_types=["proximity"], cold_limit=-20)) == ["COLD-1"]

    fig = build_figure(ROWS)
    assert list(fig.data[0].y) == ["COLD-1", "MILD-2", "WARM-3"]      # unknown range omitted
    assert list(fig.data[0].base) == [-40, -25, 0]
