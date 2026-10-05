"""Chart selection from result shape + intent. Returns a Plotly figure as a JSON-serializable dict (or None)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from analytics.analysis import _PERIOD_NAMES, _wide_pair, classify_columns

PALETTE = ["#2F6FDE", "#E8833A", "#2BA58B", "#9B5DE5", "#D1495B", "#8D99AE"]
MAX_BARS = 25


def _pretty(name: str) -> str:
    return name.replace("_", " ").title()


def _spec(fig: go.Figure) -> dict[str, Any]:
    fig.update_layout(template="plotly_white", colorway=PALETTE, margin=dict(l=50, r=20, t=50, b=50), legend_title_text="")
    return json.loads(fig.to_json())


def _dims(roles: dict[str, list[str]]) -> list[str]:
    return [c for c in roles["category"] if not c.endswith("_id")]


def choose_chart(df: pd.DataFrame, intent: str | None = None) -> str | None:
    """Return the chart kind that fits the data: line, grouped_bar, bar, hbar, or None."""
    if df.empty or (len(df) == 1):
        return None
    roles = classify_columns(df)
    time_cols, numeric, dims = roles["time"], roles["numeric"], _dims(roles)
    if not numeric:
        return None
    if _wide_pair(numeric) and dims:
        return "grouped_bar"
    if any(c.lower() in _PERIOD_NAMES and df[c].nunique() == 2 for c in time_cols) and dims:
        return "grouped_bar"
    if time_cols and df[time_cols[0]].nunique() >= 3:
        return "line"
    if time_cols and not dims:
        return "bar"
    if dims:
        return "hbar" if df[dims[0]].nunique() > 6 or intent == "ranking" else "bar"
    return None


def build_chart(df: pd.DataFrame, intent: str | None = None) -> dict[str, Any] | None:
    kind = choose_chart(df, intent)
    if kind is None:
        return None
    roles = classify_columns(df)
    time_cols, numeric, dims = roles["time"], roles["numeric"], _dims(roles)
    value = numeric[0]

    if kind == "line":
        tc = time_cols[0]
        data = df.sort_values(tc)
        if dims:
            fig = go.Figure([go.Scatter(x=g[tc], y=g[value], mode="lines+markers", name=str(k))
                             for k, g in data.groupby(dims[0])])
        else:
            fig = go.Figure(go.Scatter(x=data[tc], y=data[value], mode="lines+markers", name=_pretty(value)))
        fig.update_layout(title=f"{_pretty(value)} by {_pretty(tc)}", xaxis_title=_pretty(tc), yaxis_title=_pretty(value))
        return _spec(fig)

    if kind == "grouped_bar":
        dim = dims[0]
        pair = _wide_pair(numeric)
        if pair:
            fig = go.Figure([go.Bar(name=_pretty(c), x=df[dim], y=df[c]) for c in (pair[1], pair[0])])
        else:
            pc = next(c for c in time_cols if c.lower() in _PERIOD_NAMES and df[c].nunique() == 2)
            fig = go.Figure([go.Bar(name=str(k), x=g[dim], y=g[value]) for k, g in df.sort_values(pc).groupby(pc)])
        fig.update_layout(barmode="group", title=f"{_pretty(value)} by {_pretty(dim)}", yaxis_title=_pretty(value))
        return _spec(fig)

    cat = dims[0] if dims else time_cols[0]
    if kind == "hbar":
        data = df.sort_values(value, ascending=False).head(MAX_BARS).iloc[::-1]
        fig = go.Figure(go.Bar(x=data[value], y=data[cat], orientation="h"))
    else:
        data = df.head(MAX_BARS)
        fig = go.Figure(go.Bar(x=data[cat], y=data[value]))
    fig.update_layout(title=f"{_pretty(value)} by {_pretty(cat)}")
    return _spec(fig)
