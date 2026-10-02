"""Plotly charts in the Google Photos palette. Figures only; pages call Streamlit."""

from __future__ import annotations

import plotly.graph_objects as go

from dashboard.labels import DIMENSION_LABELS, DIMENSIONS, SOURCE_LABELS, category_labels, titleize

INK = "#202124"
STONE = "#5F6368"
BLUE = "#4285F4"
RED = "#EA4335"
YELLOW = "#FBBC04"
GREEN = "#34A853"
GRID = "#E8EAED"
PALETTE = [BLUE, RED, YELLOW, GREEN, "#1967D2", "#C5221F", "#F9AB00", "#137333"]
BAND_COLORS = {"High": GREEN, "Medium": "#F9AB00", "Low": STONE}


def _layout(title: str, *, height: int = 320) -> dict:
    return dict(
        title=dict(text=title, font=dict(family="Roboto, sans-serif", size=16, color=INK)),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Roboto, sans-serif", color=INK, size=13),
        height=height,
        margin=dict(l=48, r=16, t=48, b=40),
        legend=dict(orientation="h", y=-0.2),
        colorway=PALETTE,
    )


def _axis():
    return dict(gridcolor=GRID, zerolinecolor=GRID, color=STONE)


def source_trend(rows: list[dict]) -> go.Figure:
    fig = go.Figure()
    sources = sorted({row["source"] for row in rows})
    months = sorted({row["month"] for row in rows})
    lookup = {(row["month"], row["source"]): row["n"] for row in rows}
    for index, source in enumerate(sources):
        fig.add_trace(
            go.Scatter(
                x=months,
                y=[lookup.get((month, source), 0) for month in months],
                mode="lines+markers",
                name=SOURCE_LABELS.get(source, source),
                line=dict(color=PALETTE[index % len(PALETTE)], width=2),
            )
        )
    fig.update_layout(**_layout("Items per source over time"))
    fig.update_xaxes(**_axis())
    fig.update_yaxes(**_axis(), title="Items")
    return fig


def category_bars(rows: list[dict]) -> go.Figure:
    labels = category_labels()
    names = [labels.get(row["category"], titleize(row["category"])) for row in rows]
    fig = go.Figure(
        go.Bar(x=[row["n"] for row in rows], y=names, orientation="h", marker_color=BLUE)
    )
    fig.update_layout(
        **_layout("Retrieval category distribution", height=360),
        yaxis=dict(autorange="reversed"),
    )
    fig.update_xaxes(**_axis(), title="Items")
    return fig


def radar(areas: list[dict]) -> go.Figure:
    theta = [DIMENSION_LABELS[key] for key in DIMENSIONS] + [DIMENSION_LABELS[DIMENSIONS[0]]]
    fig = go.Figure()
    for index, area in enumerate(areas):
        values = [float(area[key]) for key in DIMENSIONS]
        fig.add_trace(
            go.Scatterpolar(
                r=values + [values[0]],
                theta=theta,
                name=area["name"],
                fill="toself",
                opacity=0.55,
                line=dict(color=PALETTE[index % len(PALETTE)]),
            )
        )
    fig.update_layout(
        **_layout("Sensitivity and trade-off radar", height=420),
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(range=[0, 5], gridcolor=GRID, color=STONE),
            angularaxis=dict(gridcolor=GRID, color=INK),
        ),
        showlegend=True,
    )
    return fig


def bubble(areas: list[dict]) -> go.Figure:
    fig = go.Figure()
    for band in ("High", "Medium", "Low"):
        group = [area for area in areas if area.get("band_live") == band]
        if not group:
            continue
        fig.add_trace(
            go.Scatter(
                x=[area["frequency"] for area in group],
                y=[area["severity"] for area in group],
                mode="markers+text",
                text=[str(area["rank"]) for area in group],
                textposition="top center",
                name=band,
                marker=dict(
                    size=[8 + float(area["evidence_quality"]) * 6 for area in group],
                    color=BAND_COLORS[band],
                    line=dict(width=1, color="#FFFFFF"),
                ),
                hovertext=[area["name"] for area in group],
                hoverinfo="text+x+y",
            )
        )
    fig.update_layout(**_layout("Frequency vs. severity"))
    fig.update_xaxes(**_axis(), title="Frequency", range=[0.5, 5.5])
    fig.update_yaxes(**_axis(), title="Severity", range=[0.5, 5.5])
    return fig


def horizontal_bars(title: str, counts: dict[str, int], *, color: str = BLUE) -> go.Figure:
    labels = [SOURCE_LABELS.get(key, titleize(key)) for key in counts]
    fig = go.Figure(go.Bar(x=list(counts.values()), y=labels, orientation="h", marker_color=color))
    fig.update_layout(**_layout(title, height=280), yaxis=dict(autorange="reversed"))
    fig.update_xaxes(**_axis())
    return fig


def paired_bars(
    remembered: dict[str, int], forgotten: dict[str, int]
) -> tuple[go.Figure, go.Figure]:
    return (
        horizontal_bars("Remembered cues", remembered, color=BLUE),
        horizontal_bars("Forgotten details", forgotten, color=YELLOW),
    )


def breakdown_funnel(counts: dict[str, int]) -> go.Figure:
    labels = [titleize(key) for key in counts]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=list(counts.values()),
            marker=dict(color=[PALETTE[index % 4] for index in range(len(labels))]),
            textinfo="value",
        )
    )
    fig.update_layout(**_layout("Where retrieval breaks down", height=420))
    return fig
