"""Plotly chart specs chosen from result shape + intent. Returns a JSON-serialisable dict."""

from __future__ import annotations

import json
import re

import pandas as pd
import plotly.express as px
import plotly.io as pio

from analytics.analysis import _split_columns

_PREV = re.compile(r"prev|prior|before|last", re.I)
_CUR = re.compile(r"curr|current|latest|this", re.I)
MAX_BARS = 25


def _to_json(fig) -> dict:
    fig.update_layout(template="plotly_white", margin=dict(l=40, r=20, t=60, b=40),
                      legend_title_text="")
    return json.loads(pio.to_json(fig))


def _pretty(c: str) -> str:
    return c.replace("_", " ").title()


def build_chart(frame: dict | None, intent: str = "other", title: str | None = None) -> dict | None:
    if not frame or not frame["records"]:
        return None
    df = pd.DataFrame.from_records(frame["records"], columns=frame["columns"])
    numeric, labels, time_col = _split_columns(df)
    if not numeric or len(df) < 2:
        return None  # single value: no chart

    labels_map = {c: _pretty(c) for c in df.columns}
    title = title or None
    # time series
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.sort_values(time_col)
        if labels and df[labels[0]].nunique() <= 8:
            fig = px.line(df, x=time_col, y=numeric[0], color=labels[0], markers=True,
                          title=title, labels=labels_map)
        else:
            fig = px.line(df, x=time_col, y=numeric[0], markers=True, title=title, labels=labels_map)
        return _to_json(fig)

    if not labels:
        return None
    lab = labels[0]
    prev_c = next((c for c in numeric if _PREV.search(c)), None)
    cur_c = next((c for c in numeric if _CUR.search(c)), None)
    delta_c = next((c for c in numeric if re.search(r"change|delta|diff", c, re.I)), None)
    d = df.head(MAX_BARS)
    if prev_c and cur_c:
        melted = d.melt(id_vars=[lab], value_vars=[prev_c, cur_c], var_name="period", value_name="value")
        fig = px.bar(melted, x=lab, y="value", color="period", barmode="group", title=title,
                     labels={"value": "Value", lab: labels_map[lab]})
    elif delta_c and intent in ("diagnostic", "comparison"):
        d = d.sort_values(delta_c)
        fig = px.bar(d, x=delta_c, y=lab, orientation="h", title=title, labels=labels_map)
    elif len(labels) > 1 and d[labels[1]].nunique() <= 8:
        fig = px.bar(d, x=lab, y=numeric[0], color=labels[1], barmode="group", title=title,
                     labels=labels_map)
    else:
        horizontal = len(d) > 6 or d[lab].astype(str).str.len().max() > 12
        d = d.sort_values(numeric[0], ascending=horizontal)
        fig = (px.bar(d, x=numeric[0], y=lab, orientation="h", title=title, labels=labels_map)
               if horizontal else px.bar(d, x=lab, y=numeric[0], title=title, labels=labels_map))
    return _to_json(fig)
