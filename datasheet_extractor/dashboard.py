"""Dash dashboard for browsing extracted records.

Run with::

    python -m datasheet_extractor.dashboard

The dashboard reads from ``Store`` and nothing else. It never touches PDFs
or the LLM, so it can be opened while the pipeline is running and shows
whatever has been stored so far. Filtering is done in plain Python on the
loaded rows (``filter_rows``), which keeps that logic testable without a
browser.

The page is built around one idea: the filters read as a sentence, and the
line under it is the answer. The table below is styled like the printed
specification tables the data came from.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html

from datasheet_extractor.store import Record, Store

DEFAULT_DB = Path("datasheets.db")

FONT_URL = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap"
FONT_STACK = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif'

INK = "#16212C"
INK_SOFT = "#5C6B78"
RULE = "#D3DAE0"
SHEET = "#FFFFFF"
BLUE = "#1D5FBF"
STATUS_COLOR = {"ok": "#2B7A4B", "warning": "#A86A0F", "error": "#B93A2C"}
STATUS_WORD = {"ok": "ok", "warning": "check", "error": "incomplete"}

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
    ("status_word", "Checks"),
]
NUMERIC_COLUMNS = {
    "supply_voltage_min", "supply_voltage_max", "operating_temp_min",
    "operating_temp_max", "response_time", "weight",
}


# -- data --------------------------------------------------------------------


def record_to_row(record: Record) -> dict[str, Any]:
    """Flatten a stored record into one table row."""
    row: dict[str, Any] = dict(record.data)
    row["id"] = record.id
    row["status"] = record.status
    row["status_word"] = STATUS_WORD[record.status]
    row["problem_count"] = len(record.problems)
    row["problems_json"] = json.dumps(
        [{"severity": p.severity, "field": p.field, "message": p.message} for p in record.problems]
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


def build_figure(rows: list[dict[str, Any]], cold_limit: float | None = None) -> go.Figure:
    """One horizontal bar per part spanning its operating temperature range.

    Rows missing either end of the range are left out rather than drawn
    misleadingly short. When ``cold_limit`` is given, a dashed line marks
    it so the answer to "operates down to X" is visible in the picture.
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
            marker={"color": BLUE, "line": {"width": 0}},
            width=0.45,
            hovertemplate="%{y}<br>%{base} °C to %{customdata} °C<extra></extra>",
            customdata=[r["operating_temp_max"] for r in plotted],
        )
    )
    fig.update_layout(
        font={"family": FONT_STACK, "color": INK, "size": 13},
        height=max(200, 50 + 30 * len(plotted)),
        margin={"l": 8, "r": 16, "t": 8, "b": 36},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={
            "title": {"text": "°C", "font": {"color": INK_SOFT}},
            "zeroline": True, "zerolinecolor": RULE, "zerolinewidth": 1, "dtick": 20,
            "gridcolor": RULE, "griddash": "dot", "tickfont": {"color": INK_SOFT},
        },
        yaxis={"automargin": True, "tickfont": {"color": INK}},
        bargap=0.35,
        showlegend=False,
        hoverlabel={"bgcolor": INK, "font": {"family": FONT_STACK, "color": SHEET}},
    )
    if cold_limit is not None and plotted:
        fig.add_vline(x=cold_limit, line={"color": INK, "width": 1, "dash": "dash"})
        fig.add_annotation(
            x=cold_limit, y=1, yref="paper", yanchor="bottom", showarrow=False,
            text=f"{cold_limit:g} °C", font={"color": INK, "size": 12},
        )
        fig.update_layout(margin={"t": 24})
    if not plotted:
        fig.add_annotation(
            text="No matching record has a complete temperature range.",
            showarrow=False, font={"color": INK_SOFT},
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
    return fig


# -- app ---------------------------------------------------------------------


def create_app(db_path: str | Path = DEFAULT_DB) -> Dash:
    """Build the Dash application bound to one database file."""
    app = Dash(__name__, title="Datasheet Extractor", external_stylesheets=[FONT_URL])
    initial = load_rows(db_path)

    def options(key: str, placeholder: str) -> list[dict[str, str]]:
        values = sorted({str(r[key]) for r in initial if r.get(key)})
        return [{"label": v, "value": v} for v in values]

    app.layout = html.Div(
        className="page",
        children=[
            html.Header(
                className="masthead",
                children=[
                    html.Span("Datasheet Extractor", className="brand"),
                    html.Span(id="on-file", className="on-file", children=_on_file_text(initial)),
                ],
            ),
            html.Section(
                className="ask",
                children=[
                    html.Div(
                        className="sentence",
                        children=[
                            html.Span("Show"),
                            dcc.Dropdown(id="f-type", className="inline-select wide",
                                         options=options("sensor_type", ""), placeholder="all sensor types"),
                            html.Span("sensors from"),
                            dcc.Dropdown(id="f-manufacturer", className="inline-select wider",
                                         options=options("manufacturer", ""), placeholder="any manufacturer"),
                            html.Span("that operate down to"),
                            html.Span(
                                className="nowrap",
                                children=[
                                    dcc.Input(id="f-cold", type="number", debounce=True,
                                              placeholder="any", className="inline-number"),
                                    html.Span(" °C."),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="sentence secondary",
                        children=[
                            html.Span("Part number contains"),
                            dcc.Input(id="f-search", type="text", debounce=True, placeholder="anything",
                                      className="inline-text"),
                            html.Span("and the checks came back"),
                            dcc.RadioItems(
                                id="f-status", className="segmented", inline=True, value="any",
                                options=[
                                    {"label": "any", "value": "any"},
                                    {"label": "ok", "value": "ok"},
                                    {"label": "check", "value": "warning"},
                                    {"label": "incomplete", "value": "error"},
                                ],
                            ),
                        ],
                    ),
                    html.P(id="answer", className="answer"),
                ],
            ),
            dash_table.DataTable(
                id="table",
                columns=[{"name": header, "id": key} for key, header in COLUMNS],
                data=initial,
                sort_action="native",
                row_selectable="single",
                page_size=15,
                style_table={"overflowX": "auto"},
                style_cell={
                    "fontFamily": FONT_STACK, "fontSize": "13px", "padding": "7px 10px",
                    "textAlign": "left", "maxWidth": "240px", "overflow": "hidden",
                    "textOverflow": "ellipsis", "borderLeft": "none", "borderRight": "none",
                    "borderTop": "none", "borderBottom": f"1px solid {RULE}",
                    "color": INK, "backgroundColor": SHEET,
                },
                style_cell_conditional=[
                    {"if": {"column_id": c}, "textAlign": "right", "fontVariantNumeric": "tabular-nums"}
                    for c in NUMERIC_COLUMNS
                ] + [{"if": {"column_id": "status_word"}, "fontWeight": "500"}],
                style_header={
                    "fontWeight": "600", "backgroundColor": SHEET, "color": INK,
                    "borderBottom": f"2px solid {INK}", "borderTop": "none",
                },
                style_data_conditional=[
                    {"if": {"filter_query": "{status} = error", "column_id": "part_number"},
                     "borderLeft": f"3px solid {STATUS_COLOR['error']}"},
                    {"if": {"filter_query": "{status} = warning", "column_id": "part_number"},
                     "borderLeft": f"3px solid {STATUS_COLOR['warning']}"},
                    {"if": {"filter_query": "{status} = ok", "column_id": "part_number"},
                     "borderLeft": f"3px solid {STATUS_COLOR['ok']}"},
                    {"if": {"filter_query": "{status} = error", "column_id": "status_word"},
                     "color": STATUS_COLOR["error"]},
                    {"if": {"filter_query": "{status} = warning", "column_id": "status_word"},
                     "color": STATUS_COLOR["warning"]},
                    {"if": {"filter_query": "{status} = ok", "column_id": "status_word"},
                     "color": STATUS_COLOR["ok"]},
                    {"if": {"state": "selected"}, "backgroundColor": "#EAF1FB", "border": "none",
                     "borderBottom": f"1px solid {RULE}"},
                ],
            ),
            html.Div(
                className="below",
                children=[
                    html.Section(id="detail", className="detail"),
                    html.Section(
                        className="chart",
                        children=[
                            html.H2("Operating temperature, by part", className="section-title"),
                            dcc.Graph(id="chart", responsive=True,
                                      config={"displayModeBar": False}),
                        ],
                    ),
                ],
            ),
        ],
    )

    @app.callback(
        Output("table", "data"),
        Output("table", "selected_rows"),
        Output("answer", "children"),
        Output("chart", "figure"),
        Output("chart", "style"),
        Input("f-manufacturer", "value"),
        Input("f-type", "value"),
        Input("f-status", "value"),
        Input("f-search", "value"),
        Input("f-cold", "value"),
    )
    def apply_filters(manufacturer, sensor_type, status, search, cold):
        rows = load_rows(db_path)
        matched = filter_rows(
            rows,
            manufacturers=[manufacturer] if manufacturer else None,
            sensor_types=[sensor_type] if sensor_type else None,
            statuses=[status] if status and status != "any" else None,
            search=search or "",
            cold_limit=cold,
        )
        figure = build_figure(matched, cold)
        # A responsive graph takes its size from the container, so the
        # container must follow the per-row height the figure asks for.
        style = {"height": f"{figure.layout.height}px"}
        return matched, [], _answer_text(matched, len(rows)), figure, style

    @app.callback(
        Output("detail", "children"),
        Input("table", "selected_rows"),
        State("table", "data"),
    )
    def show_detail(selected, data):
        if not selected or not data:
            return [
                html.H2("Record detail", className="section-title"),
                html.P("Select a row to see where it came from and what the checks found.",
                       className="muted"),
            ]
        row = data[selected[0]]
        problems = json.loads(row["problems_json"])
        heading = html.H2(row.get("part_number") or "No part number found", className="section-title")
        provenance = html.P(
            f"From {row['source_file']}, extracted {row['extracted_at'][:10]}.", className="muted"
        )
        if not problems:
            body = html.P("All checks passed. Every required value was found and within range.")
        else:
            body = html.Ul(
                className="problems",
                children=[
                    html.Li(
                        className=f"problem {p['severity']}",
                        children=[
                            html.Span(p["field"].replace("_", " "), className="field"),
                            html.Span(p["message"]),
                        ],
                    )
                    for p in problems
                ],
            )
        return [heading, provenance, body]

    return app


def _on_file_text(rows: list[dict[str, Any]]) -> str:
    flagged = sum(1 for r in rows if r["status"] != "ok")
    text = f"{len(rows)} records on file"
    return f"{text}, {flagged} need attention" if flagged else text


def _answer_text(matched: list[dict[str, Any]], total: int) -> str:
    if total == 0:
        return "Nothing on file yet. Run the pipeline on a folder of datasheets first."
    if not matched:
        return "No sensors match. Loosen a filter to see more."
    flagged = sum(1 for r in matched if r["status"] != "ok")
    noun = "sensor matches" if len(matched) == 1 else "sensors match"
    text = f"{len(matched)} of {total} {noun}."
    if flagged:
        text += f" {flagged} of them need attention."
    return text


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
