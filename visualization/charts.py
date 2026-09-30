"""Chart spec from the result shape (Phase 4 placeholder; Phase 5 adds intent-aware choice).

Specs are built by our code from the data, never from LLM output (Security model §7).
"""

from __future__ import annotations

import json
from typing import Any, Literal

import plotly.graph_objects as go

from analytics.analysis import label_columns, numeric_columns, to_frame

ChartType = Literal["bar", "line", "pie", "scatter", "table", "none"]
CHART_TYPES: frozenset[str] = frozenset(ChartType.__args__)  # type: ignore[attr-defined]
MAX_BARS = 30


def coerce_chart_type(value: Any) -> str:
    """Anything outside the enum falls back to 'table'."""
    return value if isinstance(value, str) and value in CHART_TYPES else "table"


def choose(result: dict[str, Any], intent: str | None) -> str:
    df = to_frame(result)
    if df.empty or len(df) == 1:
        return "none"
    nums, labels = numeric_columns(df), label_columns(df)
    if not nums or len(labels) != 1:
        return "table"
    x = labels[0]
    if intent == "trend" or any(k in x.lower() for k in ("date", "month", "quarter", "week", "year")):
        return "line"
    return "bar" if len(df) <= MAX_BARS else "table"


def build(result: dict[str, Any], intent: str | None) -> dict[str, Any] | None:
    chart = choose(result, intent)
    if chart in ("none", "table"):
        return None
    df = to_frame(result)
    x = label_columns(df)[0]
    fig = go.Figure()
    for col in numeric_columns(df)[:3]:
        trace = go.Scatter if chart == "line" else go.Bar
        fig.add_trace(trace(x=df[x].astype(str).tolist(), y=df[col].tolist(), name=col))
    fig.update_layout(xaxis_title=x, template="plotly_white", barmode="group")
    return {"type": chart, "figure": json.loads(fig.to_json())}
