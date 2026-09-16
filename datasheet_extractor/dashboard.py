"""Dash dashboard for browsing extracted records.

Run with::

    python -m datasheet_extractor.dashboard

The dashboard reads from ``Store`` and nothing else. It never touches PDFs
or the LLM, so it can be opened while the pipeline is running and shows
whatever has been stored so far. Filtering is done in plain Python on the
loaded rows (``filter_rows``), which keeps that logic testable without a
browser.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html

from datasheet_extractor.store import Record, Store

DEFAULT_DB = Path("datasheets.db")

# (row key, column header). Order is the table's column order.
COLUMNS: list[tuple[str, str]] = [
    ("part_number", "Part number"),
    ("manufacturer", "Manufacturer"),
    ("sensor_type", "Type"),
    ("output_signal", "Output"),
    ("supply_voltage_min", "Supply min (V)"),
    ("supply_voltage_max", "Supply max (V)"),
    ("operating_temp_min", "Temp min (°C)"),
    ("operating_temp_max", "Temp max (°C)"),
    ("measuring_range", "Measuring range"),
    ("ip_rating", "IP rating"),
    ("response_time", "Response (ms)"),
    ("weight", "Weight (g)"),
    ("status", "Status"),
    ("problem_count", "Problems"),
]

STATUS_ORDER = ("ok", "warning", "error")
BAR_COLOR = "#3B6EA5"


# -- data --------------------------------------------------------------------


def record_to_row(record: Record) -> dict[str, Any]:
    """Flatten a stored record into one table row."""
    row: dict[str, Any] = dict(record.data)
    row["id"] = record.id
    row["status"] = record.status
    row["problem_count"] = len(record.problems)
    row["problems_text"] = "\n".join(
        f"{p.severity.upper()} {p.field}: {p.message}" for p in record.problems
    )
    row["source_file"] = Path(record.source_file).name
    row["extracted_at"] = record.extracted_at
    return row


def load_rows(db_path: str | Path) -> list[dict[str, Any]]:
    """Read every record from the database as table rows."""
    with Store(db_path) as store:
        return [record_to_row(r) for r in store.all_records()]


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    manufacturers: Sequence[str] | None = None,
    sensor_types: Sequence[str] | None = None,
    statuses: Sequence[str] | None = None,
    search: str = "",
    cold_limit: float | None = None,
) -> list[dict[str, Any]]:
    """Apply the dashboard's filters to ``rows``.

    Args:
        manufacturers, sensor_types, statuses: Keep rows whose value is in
            the list. An empty or ``None`` list means "no filter".
        search: Case-insensitive substring match on the part number.
        cold_limit: Keep rows rated to operate at this temperature or
            colder, i.e. ``operating_temp_min <= cold_limit``. Rows with
            no known minimum are dropped, since they cannot be vouched for.
    """
    needle = search.strip().lower()
    kept = []
    for row in rows:
        if manufacturers and row.get("manufacturer") not in manufacturers:
            continue
        if sensor_types and row.get("sensor_type") not in sensor_types:
            continue
        if statuses and row.get("status") not in statuses:
            continue
        if needle and needle not in str(row.get("part_number") or "").lower():
            continue
        if cold_limit is not None:
            t_min = row.get("operating_temp_min")
            if t_min is None or t_min > cold_limit:
                continue
        kept.append(row)
    return kept


# -- figure ------------------------------------------------------------------


def build_figure(rows: list[dict[str, Any]]) -> go.Figure:
    """One horizontal bar per part spanning its operating temperature range.

    Rows missing either end of the range are left out rather than drawn
    misleadingly short.
    """
    plotted = [
        r for r in rows
        if r.get("operating_temp_min") is not None and r.get("operating_temp_max") is not None
    ]
    plotted.sort(key=lambda r: (r["operating_temp_min"], r["operating_temp_max"]))
    labels = [str(r.get("part_number") or r["source_file"]) for r in plotted]
    starts = [r["operating_temp_min"] for r in plotted]
    spans = [r["operating_temp_max"] - r["operating_temp_min"] for r in plotted]

    fig = go.Figure(
        go.Bar(
            y=labels,
            x=spans,
            base=starts,
            orientation="h",
            marker={"color": BAR_COLOR, "line": {"width": 0}},
            width=0.5,
            hovertemplate="%{y}<br>%{base}°C to %{customdata}°C<extra></extra>",
            customdata=[r["operating_temp_max"] for r in plotted],
        )
    )
    fig.update_layout(
        title={"text": "Operating temperature range per part", "x": 0, "font": {"size": 15}},
        height=max(220, 60 + 34 * len(plotted)),
        margin={"l": 10, "r": 20, "t": 40, "b": 30},
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis={"title": "°C", "zeroline": True, "zerolinecolor": "#CBD2DA", "gridcolor": "#EEF1F4"},
        yaxis={"automargin": True},
        bargap=0.3,
        showlegend=False,
    )
    if not plotted:
        fig.add_annotation(text="No records with a complete temperature range", showarrow=False)
    return fig


# -- app ---------------------------------------------------------------------


def create_app(db_path: str | Path = DEFAULT_DB) -> Dash:
    """Build the Dash application bound to one database file."""
    app = Dash(__name__, title="Datasheet Extractor")
    initial = load_rows(db_path)

    def options(key: str) -> list[dict[str, str]]:
        values = sorted({str(r[key]) for r in initial if r.get(key)})
        return [{"label": v, "value": v} for v in values]

    app.layout = html.Div(
        style={"fontFamily": "system-ui, sans-serif", "maxWidth": "1280px", "margin": "0 auto",
               "padding": "16px 24px", "color": "#1F2933"},
        children=[
            html.H1("Datasheet Extractor", style={"fontSize": "24px", "marginBottom": "4px"}),
            html.P(
                "Specifications extracted from PDF datasheets. Filter, sort, and click a row "
                "to see what the validator flagged.",
                style={"color": "#52606D", "marginTop": 0},
            ),
            html.Div(
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"},
                children=[
                    _labelled("Manufacturer", dcc.Dropdown(id="f-manufacturer", options=options("manufacturer"), multi=True)),
                    _labelled("Sensor type", dcc.Dropdown(id="f-type", options=options("sensor_type"), multi=True)),
                    _labelled("Status", dcc.Dropdown(
                        id="f-status", multi=True,
                        options=[{"label": s, "value": s} for s in STATUS_ORDER],
                    )),
                    _labelled("Part number contains", dcc.Input(id="f-search", type="text", debounce=True,
                                                                style={"width": "100%", "height": "34px"})),
                    _labelled("Operates down to (°C)", dcc.Input(id="f-cold", type="number", debounce=True,
                                                                 placeholder="e.g. -20",
                                                                 style={"width": "100%", "height": "34px"})),
                ],
            ),
            html.P(id="summary", style={"color": "#52606D", "margin": "12px 0 4px"}),
            dash_table.DataTable(
                id="table",
                columns=[{"name": header, "id": key} for key, header in COLUMNS],
                data=initial,
                sort_action="native",
                row_selectable="single",
                page_size=15,
                style_table={"overflowX": "auto"},
                style_cell={"fontFamily": "system-ui, sans-serif", "fontSize": "13px", "padding": "6px 8px",
                            "textAlign": "left", "maxWidth": "220px", "overflow": "hidden",
                            "textOverflow": "ellipsis"},
                style_header={"fontWeight": "600", "backgroundColor": "#F5F7FA"},
                style_data_conditional=[
                    {"if": {"filter_query": "{status} = error"}, "backgroundColor": "#FDECEC"},
                    {"if": {"filter_query": "{status} = warning"}, "backgroundColor": "#FFF6E0"},
                ],
            ),
            html.Div(id="detail", style={"margin": "12px 0", "padding": "12px 16px", "border": "1px solid #E4E7EB",
                                         "borderRadius": "6px", "minHeight": "48px", "whiteSpace": "pre-wrap"}),
            dcc.Graph(id="chart", config={"displayModeBar": False}),
        ],
    )

    @app.callback(
        Output("table", "data"),
        Output("summary", "children"),
        Output("chart", "figure"),
        Input("f-manufacturer", "value"),
        Input("f-type", "value"),
        Input("f-status", "value"),
        Input("f-search", "value"),
        Input("f-cold", "value"),
    )
    def apply_filters(manufacturers, sensor_types, statuses, search, cold):
        rows = filter_rows(
            load_rows(db_path),
            manufacturers=manufacturers,
            sensor_types=sensor_types,
            statuses=statuses,
            search=search or "",
            cold_limit=cold,
        )
        flagged = sum(1 for r in rows if r["status"] != "ok")
        summary = f"{len(rows)} record(s) shown, {flagged} with problems."
        return rows, summary, build_figure(rows)

    @app.callback(
        Output("detail", "children"),
        Input("table", "selected_rows"),
        State("table", "data"),
    )
    def show_detail(selected, data):
        if not selected or not data:
            return html.Span("Select a row to see its source file and any problems.",
                             style={"color": "#7B8794"})
        row = data[selected[0]]
        heading = html.Div(
            f"{row.get('part_number') or '(no part number)'} from {row['source_file']}, "
            f"extracted {row['extracted_at'][:10]}",
            style={"fontWeight": "600", "marginBottom": "6px"},
        )
        if not row["problems_text"]:
            return [heading, html.Span("No problems. Every required field was found and in range.")]
        return [heading, html.Span(row["problems_text"])]

    return app


def _labelled(label: str, control: Any) -> html.Div:
    return html.Div(
        style={"flex": "1 1 200px", "minWidth": "180px"},
        children=[html.Label(label, style={"fontSize": "12px", "color": "#52606D"}), control],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m datasheet_extractor.dashboard",
        description="Browse extracted datasheet records in the browser.",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database written by the pipeline")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--debug", action="store_true", help="Dash debug mode with hot reload")
    args = parser.parse_args(argv)

    if not Path(args.db).is_file():
        print(f"error: {args.db} not found. Run the pipeline first.", file=sys.stderr)
        return 2
    create_app(args.db).run(host="127.0.0.1", port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
